"""Point-in-time data loading, validation, hashing, and optional online refresh."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class DataContractError(RuntimeError):
    pass


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().lower().replace(".", "")
    if value.startswith(("sh", "sz", "bj")):
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"invalid A-share symbol: {symbol}")
    if digits in {"000300", "000682"}:
        return "sh" + digits
    if digits.startswith(("6", "9")):
        return "sh" + digits
    if digits.startswith(("4", "8")):
        return "bj" + digits
    return "sz" + digits


@dataclass(frozen=True, slots=True)
class DataManifest:
    generated_at: str
    source: str
    adjustment: str
    files: dict[str, str]
    symbols: tuple[str, ...]
    start: str
    end: str
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "adjustment": self.adjustment,
            "files": self.files,
            "symbols": list(self.symbols),
            "start": self.start,
            "end": self.end,
            "digest": self.digest,
        }


class DataStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise DataContractError(f"data directory does not exist: {self.root}")
        self._cache: dict[str, pd.DataFrame] = {}

    def path_for(self, symbol: str) -> Path:
        normalized = normalize_symbol(symbol)
        candidates = [self.root / f"{normalized}.csv", self.root / f"{normalized[2:]}.csv"]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise DataContractError(f"missing required data for {normalized}")

    def load(self, symbol: str, *, as_of: str | None = None) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        if normalized not in self._cache:
            path = self.path_for(normalized)
            frame = pd.read_csv(path)
            self._cache[normalized] = self._validate(frame, normalized)
        frame = self._cache[normalized]
        if as_of is None:
            return frame.copy()
        bounded = frame.loc[: pd.Timestamp(as_of)].copy()
        if bounded.empty:
            raise DataContractError(f"{normalized} has no data on or before {as_of}")
        return bounded

    @staticmethod
    def _validate(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        missing = set(REQUIRED_COLUMNS) - set(frame.columns)
        if missing:
            raise DataContractError(f"{symbol} missing columns: {sorted(missing)}")
        out = frame.copy()
        out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
        if out["date"].duplicated().any() or not out["date"].is_monotonic_increasing:
            raise DataContractError(f"{symbol} dates must be unique and increasing")
        for column in REQUIRED_COLUMNS[1:]:
            out[column] = pd.to_numeric(out[column], errors="coerce")
        invalid = (
            out[["open", "high", "low", "close"]].isna().any(axis=1)
            | (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (out["high"] < out[["open", "close", "low"]].max(axis=1))
            | (out["low"] > out[["open", "close", "high"]].min(axis=1))
            | out["volume"].isna()
            | (out["volume"] < 0)
        )
        if invalid.any():
            raise DataContractError(f"{symbol} contains {int(invalid.sum())} invalid OHLCV rows")
        if "amount" not in out:
            out["amount"] = out["close"] * out["volume"]
        else:
            out["amount"] = pd.to_numeric(out["amount"], errors="coerce")
            out["amount"] = out["amount"].fillna(out["close"] * out["volume"])
        return out.set_index("date", drop=True)

    def common_sessions(self, symbols: Iterable[str], start: str, end: str) -> pd.DatetimeIndex:
        sessions: pd.DatetimeIndex | None = None
        for symbol in symbols:
            index = self.load(symbol).loc[pd.Timestamp(start) : pd.Timestamp(end)].index
            sessions = index if sessions is None else sessions.intersection(index)
        if sessions is None or len(sessions) < 2:
            raise DataContractError("at least two common sessions are required")
        return sessions

    def manifest(self, symbols: Iterable[str], *, source: str = "frozen") -> DataManifest:
        normalized = tuple(sorted({normalize_symbol(item) for item in symbols}))
        files: dict[str, str] = {}
        starts: list[str] = []
        ends: list[str] = []
        for symbol in normalized:
            path = self.path_for(symbol)
            files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            frame = self.load(symbol)
            starts.append(str(frame.index.min().date()))
            ends.append(str(frame.index.max().date()))
        payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return DataManifest(
            generated_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            adjustment="QFQ stocks; raw indices",
            files=files,
            symbols=normalized,
            start=max(starts),
            end=min(ends),
            digest=hashlib.sha256(payload).hexdigest(),
        )

    def refresh_akshare(self, symbols: Iterable[str], *, end: str) -> None:
        try:
            import akshare as ak  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install unified-ai-quant[data] for online refresh") from exc
        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            if symbol in {"sh000300", "sh000682"}:
                raise RuntimeError("index refresh requires a raw-index adapter; refusing QFQ fallback")
            raw = ak.stock_zh_a_hist(
                symbol=symbol[2:],
                period="daily",
                start_date="20000101",
                end_date=end.replace("-", ""),
                adjust="qfq",
            )
            mapping = {
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            }
            frame = raw.rename(columns=mapping)[list(mapping.values())]
            validated = self._validate(frame, symbol).reset_index()
            validated.to_csv(self.root / f"{symbol}.csv", index=False)
            self._cache.pop(symbol, None)
