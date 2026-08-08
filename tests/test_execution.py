from __future__ import annotations

import pandas as pd

from unified_ai_quant.config import DEFAULT_CONFIG
from unified_ai_quant.execution import ExecutionPlanner, fee_components, merge_pending_orders
from unified_ai_quant.types import AccountState, PendingOrder, Position, Target, Tranche


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def test_next_open_and_t1_enforced():
    panel = {
        "sz300308": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 100,
                    "high": 103,
                    "low": 99,
                    "close": 102,
                    "volume": 1e8,
                    "amount": 1e10,
                },
                {
                    "date": "2026-01-06",
                    "open": 103,
                    "high": 105,
                    "low": 101,
                    "close": 104,
                    "volume": 1e8,
                    "amount": 1e10,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sz300308", "BUY", 0.5, "entry", "CORE")]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-05"), account=account, panel=panel) == []
    fills = planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel)
    assert fills and fills[0].fill_date > fills[0].signal_date
    account.pending_orders = [PendingOrder("2026-01-06", "sz300308", "SELL", 0.0, "exit", "CORE")]
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []


def test_limit_and_suspension_keep_pending():
    panel = {
        "sz000001": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 11,
                    "high": 11,
                    "low": 11,
                    "close": 11,
                    "volume": 1e8,
                    "amount": 1.1e9,
                },
                {
                    "date": "2026-01-07",
                    "open": 11,
                    "high": 11,
                    "low": 11,
                    "close": 11,
                    "volume": 0,
                    "amount": 0,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE")]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert account.pending_orders
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders


def test_continuous_up_limits_remain_pending_until_market_reopens():
    panel = {
        "sz000001": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 11,
                    "high": 11,
                    "low": 11,
                    "close": 11,
                    "volume": 1e8,
                    "amount": 1.1e9,
                },
                {
                    "date": "2026-01-07",
                    "open": 12.1,
                    "high": 12.1,
                    "low": 12.1,
                    "close": 12.1,
                    "volume": 1e8,
                    "amount": 1.21e9,
                },
                {
                    "date": "2026-01-08",
                    "open": 12.0,
                    "high": 12.3,
                    "low": 11.8,
                    "close": 12.2,
                    "volume": 1e8,
                    "amount": 1.22e9,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE")]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders[0].attempts == 2
    fills = planner.execute_open(date=pd.Timestamp("2026-01-08"), account=account, panel=panel)
    assert len(fills) == 1
    assert fills[0].side == "BUY"


def test_continuous_down_limits_retain_sell_until_market_reopens():
    panel = {
        "sz000001": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 9,
                    "high": 9,
                    "low": 9,
                    "close": 9,
                    "volume": 1e8,
                    "amount": 0.9e9,
                },
                {
                    "date": "2026-01-07",
                    "open": 8.1,
                    "high": 8.1,
                    "low": 8.1,
                    "close": 8.1,
                    "volume": 1e8,
                    "amount": 0.81e9,
                },
                {
                    "date": "2026-01-08",
                    "open": 8.2,
                    "high": 8.4,
                    "low": 8.0,
                    "close": 8.3,
                    "volume": 1e8,
                    "amount": 0.83e9,
                },
            ]
        )
    }
    position = Position(
        symbol="sz000001",
        shares=10000,
        avg_cost=10,
        entry_date="2026-01-01",
        tranches=[Tranche("old", "CORE", 10000, 10, "2026-01-01", "2026-01-02", 10)],
    )
    account = AccountState(
        initial_cash=100000,
        cash=0,
        positions={"sz000001": position},
        operating_peak=100000,
        capital_peak=100000,
    )
    account.pending_orders = [PendingOrder("2026-01-05", "sz000001", "SELL", 0.0, "exit", "CORE")]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders[0].attempts == 2
    fills = planner.execute_open(date=pd.Timestamp("2026-01-08"), account=account, panel=panel)
    assert len(fills) == 1
    assert fills[0].side == "SELL"
    assert "sz000001" not in account.positions


def test_large_opening_gap_reprices_target_and_preserves_weight_cap():
    panel = {
        "sz000001": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 15,
                    "high": 15.5,
                    "low": 10.5,
                    "close": 14.5,
                    "volume": 1e8,
                    "amount": 1.45e9,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sz000001", "BUY", 0.60, "entry", "CORE")]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    )
    assert len(fills) == 1
    position_value = account.positions["sz000001"].shares * fills[0].price
    post_fill_equity = account.cash + position_value
    assert position_value / post_fill_equity <= DEFAULT_CONFIG.max_symbol_weight + 1e-12
    assert account.cash >= 0


def test_fee_formula_is_recomputable():
    commission, stamp, transfer = fee_components("SELL", 100_000, DEFAULT_CONFIG)
    assert commission == 25
    assert stamp == 50
    assert transfer == 1


def test_sellable_shares_are_tranche_based():
    position = Position(
        symbol="sz300308",
        shares=300,
        tranches=[
            Tranche("a", "CORE", 200, 100, "2026-01-05", "2026-01-06", 100),
            Tranche("b", "ADD1", 100, 110, "2026-01-06", "2026-01-07", 110),
        ],
    )
    assert position.sellable_shares("2026-01-06") == 200


def test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200():
    panel = {
        "sh688008": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 100000,
                    "amount": 1e7,
                },
                {
                    "date": "2026-01-06",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 100000,
                    "amount": 1e7,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sh688008", "BUY", 0.60, "entry", "CORE")]
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    fills = ExecutionPlanner(cfg).execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel)
    assert fills and fills[0].shares >= 200
    assert account.pending_orders and account.pending_orders[0].remaining_shares > 0


def test_sells_release_cash_before_buys():
    panel = {
        "sz000001": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
            ]
        ),
        "sz000002": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-06",
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
            ]
        ),
    }
    old = Position(
        symbol="sz000001",
        shares=10000,
        avg_cost=10,
        entry_date="2026-01-01",
        tranches=[Tranche("old", "CORE", 10000, 10, "2026-01-01", "2026-01-02", 10)],
    )
    account = AccountState(
        initial_cash=100000, cash=0, positions={"sz000001": old}, operating_peak=100000, capital_peak=100000
    )
    account.pending_orders = [
        PendingOrder("2026-01-05", "sz000002", "BUY", 0.5, "entry", "CORE"),
        PendingOrder("2026-01-05", "sz000001", "SELL", 0.0, "exit", "CORE"),
    ]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    )
    assert [fill.side for fill in fills] == ["SELL", "BUY"]


def test_compatible_blocked_order_survives_daily_replanning():
    retained = PendingOrder(
        "2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE", attempts=2
    )
    target = Target("sz000001", 0.5, "CORE", 0.8, 1.0, "mature anchored leader")
    merged = merge_pending_orders(retained=[retained], planned=(), targets=(target,))
    assert merged == (retained,)
    assert merged[0].attempts == 2


def test_new_exit_target_cancels_stale_blocked_buy():
    retained = PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE")
    planned = PendingOrder("2026-01-06", "sz000001", "SELL", 0.0, "risk", "CORE")
    target = Target("sz000001", 0.0, "CORE", 0.0, 0.0, "risk")
    merged = merge_pending_orders(retained=[retained], planned=(planned,), targets=(target,))
    assert merged == (planned,)
