"""A-share board rules: price limits, tick rounding, and order quantity.

Rules are resolved by trading date, board, and special status. Only the
boards traded by this long-only A-share tool are supported; any other
code is rejected instead of inheriting a default limit.
"""

from __future__ import annotations

import math
from datetime import date as date_type
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, cast

import pandas as pd

from ..types import (
    Side,
)

DateLike = str | date_type | pd.Timestamp

Board = Literal["MAIN", "CHINEXT", "STAR"]

_CHINEXT_REGISTRATION = date_type(2020, 8, 24)
_STAR_OPEN = date_type(2019, 7, 22)
_MAIN_ST_TEN_PERCENT = date_type(2026, 7, 6)
_TICK = Decimal("0.01")


class UnsupportedSecurityError(ValueError):
    """Raised for codes outside the supported SH/SZ main, ChiNext, and STAR boards."""


def security_board(symbol: str) -> Board:
    """Return the exchange board of one canonical ``sh``/``sz`` stock code."""

    market, digits = symbol[:2], symbol[2:]
    if len(digits) != 6 or not digits.isdigit():
        raise UnsupportedSecurityError(f"unsupported security code: {symbol}")
    if market == "sh" and digits.startswith(("600", "601", "603", "605")):
        return "MAIN"
    if market == "sh" and digits.startswith(("688", "689")):
        return "STAR"
    if market == "sz" and digits.startswith(("000", "001", "002", "003")):
        return "MAIN"
    if market == "sz" and digits.startswith(("300", "301")):
        return "CHINEXT"
    raise UnsupportedSecurityError(f"unsupported security code: {symbol}")


def _session_date(value: DateLike) -> date_type:
    return pd.Timestamp(value).date()


def limit_rate(symbol: str, trade_date: DateLike, *, special_treatment: bool = False) -> float:
    """Return the daily price-limit rate effective for one board, status, and date."""

    board = security_board(symbol)
    day = _session_date(trade_date)
    if board == "STAR":
        if day < _STAR_OPEN:
            raise UnsupportedSecurityError("STAR board rules start on 2019-07-22")
        return 0.20
    if board == "CHINEXT":
        return 0.20 if day >= _CHINEXT_REGISTRATION else (0.05 if special_treatment else 0.10)
    if special_treatment:
        return 0.10 if day >= _MAIN_ST_TEN_PERCENT else 0.05
    return 0.10


def round_price(value: float | Decimal) -> Decimal:
    """Round one price to the 0.01 yuan tick with half-up decimal rounding."""

    return Decimal(str(value)).quantize(_TICK, rounding=ROUND_HALF_UP)


def price_limits(
    symbol: str,
    trade_date: DateLike,
    reference_close: float,
    *,
    special_treatment: bool = False,
) -> tuple[Decimal, Decimal]:
    """Return the tick-rounded lower and upper limit prices for one session."""

    rate = Decimal(str(limit_rate(symbol, trade_date, special_treatment=special_treatment)))
    reference = Decimal(str(reference_close))
    return round_price(reference * (1 - rate)), round_price(reference * (1 + rate))


def _blocked(
    symbol: str,
    side: str,
    row: pd.Series | pd.DataFrame,
    previous_close: float,
    trade_date: DateLike | None = None,
) -> bool:
    """Use only the observable open; an open at the limit price has no assured fill.

    ``previous_close`` is the exchange limit reference in the same price
    coordinate as ``row``. A session flagged ``no_price_limit`` (new-listing
    sessions) cannot be limit blocked.
    """

    if isinstance(row, pd.DataFrame):
        if row.empty:
            return True
        row = row.iloc[-1]
    open_price = float(row["open"])
    if not math.isfinite(open_price) or open_price <= 0 or not math.isfinite(previous_close) or previous_close <= 0:
        return True
    if bool(row.get("no_price_limit", False)):
        return False
    session = trade_date if trade_date is not None else pd.Timestamp(cast(pd.Timestamp, row.name))
    lower, upper = price_limits(
        symbol,
        session,
        previous_close,
        special_treatment=bool(row.get("special_treatment", False)),
    )
    opened = round_price(open_price)
    if side == Side.BUY.value:
        return opened >= upper
    return opened <= lower


market_execution_blocked = _blocked


def minimum_order_shares(symbol: str) -> int:
    """Return the smallest legal new order quantity for one board."""

    return 200 if security_board(symbol) == "STAR" else 100


def legal_buy_shares(symbol: str, shares: int) -> int:
    """Round one buy request down to a legal new order quantity, or zero."""

    if shares <= 0:
        return 0
    if security_board(symbol) == "STAR":
        return shares if shares >= 200 else 0
    return shares // 100 * 100


def legal_sell_shares(symbol: str, shares: int, *, holding: int) -> int:
    """Round one sell request to a legal quantity for the given holding.

    A balance below the board minimum can only be sold in one order, so a
    request that would leave such an odd balance behind while the whole
    holding is sellable is reduced to a legal size instead of stranding it.
    """

    shares = min(max(0, shares), holding)
    if shares <= 0:
        return 0
    if shares == holding:
        return shares
    if security_board(symbol) == "STAR":
        return shares if shares >= 200 else 0
    return shares // 100 * 100


def buy_share_step(symbol: str, shares: int) -> int:
    """Return the decrement that keeps a legal buy quantity legal."""

    if security_board(symbol) == "STAR":
        return 1 if shares > 200 else 200
    return 100
