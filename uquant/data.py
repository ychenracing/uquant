"""Point-in-time data loading, validation and hashing of immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")
LEGACY_ADJUSTMENT = "QFQ stocks; raw indices"
RAW_ADJUSTMENT = "RAW stocks with causal preclose adjustment; raw indices"


class DataContractError(RuntimeError):
    """Raised when market data cannot satisfy the point-in-time contract."""


def normalize_symbol(symbol: str) -> str:
    """Normalize a six-digit or exchange-prefixed A-share symbol."""
    value = symbol.strip().lower().replace(".", "")
    if value.startswith(("sh", "sz", "bj")):
        normalized = value
    else:
        digits = "".join(ch for ch in value if ch.isascii() and ch.isdigit())
        if len(digits) != 6:
            raise ValueError(f"invalid A-share symbol: {symbol}")
        if digits in {"000300", "000682"} or digits.startswith(("6", "9")):
            normalized = "sh" + digits
        elif digits.startswith(("4", "8")):
            normalized = "bj" + digits
        else:
            normalized = "sz" + digits
    if re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", normalized) is None:
        raise ValueError(f"invalid A-share symbol: {symbol}")
    return normalized


@dataclass(frozen=True, slots=True)
class DataManifest:
    """Bounded, reproducible identity of the files used for one decision."""

    generated_at: str
    source: str
    adjustment: str
    files: dict[str, str]
    symbols: tuple[str, ...]
    start: str
    end: str
    digest: str

    def to_dict(self) -> dict[str, object]:
        """Return the manifest in JSON-compatible form."""

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


def _repair_legacy_lot_volume(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert legacy rows whose volume was stored in 100-share lots.

    Legacy frozen files carry no unit column. A row is converted only when
    its own turnover proves the unit: amount / close exceeds 50x the volume.
    Rows without a turnover are never guessed. Typed snapshots declare
    ``volume_unit`` and bypass this repair.
    """

    implied = frame["amount"] / frame["close"]
    lots = (frame["volume"] > 0) & np.isfinite(implied) & (implied > frame["volume"] * 50)
    out = frame.copy()
    out.loc[lots, "volume"] = out.loc[lots, "volume"] * 100.0
    return out


def ex_reference_price(previous_close: float, *, cash_per_share: float, share_ratio: float) -> float:
    """Exchange ex-rights reference price for a cash and bonus/transfer distribution."""
    return (previous_close - cash_per_share) / (1.0 + share_ratio)


def causal_adjustment_factor(
    close: pd.Series,
    preclose: pd.Series,
    events: list[dict[str, Any]] | None = None,
) -> pd.Series:
    """Cumulative back-adjustment factor from ex-rights reference prices.

    On an ex-date the reference price is below the previous raw close and
    ``prev_close / reference`` is the event ratio. Recorded distributions use
    the exchange formula; other dates fall back to the vendor ``preclose``.
    The factor starts at 1 and only uses rows up to each date, so appending
    data never rewrites history.
    """

    previous = close.shift(1)
    overrides: dict[pd.Timestamp, float] = {}
    for event in events or []:
        date = pd.Timestamp(event["ex_date"])
        if date in preclose.index and np.isfinite(previous.get(date, np.nan)):
            overrides[date] = ex_reference_price(
                float(previous[date]),
                cash_per_share=float(event.get("cash_per_share", 0.0)),
                share_ratio=float(event.get("share_ratio", 0.0)),
            )
    reference = preclose.astype(float).copy()
    reference.update(pd.Series(overrides, dtype=float))
    ratio = (previous / reference).where(reference > 0)
    ratio = ratio.where(np.isfinite(ratio) & ((ratio - 1.0).abs() > 1e-9), 1.0)
    return ratio.cumprod()


class DataStore:
    """Load, validate, bound and hash one immutable daily OHLCV snapshot.

    A snapshot directory is never refreshed in place: a data update publishes
    a new directory, and callers switch to it with a new ``DataStore``.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise DataContractError(f"data directory does not exist: {self.root}")
        pointer = self.root / "LATEST"
        if pointer.is_file() and not any(self.root.glob("*.csv")):
            self.root = self.root / pointer.read_text(encoding="utf-8").strip()
            if not self.root.is_dir():
                raise DataContractError(f"LATEST points to a missing snapshot: {self.root}")
        self._cache: dict[str, pd.DataFrame] = {}
        self._prefix_hash_cache: dict[str, tuple[pd.DatetimeIndex, tuple[str, ...]]] = {}
        manifest_path = self.root / "DATA_MANIFEST.json"
        self.snapshot_manifest: dict[str, Any] = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        )
        self.adjustment = (
            RAW_ADJUSTMENT if self.snapshot_manifest.get("price_basis") == "raw" else LEGACY_ADJUSTMENT
        )
        actions_path = self.root / "CORPORATE_ACTIONS.json"
        self._corporate_actions: list[dict[str, Any]] = (
            json.loads(actions_path.read_text(encoding="utf-8")) if actions_path.is_file() else []
        )

    def corporate_actions(self, symbol: str) -> list[dict[str, Any]]:
        """Return the snapshot's recorded dividend/bonus events for one symbol."""

        normalized = normalize_symbol(symbol)
        return [dict(item) for item in self._corporate_actions if item["symbol"] == normalized]

    def path_for(self, symbol: str) -> Path:
        """Resolve a normalized symbol to an existing CSV path."""

        normalized = normalize_symbol(symbol)
        candidates = [self.root / f"{normalized}.csv", self.root / f"{normalized[2:]}.csv"]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise DataContractError(f"missing required data for {normalized}")

    def load(self, symbol: str, *, as_of: str | None = None) -> pd.DataFrame:
        """Load validated data, optionally bounded through an inclusive date."""

        normalized = normalize_symbol(symbol)
        if normalized not in self._cache:
            path = self.path_for(normalized)
            frame = pd.read_csv(path)
            self._cache[normalized] = self._validate(frame, normalized, self.corporate_actions(normalized))
        frame = self._cache[normalized]
        if as_of is None:
            return frame.copy()
        bounded = frame.loc[: pd.Timestamp(as_of)].copy()
        if bounded.empty:
            raise DataContractError(f"{normalized} has no data on or before {as_of}")
        return bounded

    @staticmethod
    def _validate(
        frame: pd.DataFrame, symbol: str, events: list[dict[str, Any]] | None = None
    ) -> pd.DataFrame:
        missing = set(REQUIRED_COLUMNS) - set(frame.columns)
        if missing:
            raise DataContractError(f"{symbol} missing columns: {sorted(missing)}")
        out = frame.copy()
        out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
        if out["date"].isna().any() or out["date"].duplicated().any() or not out["date"].is_monotonic_increasing:
            raise DataContractError(f"{symbol} dates must be unique and increasing")
        for column in REQUIRED_COLUMNS[1:]:
            out[column] = pd.to_numeric(out[column], errors="coerce")
        invalid = (
            ~np.isfinite(out[["open", "high", "low", "close", "volume"]]).all(axis=1)
            | (out[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (out["high"] < out[["open", "close", "low"]].max(axis=1))
            | (out["low"] > out[["open", "close", "high"]].min(axis=1))
            | (out["volume"] < 0)
        )
        if invalid.any():
            raise DataContractError(f"{symbol} contains {int(invalid.sum())} invalid OHLCV rows")
        # A missing turnover stays missing: price x volume is not a traded amount.
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce") if "amount" in out else np.nan
        if (np.isinf(out["amount"]) | (out["amount"] < 0)).any():
            raise DataContractError(f"{symbol} contains invalid turnover amounts")
        if "volume_unit" not in out:
            out = _repair_legacy_lot_volume(out)
        out = out.set_index("date", drop=True)
        if "preclose" in out:
            out["preclose"] = pd.to_numeric(out["preclose"], errors="coerce")
            out["adj_close"] = out["close"] * causal_adjustment_factor(out["close"], out["preclose"], events)
        return out

    def common_sessions(self, symbols: Iterable[str], start: str, end: str) -> pd.DatetimeIndex:
        """Return at least two sessions shared by every requested symbol."""

        sessions: pd.DatetimeIndex | None = None
        for symbol in symbols:
            index = pd.DatetimeIndex(self.load(symbol).loc[pd.Timestamp(start) : pd.Timestamp(end)].index)
            sessions = index if sessions is None else sessions.intersection(index)
        if sessions is None or len(sessions) < 2:
            raise DataContractError("at least two common sessions are required")
        return sessions

    def _prefix_hash(self, symbol: str, *, as_of: pd.Timestamp | None) -> str:
        normalized = normalize_symbol(symbol)
        if normalized not in self._prefix_hash_cache:
            frame = self.load(normalized)
            canonical = frame.to_csv(
                index=True,
                date_format="%Y-%m-%d",
                float_format="%.12g",
                na_rep="",
                lineterminator="\n",
            ).splitlines(keepends=True)
            if len(canonical) != len(frame) + 1:
                raise DataContractError(f"cannot construct canonical prefix hash for {normalized}")
            chain = hashlib.sha256(canonical[0].encode("utf-8")).digest()
            prefix_digests: list[str] = []
            for row in canonical[1:]:
                chain = hashlib.sha256(chain + row.encode("utf-8")).digest()
                prefix_digests.append(chain.hex())
            self._prefix_hash_cache[normalized] = (
                pd.DatetimeIndex(frame.index.copy()),
                tuple(prefix_digests),
            )
        index, cached_digests = self._prefix_hash_cache[normalized]
        position = len(index) - 1 if as_of is None else int(index.searchsorted(as_of, side="right")) - 1
        if position < 0:
            raise DataContractError(
                f"{normalized} has no data on or before {as_of.date() if as_of else as_of}"
            )
        return cached_digests[position]

    def manifest(
        self,
        symbols: Iterable[str],
        *,
        source: str = "frozen",
        as_of: str | pd.Timestamp | None = None,
    ) -> DataManifest:
        """Build a deterministic identity for the visible prefix of each symbol."""

        normalized = tuple(sorted({normalize_symbol(item) for item in symbols}))
        bound = pd.Timestamp(as_of).normalize() if as_of is not None else None
        files: dict[str, str] = {}
        starts: list[str] = []
        ends: list[str] = []
        for symbol in normalized:
            path = self.path_for(symbol)
            frame = self.load(symbol)
            bounded = frame if bound is None else frame.loc[:bound]
            if bounded.empty:
                boundary = bound.date() if bound is not None else "unbounded"
                raise DataContractError(f"{symbol} has no data on or before {boundary}")
            files[path.name] = self._prefix_hash(symbol, as_of=bound)
            starts.append(str(bounded.index.min().date()))
            ends.append(str(bounded.index.max().date()))
        payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return DataManifest(
            generated_at=datetime.now(UTC).isoformat(),
            source=source,
            adjustment=self.adjustment,
            files=files,
            symbols=normalized,
            start=max(starts),
            end=min(ends),
            digest=hashlib.sha256(payload).hexdigest(),
        )
