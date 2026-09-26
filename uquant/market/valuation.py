"""One valuation rule for every account mark: execution, daily equity, and reports."""

from __future__ import annotations

import math
import numbers
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import pandas as pd

from ..types import AccountState


@dataclass(frozen=True, slots=True)
class Mark:
    """A position mark with its session, field, and quality."""

    price: float
    session: pd.Timestamp
    field: str
    status: str  # "CURRENT" on the valuation session, "LAST_VALID_CLOSE" otherwise


class ValuationError(RuntimeError):
    """Raised when a held position has no valid visible price."""


def _finite_positive(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    number = float(value)
    return math.isfinite(number) and number > 0


def mark_price(frame: pd.DataFrame | None, as_of: pd.Timestamp, *, field: str = "close") -> Mark | None:
    """Return the session ``field`` or else the last valid close before ``as_of``.

    A missing session row is valued at the latest earlier valid close, never
    at cost and never at a stale ``field`` other than close.
    """

    if frame is None or frame.empty:
        return None
    if field not in frame.columns:
        raise KeyError(field)
    date = pd.Timestamp(as_of)
    if date in frame.index:
        row = frame.loc[date]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        if _finite_positive(row[field]):
            return Mark(float(row[field]), date, field, "CURRENT")
    earlier = frame.index < date
    closes = pd.Series(pd.to_numeric(frame["close"][earlier], errors="coerce"))
    valid = closes[closes > 0]
    if valid.empty:
        return None
    return Mark(float(valid.iloc[-1]), pd.Timestamp(cast(pd.Timestamp, valid.index[-1])), "close", "LAST_VALID_CLOSE")


def account_equity(
    account: AccountState,
    panel: Mapping[str, pd.DataFrame],
    as_of: pd.Timestamp,
    *,
    field: str = "close",
) -> float:
    """Return cash plus every held position marked by :func:`mark_price`."""

    total = account.cash
    for symbol, position in account.positions.items():
        if position.shares <= 0:
            continue
        mark = mark_price(panel.get(symbol), as_of, field=field)
        if mark is None:
            raise ValuationError(f"{symbol} has no valid price on or before {pd.Timestamp(as_of).date()}")
        total += position.shares * mark.price
    return total


__all__ = ("Mark", "ValuationError", "account_equity", "mark_price")
