#!/usr/bin/env python3
"""Causally backfill the frozen Tencent history without touching later rows.

The existing snapshot was sourced from Tencent Finance but intentionally began
in 2022.  This utility downloads bounded, non-overlapping 2014-2021 chunks,
validates each symbol against the first already-frozen row, and prepends only
dates earlier than that row.  Existing 2022+ bytes are preserved verbatim.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
CHUNKS = (
    ("2014-01-01", "2015-12-31"),
    ("2016-01-01", "2017-12-31"),
    ("2018-01-01", "2019-12-31"),
    ("2020-01-01", "2021-12-31"),
)
HISTORICAL_CUTOFF = "2021-12-31"
INDEX_SYMBOLS = {"sh000300", "sh000682", "sz399006"}
TECH_INDEX = "sh000682"
TECH_PROXY = "sz399006"
TECH_TARGET_FIRST_DATE = "2021-08-16"
HEADER = "date,open,high,low,close,volume,amount\n"


@dataclass(frozen=True, slots=True)
class BackfillResult:
    symbol: str
    first_date: str
    last_date: str
    historical_rows_added: int
    total_rows: int
    anchor_max_relative_difference: float
    sha256: str
    pre_inception_proxy: str | None = None


def _manifest_result(payload: dict[str, Any]) -> BackfillResult:
    """Read the stable backfill fields while preserving newer manifest extras."""
    return BackfillResult(
        symbol=str(payload["symbol"]),
        first_date=str(payload["first_date"]),
        last_date=str(payload["last_date"]),
        historical_rows_added=int(payload["historical_rows_added"]),
        total_rows=int(payload["total_rows"]),
        anchor_max_relative_difference=float(
            payload["anchor_max_relative_difference"]
        ),
        sha256=str(payload["sha256"]),
        pre_inception_proxy=payload.get("pre_inception_proxy"),
    )


def _request_text(url: str, *, attempts: int = 4) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 uquant-history-backfill/1.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read(8_000_001)
            if len(payload) > 8_000_000:
                raise RuntimeError("Tencent response exceeded 8 MB")
            return payload.decode("utf-8", errors="strict")
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"Tencent request failed after {attempts} attempts: {url}") from error


def _payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if "(" in stripped and stripped.endswith(")"):
        stripped = stripped[stripped.index("(") + 1 : -1]
    elif "=" in stripped:
        stripped = stripped.split("=", 1)[1]
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise RuntimeError("Tencent returned a non-object payload")
    return value


def _fetch(
    symbol: str, start: str, end: str, *, count: int = 640
) -> list[tuple[str, str, str, str, str, str]]:
    adjusted = symbol not in INDEX_SYMBOLS
    query = urlencode(
        {
            "_var": "kline_dayqfq" if adjusted else "kline_day",
            "param": ",".join(
                (symbol, "day", start, end, str(count), "qfq" if adjusted else "")
            ),
        }
    )
    root = _payload(_request_text(f"{ENDPOINT}?{query}")).get("data", {}).get(symbol)
    if not isinstance(root, dict):
        return []
    source = root.get("qfqday") or root.get("day") or []
    rows: list[tuple[str, str, str, str, str, str]] = []
    for raw in source:
        if not isinstance(raw, list) or len(raw) < 6:
            raise RuntimeError(f"Tencent returned a malformed row for {symbol}: {raw!r}")
        date, open_, close, high, low, volume = map(str, raw[:6])
        if start <= date <= end:
            rows.append((date, open_, high, low, close, volume))
    return rows


def _existing_anchor(path: Path) -> tuple[str, dict[str, float], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith(HEADER):
        raise RuntimeError(f"unexpected frozen CSV header: {path}")
    reader = csv.DictReader(text.splitlines())
    first = next(reader, None)
    if first is None:
        raise RuntimeError(f"empty frozen CSV: {path}")
    prices = {name: float(first[name]) for name in ("open", "high", "low", "close")}
    return first["date"], prices, text


def _anchor_difference(
    symbol: str, anchor_date: str, existing: dict[str, float]
) -> float:
    rows = _fetch(symbol, anchor_date, anchor_date, count=30)
    if not rows:
        raise RuntimeError(f"Tencent returned no anchor row for {symbol} on {anchor_date}")
    _, open_, high, low, close, _ = rows[-1]
    fetched = dict(
        zip(
            ("open", "high", "low", "close"),
            map(float, (open_, high, low, close)),
            strict=True,
        )
    )
    differences = [
        abs(existing[name] - fetched[name]) / max(abs(existing[name]), 1e-12)
        for name in existing
    ]
    return max(differences)


def _format_volume(symbol: str, value: str) -> str:
    volume = Decimal(value)
    if symbol not in INDEX_SYMBOLS:
        volume *= 100
    if not math.isfinite(float(volume)) or volume < 0:
        raise RuntimeError(f"invalid Tencent volume for {symbol}: {value}")
    return f"{volume:.1f}"


def _valid_prices(row: tuple[str, str, str, str, str, str]) -> bool:
    _, open_, high, low, close, _ = row
    values = tuple(map(float, (open_, high, low, close)))
    if not all(math.isfinite(value) and value > 0 for value in values):
        return False
    open_value, high_value, low_value, close_value = values
    return high_value >= max(open_value, low_value, close_value) and low_value <= min(
        open_value, high_value, close_value
    )


def _backfill_one(path: Path) -> tuple[BackfillResult, bytes]:
    symbol = path.stem
    anchor_date, anchor_prices, existing_text = _existing_anchor(path)
    if anchor_date <= HISTORICAL_CUTOFF:
        raise RuntimeError(f"{symbol} already starts on or before the historical cutoff")
    rows: dict[str, tuple[str, str, str, str, str, str]] = {}
    for start, end in CHUNKS:
        for row in _fetch(symbol, start, end):
            if row[0] < anchor_date:
                rows[row[0]] = row
    # Tencent's qfq formula can cross zero for a heavily cash-distributing
    # security.  A percentage-return engine cannot consume that prefix.  Start
    # after the last non-positive adjusted bar; isolated positive-price OHLC
    # inconsistencies are omitted without discarding otherwise valid history.
    non_positive = [
        date
        for date, row in rows.items()
        if any(float(value) <= 0 for value in row[1:5])
    ]
    if non_positive:
        cutoff = max(non_positive)
        rows = {date: row for date, row in rows.items() if date > cutoff}
    rows = {date: row for date, row in rows.items() if _valid_prices(row)}
    if symbol in {"sz300308", "sz300394", "sz300502", *INDEX_SYMBOLS} and not rows:
        raise RuntimeError(f"required long-history symbol has no Tencent backfill: {symbol}")
    anchor_difference = _anchor_difference(symbol, anchor_date, anchor_prices)
    if anchor_difference > 0.001:
        raise RuntimeError(
            f"{symbol} qfq anchor changed by {anchor_difference:.4%}; refusing splice"
        )
    prefix = "".join(
        f"{date},{open_},{high},{low},{close},{_format_volume(symbol, volume)},\n"
        for date, open_, high, low, close, volume in sorted(rows.values())
    )
    payload = (HEADER + prefix + existing_text[len(HEADER) :]).encode("utf-8")
    total_rows = payload.count(b"\n") - 1
    first_date = min(rows) if rows else anchor_date
    result = BackfillResult(
        symbol=symbol,
        first_date=first_date,
        last_date=existing_text.splitlines()[-1].split(",", 1)[0],
        historical_rows_added=len(rows),
        total_rows=total_rows,
        anchor_max_relative_difference=anchor_difference,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return result, payload


def _prepend_tech_proxy(
    result: BackfillResult, payload: bytes
) -> tuple[BackfillResult, bytes]:
    """Prepend raw proxy levels and causally rebase later target levels.

    Index levels have an arbitrary base.  Rebase the target only from its first
    observable close onward so a replay before target inception reads the raw
    proxy bytes and never depends on a future target value.  The transformation
    preserves every raw within-series return and produces a continuous level.
    """
    text = payload.decode("utf-8")
    target_rows = list(csv.DictReader(text.splitlines()))
    first = target_rows[0] if target_rows else None
    if first is None:
        raise RuntimeError(f"empty {TECH_INDEX} payload")
    first_date = first["date"]
    target_close = Decimal(first["close"])
    proxy_anchor = _fetch(TECH_PROXY, first_date, first_date, count=30)
    if not proxy_anchor:
        raise RuntimeError(f"{TECH_PROXY} has no proxy anchor on {first_date}")
    proxy_close = Decimal(proxy_anchor[-1][4])
    target_scale = proxy_close / target_close
    proxy: dict[str, tuple[str, str, str, str, str, str]] = {}
    for start, end in CHUNKS:
        for row in _fetch(TECH_PROXY, start, end):
            if row[0] < first_date and _valid_prices(row):
                proxy[row[0]] = row
    if not proxy or min(proxy) > "2014-01-10":
        raise RuntimeError(f"{TECH_PROXY} did not provide the required pre-inception history")
    prefix = "".join(
        (
            f"{date},{Decimal(open_):.6f},{Decimal(high):.6f},"
            f"{Decimal(low):.6f},{Decimal(close):.6f},"
            f"{_format_volume(TECH_PROXY, volume)},\n"
        )
        for date, open_, high, low, close, volume in sorted(proxy.values())
    )
    rebased_target = "".join(
        (
            f"{row['date']},{Decimal(row['open']) * target_scale:.6f},"
            f"{Decimal(row['high']) * target_scale:.6f},"
            f"{Decimal(row['low']) * target_scale:.6f},"
            f"{Decimal(row['close']) * target_scale:.6f},"
            f"{row['volume']},{row.get('amount', '')}\n"
        )
        for row in target_rows
    )
    extended = (HEADER + prefix + rebased_target).encode("utf-8")
    updated = BackfillResult(
        symbol=result.symbol,
        first_date=min(proxy),
        last_date=result.last_date,
        historical_rows_added=result.historical_rows_added + len(proxy),
        total_rows=extended.count(b"\n") - 1,
        anchor_max_relative_difference=result.anchor_max_relative_difference,
        sha256=hashlib.sha256(extended).hexdigest(),
        pre_inception_proxy=(
            f"{TECH_PROXY} raw before {first_date}; {TECH_INDEX} causally "
            f"rebased from {first_date} by {target_scale}"
        ),
    )
    return updated, extended


def _write_metadata(data_dir: Path, results: list[BackfillResult]) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "snapshot_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-historical-backfill"),
        "generated_at_utc": generated_at,
        "adjustment": (
            "Tencent qfq for stocks; Tencent raw index returns represented in "
            "causally chain-linked levels"
        ),
        "historical_policy": "2014-2021 bounded chunks; 2022+ frozen rows preserved verbatim",
        "providers": ["Tencent Finance"],
        "endpoint": ENDPOINT,
        "pre_inception_index_proxy": {
            "target": TECH_INDEX,
            "proxy": TECH_PROXY,
            "rule": (
                "raw proxy levels before target inception; target OHLC rebased "
                "forward using only the common first-session close"
            ),
        },
        "results": [asdict(item) for item in results],
    }
    (data_dir / "DATA_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sums = "".join(f"{item.sha256}  {item.symbol}.csv\n" for item in results)
    (data_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data/frozen")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--tech-proxy-only", action="store_true")
    args = parser.parse_args(argv)
    paths = sorted(args.data_dir.glob("*.csv"))
    if not paths:
        parser.error(f"no CSV files found in {args.data_dir}")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.tech_proxy_only:
        manifest_path = args.data_dir / "DATA_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        results = [_manifest_result(item) for item in manifest["results"]]
        current = next(item for item in results if item.symbol == TECH_INDEX)
        if current.pre_inception_proxy is not None:
            parser.error("tech proxy has already been applied")
        path = args.data_dir / f"{TECH_INDEX}.csv"
        updated, payload = _prepend_tech_proxy(current, path.read_bytes())
        path.write_bytes(payload)
        results = [updated if item.symbol == TECH_INDEX else item for item in results]
        _write_metadata(args.data_dir, sorted(results, key=lambda item: item.symbol))
        print(f"history backfill: added {updated.historical_rows_added} tech rows", flush=True)
        return 0
    print(f"history backfill: downloading {len(paths)} symbols", flush=True)
    with ThreadPoolExecutor(max_workers=min(args.workers, len(paths))) as executor:
        staged = list(executor.map(_backfill_one, paths))
    staged = [
        _prepend_tech_proxy(result, payload)
        if result.symbol == TECH_INDEX
        else (result, payload)
        for result, payload in staged
    ]
    results = sorted((item[0] for item in staged), key=lambda item: item.symbol)
    with tempfile.TemporaryDirectory(prefix=".history-backfill-", dir=args.data_dir.parent) as tmp:
        stage_dir = Path(tmp)
        for result, payload in staged:
            (stage_dir / f"{result.symbol}.csv").write_bytes(payload)
        for result in results:
            source = stage_dir / f"{result.symbol}.csv"
            os.replace(source, args.data_dir / source.name)
    _write_metadata(args.data_dir, results)
    print(
        f"history backfill: added {sum(item.historical_rows_added for item in results)} rows",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
