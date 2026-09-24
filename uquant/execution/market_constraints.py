"""A-share market, lot, capacity, cash, and T+1 constraints."""

from __future__ import annotations

import math

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
    """Use only the observable open; a limit-price open has no assured fill."""

    if isinstance(row, pd.DataFrame):
        if row.empty:
            return True
        row = row.iloc[-1]
    open_price = float(row["open"])
    if not math.isfinite(open_price) or open_price <= 0 or not math.isfinite(previous_close) or previous_close <= 0:
        return True
    rate = _limit_rate(symbol)
    upper = previous_close * (1.0 + rate)
    lower = previous_close * (1.0 - rate)
    if side == Side.BUY.value:
        return open_price >= upper * 0.999
    return open_price <= lower * 1.001


market_execution_blocked = _blocked
