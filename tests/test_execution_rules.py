from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest
from test_execution import _canonical_pending, _frame

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.execution.market_constraints import (
    UnsupportedSecurityError,
    legal_buy_shares,
    legal_sell_shares,
    limit_rate,
    price_limits,
)
from uquant.types import AccountState, Position, Tranche

PROXY = DEFAULT_CONFIG.override(execution_clock="DAILY_PROXY", slippage=0.0)


def _rows(symbol_open: float, close: float = 100.0, *, sessions=("2026-01-05", "2026-01-06")):
    return _frame([
        {"date": day, "open": symbol_open, "high": symbol_open * 1.01, "low": symbol_open * 0.99,
         "close": close, "volume": 1e8, "amount": 1e10}
        for day in sessions
    ])


def _held(symbol: str, shares: int, cost: float) -> Position:
    return Position(symbol=symbol, shares=shares, avg_cost=cost, entry_date="2026-01-02",
                    tranches=[Tranche("t", "CORE", shares, cost, "2026-01-02", "2026-01-05", cost)])


def test_star_increment_below_minimum_is_not_submitted_and_budget_is_not_rounded_up() -> None:
    assert legal_buy_shares("sh688008", 100) == 0
    assert legal_buy_shares("sh688008", 201) == 201
    assert legal_buy_shares("sz300308", 250) == 200
    # A STAR balance below 200 is sold in one order; a partial sale cannot strand it.
    assert legal_sell_shares("sh688008", 150, holding=150) == 150
    assert legal_sell_shares("sh688008", 150, holding=350) == 0
    assert legal_sell_shares("sz300308", 150, holding=1_050) == 100

    account = AccountState(initial_cash=1e6, cash=1e6 - 20_000,
                           positions={"sh688008": _held("sh688008", 200, 100.0)})
    target = 300 * 100.0 / (account.cash + 20_000)
    account.pending_orders = [_canonical_pending("2026-01-05", "sh688008", "BUY", target, "add")]
    fills = ExecutionPlanner(PROXY).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel={"sh688008": _rows(100.0)})
    assert fills == []

    fresh = AccountState.empty(19_990.0)
    fresh.pending_orders = [_canonical_pending("2026-01-05", "sh688008", "BUY", 0.999, "entry")]
    assert ExecutionPlanner(PROXY).execute_open(
        date=pd.Timestamp("2026-01-06"), account=fresh, panel={"sh688008": _rows(100.0)}) == []


def test_missing_session_is_valued_at_last_valid_close_not_cost() -> None:
    held = _held("sz300308", 1_000, 100.0)
    account = AccountState(initial_cash=100_000, cash=100_000, positions={"sz300308": held})
    account.pending_orders = [_canonical_pending("2026-01-05", "sh603986", "BUY", 0.20, "entry")]
    panel = {
        "sz300308": _rows(200.0, 200.0, sessions=("2026-01-02", "2026-01-05")),
        "sh603986": _rows(100.0),
    }
    fills = ExecutionPlanner(PROXY).execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel)
    assert [fill.shares for fill in fills] == [600]


def test_price_limits_follow_board_status_date_and_decimal_ticks() -> None:
    assert limit_rate("sh600487", "2026-07-03", special_treatment=True) == 0.05
    assert limit_rate("sh600487", "2026-07-06", special_treatment=True) == 0.10
    assert limit_rate("sz300308", "2026-07-03", special_treatment=True) == 0.20
    assert limit_rate("sh688008", "2023-01-03") == 0.20
    assert price_limits("sz002371", "2024-01-02", 10.05) == (Decimal("9.05"), Decimal("11.06"))
    with pytest.raises(UnsupportedSecurityError):
        limit_rate("bj430047", "2024-01-02")
