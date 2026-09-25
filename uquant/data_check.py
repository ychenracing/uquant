"""Content checks for one market-data snapshot, with stocks and indices kept apart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import DataStore, normalize_symbol

INDEX_SYMBOLS = frozenset({"sh000300", "sh000682"})
CALENDAR_SYMBOL = "sh000300"
VWAP_TOLERANCE = 0.03


def _issue(symbol: str, check: str, severity: str, rows: pd.Index | list[Any], detail: str = "") -> dict[str, Any]:
    dates = [str(pd.Timestamp(item).date()) for item in list(rows)]
    return {
        "symbol": symbol,
        "check": check,
        "severity": severity,
        "count": len(dates),
        "sample": dates[:5],
        **({"detail": detail} if detail else {}),
    }


def _stock_issues(store: DataStore, symbol: str, frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    suspended = set(store.snapshot_manifest.get("suspended_dates", {}).get(symbol, []))
    expected = calendar[(calendar >= frame.index.min()) & (calendar <= frame.index.max())]
    missing = expected.difference(frame.index)
    unexplained = [date for date in missing if str(date.date()) not in suspended]
    if unexplained:
        issues.append(_issue(symbol, "calendar_gap", "warning", unexplained, "missing sessions without a suspension fact"))
    amount = frame["amount"]
    if amount.isna().any():
        issues.append(_issue(symbol, "missing_amount", "warning", frame.index[amount.isna()]))
    zero = (amount == 0) & (frame["volume"] > 0)
    if zero.any():
        issues.append(_issue(symbol, "zero_amount_positive_volume", "error", frame.index[zero], "suspected missing turnover"))
    if "volume_unit" in frame:
        traded = (frame["volume"] > 0) & (amount > 0)
        vwap = amount / frame["volume"]
        outside = traded & (
            (vwap < frame["low"] * (1 - VWAP_TOLERANCE)) | (vwap > frame["high"] * (1 + VWAP_TOLERANCE))
        )
        if outside.any():
            issues.append(_issue(symbol, "vwap_outside_range", "error", frame.index[outside], "unit mismatch"))
    if "preclose" in frame:
        events = {item["ex_date"] for item in store.corporate_actions(symbol)}
        moved = (frame["preclose"] - frame["close"].shift(1)).abs() > 1e-6
        moved.iloc[0] = False
        unexplained_ex = [date for date in frame.index[moved] if str(date.date()) not in events]
        if unexplained_ex:
            issues.append(_issue(symbol, "preclose_without_event", "warning", unexplained_ex))
    return issues


def check_snapshot(
    root: str | Path,
    *,
    symbols: list[str] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-compatible report; ``ok`` is False when any error is present."""

    store = DataStore(root)
    names = (
        sorted({normalize_symbol(item) for item in symbols})
        if symbols
        else sorted(path.stem for path in Path(root).glob("*.csv"))
    )
    calendar = pd.DatetimeIndex(store.load(CALENDAR_SYMBOL).index)
    issues: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}
    for symbol in names:
        frame = store.load(symbol)
        coverage[symbol] = {
            "kind": "index" if symbol in INDEX_SYMBOLS else "stock",
            "rows": len(frame),
            "start": str(frame.index.min().date()),
            "end": str(frame.index.max().date()),
        }
        if as_of is not None and frame.index.max() < pd.Timestamp(as_of):
            issues.append(_issue(symbol, "stale", "error", [frame.index.max()], f"last session before {as_of}"))
        if symbol in INDEX_SYMBOLS:
            missing = calendar[(calendar >= frame.index.min()) & (calendar <= frame.index.max())].difference(frame.index)
            if len(missing):
                issues.append(_issue(symbol, "calendar_gap", "warning", missing))
            continue
        issues.extend(_stock_issues(store, symbol, frame, calendar))
    stocks = [item for item in coverage.values() if item["kind"] == "stock"]
    return {
        "root": str(root),
        "adjustment": store.adjustment,
        "ok": not any(item["severity"] == "error" for item in issues),
        "stock_rows": int(np.sum([item["rows"] for item in stocks])) if stocks else 0,
        "coverage": coverage,
        "issues": issues,
    }
