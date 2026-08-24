"""Task 6 mechanical owner for market constraints."""

from __future__ import annotations

import pandas as pd

from ..types import (
    Side,
)


def _limit_rate(symbol: str) -> float:
    digits = symbol[2:]
    return 0.20 if digits.startswith(("300", "688")) else 0.10


def _blocked(
    symbol: str,
    side: str,
    row: pd.Series | pd.DataFrame,
    previous_close: float,
) -> bool:
    """Return whether volume or a one-price limit prevents this side from filling."""

    if isinstance(row, pd.DataFrame):
        if row.empty:
            return True
        row = row.iloc[-1]
    volume = float(row.get("volume", 0.0) or 0.0)
    if volume <= 0 or previous_close <= 0:
        return True
    rate = _limit_rate(symbol)
    upper = previous_close * (1.0 + rate)
    lower = previous_close * (1.0 - rate)
    if side == Side.BUY.value:
        return float(row["open"]) >= upper * 0.999 and float(row["low"]) >= upper * 0.999
    return float(row["open"]) <= lower * 1.001 and float(row["high"]) <= lower * 1.001


market_execution_blocked = _blocked
