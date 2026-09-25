"""Build and atomically publish an immutable raw-price market-data snapshot.

Stocks use raw OHLC with the exchange ex-rights ``preclose``, volume in
shares and turnover in yuan. Cash/share distributions are stored as
corporate-action facts. Index files extend the base snapshot's causally
chain-linked levels with raw index returns. Nothing is written into an
existing snapshot: a complete staging directory passes ``check_snapshot``
and is then renamed into place.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .data import normalize_symbol
from .data_check import check_snapshot
from .infrastructure.atomic_files import atomic_write_text

STOCK_COLUMNS = ("date", "open", "high", "low", "close", "preclose", "volume", "amount", "volume_unit",
                 "special_treatment")
INDEX_COLUMNS = ("date", "open", "high", "low", "close", "volume")
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{start},{end},{count},"


class MarketDataProvider(Protocol):
    name: str

    def stock_daily(self, symbol: str, start: str, end: str) -> tuple[pd.DataFrame, list[str]]: ...

    def dividends(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]: ...

    def index_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame: ...


def _baostock_code(symbol: str) -> str:
    return f"{symbol[:2]}.{symbol[2:]}"


class BaostockProvider:
    """Raw daily bars and dividend facts from baostock (``pip install baostock``)."""

    name = "baostock"

    def __init__(self) -> None:
        try:
            self._bs: Any = importlib.import_module("baostock")
        except ImportError as exc:
            raise RuntimeError("data-update requires the optional baostock package") from exc
        if self._bs.login().error_code != "0":
            raise RuntimeError("baostock login failed")

    def _rows(self, result: Any) -> list[list[str]]:
        if result.error_code != "0":
            raise RuntimeError(f"baostock error {result.error_code}: {result.error_msg}")
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        return rows

    def stock_daily(self, symbol: str, start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
        fields = "date,open,high,low,close,preclose,volume,amount,tradestatus,isST"
        rows = self._rows(self._bs.query_history_k_data_plus(
            _baostock_code(symbol), fields, start_date=start, end_date=end, frequency="d", adjustflag="3"))
        frame = pd.DataFrame(rows, columns=fields.split(","))
        suspended = frame.loc[frame["tradestatus"] != "1", "date"].tolist()
        frame = frame[frame["tradestatus"] == "1"].copy()
        for column in ("open", "high", "low", "close", "preclose", "volume", "amount"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frame["volume_unit"] = "shares"
        frame["special_treatment"] = frame["isST"].eq("1").astype(int)
        return frame[list(STOCK_COLUMNS)].reset_index(drop=True), suspended

    def dividends(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        events = []
        for year in range(int(start[:4]), int(end[:4]) + 1):
            result = self._bs.query_dividend_data(code=_baostock_code(symbol), year=str(year), yearType="operate")
            for row in self._rows(result):
                item = dict(zip(result.fields, row, strict=True))
                ex_date = item["dividOperateDate"]
                if not ex_date or not start <= ex_date <= end:
                    continue
                share_ratio = float(item["dividStocksPs"] or 0) + float(item["dividReserveToStockPs"] or 0)
                events.append({
                    "event_id": f"{symbol}:{ex_date}:distribution",
                    "symbol": symbol,
                    "announce_date": item["dividPlanAnnounceDate"],
                    "register_date": item["dividRegistDate"],
                    "ex_date": ex_date,
                    "pay_date": item["dividPayDate"] or ex_date,
                    "cash_per_share": float(item["dividCashPsBeforeTax"] or 0),
                    "share_ratio": share_ratio,
                    "description": item["dividCashStock"],
                    "source": self.name,
                })
        return events

    def index_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        if symbol == "sh000682":
            return tencent_index_daily(symbol, start, end)
        fields = "date,open,high,low,close,volume"
        rows = self._rows(self._bs.query_history_k_data_plus(
            _baostock_code(symbol), fields, start_date=start, end_date=end, frequency="d"))
        frame = pd.DataFrame(rows, columns=fields.split(","))
        for column in INDEX_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        return frame


def tencent_index_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Raw index levels from the Tencent kline endpoint (rows: date, open, close, high, low, volume)."""
    url = TENCENT_KLINE.format(code=symbol, start=start, end=end, count=2000)
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload["data"][symbol]["day"]
    frame = pd.DataFrame([row[:6] for row in rows], columns=["date", "open", "close", "high", "low", "volume"])
    for column in INDEX_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame[list(INDEX_COLUMNS)]


def _chain_index(base: pd.DataFrame, extension: pd.DataFrame) -> pd.DataFrame:
    """Append raw index returns to base levels, rebased on the last common close."""
    base = base[list(INDEX_COLUMNS)].copy()
    anchor = str(base["date"].iloc[-1])
    common = extension[extension["date"] == anchor]
    if common.empty:
        raise RuntimeError(f"index extension does not overlap base snapshot on {anchor}")
    factor = float(base["close"].iloc[-1]) / float(common["close"].iloc[0])
    tail = extension[extension["date"] > anchor].copy()
    for column in ("open", "high", "low", "close"):
        tail[column] = tail[column] * factor
    return pd.concat([base, tail], ignore_index=True)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def update_snapshot(
    *,
    output_root: str | Path,
    base_dir: str | Path,
    symbols: Iterable[str],
    start: str,
    end: str,
    provider: MarketDataProvider | None = None,
) -> Path:
    """Build a complete raw snapshot and publish it as ``output_root/<snapshot_id>``."""

    source = provider or BaostockProvider()
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    stocks = sorted({normalize_symbol(item) for item in symbols} - {"sh000300", "sh000682"})
    staging = root / f".staging-{os.getpid()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
    staging.mkdir()
    try:
        suspended: dict[str, list[str]] = {}
        actions: list[dict[str, Any]] = []
        for symbol in stocks:
            frame, halted = source.stock_daily(symbol, start, end)
            if frame.empty:
                raise RuntimeError(f"no raw bars for {symbol}")
            frame.to_csv(staging / f"{symbol}.csv", index=False)
            suspended[symbol] = halted
            actions.extend(source.dividends(symbol, start, end))
        for index in ("sh000300", "sh000682"):
            base = pd.read_csv(Path(base_dir) / f"{index}.csv", dtype={"date": str})
            base = base[base["date"] <= end]
            extension = source.index_daily(index, str(base["date"].iloc[-1]), end)
            _chain_index(base, extension).to_csv(staging / f"{index}.csv", index=False)
        (staging / "CORPORATE_ACTIONS.json").write_text(
            json.dumps(sorted(actions, key=lambda item: item["event_id"]), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        files = {path.name: _file_sha256(path) for path in sorted(staging.iterdir())}
        content = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        snapshot_id = f"raw-{end}-{content[:12]}"
        manifest = {
            "snapshot_id": snapshot_id,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "price_basis": "raw",
            "adjustment": "raw stock OHLC with exchange preclose; volume in shares; amount in yuan; "
            "indices chain-linked from the base snapshot with raw index returns",
            "providers": {"stocks": source.name, "sh000300": source.name, "sh000682": "tencent"},
            "base_snapshot": str(base_dir),
            "start": start,
            "end": end,
            "symbols": stocks,
            "suspended_dates": suspended,
            "files": files,
        }
        (staging / "DATA_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        report = check_snapshot(staging, as_of=end)
        if not report["ok"]:
            errors = [item for item in report["issues"] if item["severity"] == "error"]
            raise RuntimeError(f"snapshot failed data-check; not published: {errors[:5]}")
        target = root / snapshot_id
        if target.exists():
            shutil.rmtree(staging)
            return target
        os.replace(staging, target)
        atomic_write_text(root / "LATEST", snapshot_id + "\n")
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
