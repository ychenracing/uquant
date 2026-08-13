from __future__ import annotations

import copy
from dataclasses import replace

import pandas as pd
import pytest

from uquant.account import load_account, save_account
from uquant.broker import _allocate_broker_sale, sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    fee_components,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.types import (
    AccountOrder,
    AccountState,
    OrderStatus,
    PendingOrder,
    Position,
    ReductionPolicy,
    Target,
    Tranche,
)


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def test_broker_snapshot_reconciles_real_fills_idempotently():
    pending = PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="confirmed mature leader core",
        lifecycle="CORE",
        order_id="O000000001",
    )
    ledger = AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason=pending.reason,
        lifecycle="CORE",
        status=OrderStatus.OPEN.value,
    )
    account = AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    snapshot = {
        "as_of": "2026-01-06",
        "cash": 1_499_870.0,
        "positions": [
            {
                "symbol": "300308",
                "shares": 5_000,
                "sellable_shares": 0,
                "avg_cost": 100.026,
            }
        ],
        "fills": [
            {
                "fill_id": "BROKER-0001",
                "order_id": "O000000001",
                "fill_date": "2026-01-06",
                "symbol": "300308",
                "side": "BUY",
                "shares": 5_000,
                "price": 100.0,
                "commission": 125.0,
                "transfer_fee": 5.0,
                "final": True,
                "remaining_shares": 0,
            }
        ],
    }

    first = sync_broker_snapshot(account, snapshot)
    second = sync_broker_snapshot(account, snapshot)

    assert first["fills_imported"] == 1
    assert second["fills_imported"] == 0
    assert len(account.fills) == 1
    assert account.order_ledger[0].status == OrderStatus.FILLED.value
    assert account.pending_orders == []
    assert account.cash == 1_499_870.0
    assert account.positions["sz300308"].shares == 5_000
    assert account.positions["sz300308"].sellable_shares("2026-01-06") == 0
    assert account.positions["sz300308"].sellable_shares("2026-01-07") == 5_000
    assert account.broker_as_of == "2026-01-06"


def test_broker_as_of_is_durable_idempotent_and_monotonic(tmp_path):
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    account.last_successful_run = "2026-01-01"
    snapshot = {
        "as_of": "2026-01-10",
        "cash": 2_000_000.0,
        "positions": [],
        "fills": [],
    }

    first = sync_broker_snapshot(account, snapshot)
    second = sync_broker_snapshot(account, snapshot)
    assert first["as_of"] == second["as_of"] == "2026-01-10"
    assert account.broker_as_of == "2026-01-10"

    path = tmp_path / "broker-account.json"
    save_account(account, path)
    restored = load_account(path)
    assert restored.broker_as_of == "2026-01-10"

    before = copy.deepcopy(restored.to_dict())
    with pytest.raises(ValueError, match="predates the latest broker snapshot"):
        sync_broker_snapshot(
            restored,
            {
                "as_of": "2026-01-09",
                "cash": 1.0,
                "positions": [],
                "fills": [],
            },
        )
    assert restored.to_dict() == before

    sync_broker_snapshot(
        restored,
        {
            "as_of": "2026-01-11",
            "cash": 2_000_000.0,
            "positions": [],
            "fills": [],
        },
    )
    assert restored.broker_as_of == "2026-01-11"


def test_broker_sync_rolls_back_every_state_on_late_validation_failure():
    pending = PendingOrder(
        "2026-01-05",
        "sz300308",
        "BUY",
        0.50,
        "entry",
        "CORE",
        order_id="O000000001",
    )
    ledger = AccountOrder(
        "O000000001",
        "2026-01-05",
        "2026-01-05",
        "sz300308",
        "BUY",
        0.50,
        "entry",
        "CORE",
        status=OrderStatus.OPEN.value,
    )
    account = AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    before = copy.deepcopy(account.to_dict())
    payload = {
        "as_of": "2026-01-06",
        "cash": 1_999_000.0,
        "fills": [
            {
                "fill_id": "atomic-fill",
                "order_id": "O000000001",
                "fill_date": "2026-01-06",
                "symbol": "300308",
                "side": "BUY",
                "shares": 100,
                "price": 10.0,
                "final": True,
                "remaining_shares": 0,
            }
        ],
        "positions": [
            {
                "symbol": "300308",
                "shares": 100,
                "sellable_shares": 0,
                "avg_cost": 10.0,
            },
            {
                "symbol": "sz300308",
                "shares": 100,
                "sellable_shares": 0,
                "avg_cost": 10.0,
            },
        ],
    }

    with pytest.raises(ValueError, match="duplicate broker position"):
        sync_broker_snapshot(account, payload)
    assert account.to_dict() == before


def test_partial_broker_sell_attribution_creates_a_complete_degraded_allocation(tmp_path):
    symbol = "sz300308"
    pending = PendingOrder(
        "2026-01-05",
        symbol,
        "SELL",
        0.0,
        "risk exit",
        "CORE",
        order_id="O000000001",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    ledger = AccountOrder(
        "O000000001",
        "2026-01-05",
        "2026-01-05",
        symbol,
        "SELL",
        0.0,
        "risk exit",
        "CORE",
        status=OrderStatus.OPEN.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    account = AccountState(
        initial_cash=2_000.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=200,
                avg_cost=10.0,
                entry_date="2026-01-02",
                highest_close=12.0,
                tranches=[
                    Tranche(
                        "known-core",
                        "CORE",
                        100,
                        10.0,
                        "2026-01-02",
                        "2026-01-03",
                        12.0,
                        lowest_close=9.0,
                    )
                ],
            )
        },
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        data_hash="data",
        code_hash="code",
        operating_peak=2_000.0,
        capital_peak=2_000.0,
    )
    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-06",
            "cash": 1_500.0,
            "fills": [
                {
                    "fill_id": "partial-attribution",
                    "order_id": "O000000001",
                    "fill_date": "2026-01-06",
                    "symbol": "300308",
                    "side": "SELL",
                    "shares": 150,
                    "price": 10.0,
                    "commission": 5.0,
                    "stamp_duty": 0.75,
                    "transfer_fee": 0.015,
                    "final": True,
                    "remaining_shares": 0,
                }
            ],
            "positions": [
                {
                    "symbol": "300308",
                    "shares": 50,
                    "sellable_shares": 50,
                    "avg_cost": 10.0,
                    "entry_date": "2026-01-02",
                    "highest_close": 12.0,
                }
            ],
        },
    )

    allocations = account.fills[-1].sold_tranches
    assert sum(int(item["shares"]) for item in allocations) == 150
    assert [item["tranche_id"] for item in allocations] == [
        "known-core",
        "broker-degraded-sale:partial-attribution",
    ]
    degraded = allocations[-1]
    assert degraded["degraded"] is True
    assert degraded["shares"] == 50
    event = next(
        item for item in account.reconciliation_events if item["event"] == "sell_lot_attribution_incomplete"
    )
    assert event["broker_shares"] == 150
    assert event["attributed_shares"] == 100
    assert event["degraded_shares"] == 50
    assert account.positions[symbol].shares == 50
    assert sum(item.shares for item in account.positions[symbol].tranches) == 50

    path = tmp_path / "degraded-account.json"
    save_account(account, path)
    restored = load_account(path)
    assert restored.to_dict() == account.to_dict()


def test_existing_order_id_rejects_immutable_metadata_drift():
    ledger = AccountOrder(
        "O000000001",
        "2026-01-05",
        "2026-01-05",
        "sz300308",
        "BUY",
        0.50,
        "entry",
        "CORE",
        status=OrderStatus.OPEN.value,
    )
    drifted = PendingOrder(
        "2026-01-05",
        "sz300308",
        "BUY",
        0.50,
        "different reason",
        "CORE",
        order_id=ledger.order_id,
    )
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = [ledger]

    with pytest.raises(RuntimeError, match=r"immutable metadata.*reason"):
        reconcile_account_orders(
            account=account,
            previous=[drifted],
            current=(drifted,),
            submitted_date="2026-01-05",
        )


def test_broker_risk_priority_uses_the_simulator_core_damage_order():
    tranches = [
        Tranche(
            "healthy-core",
            "CORE",
            100,
            10.0,
            "2026-01-01",
            "2026-01-02",
            20.0,
            lowest_close=9.9,
            mfe=1.0,
            mae=-0.01,
            entry_score=0.95,
        ),
        Tranche(
            "damaged-core",
            "CORE",
            100,
            12.0,
            "2026-01-02",
            "2026-01-03",
            13.0,
            lowest_close=7.2,
            mfe=0.08,
            mae=-0.40,
            entry_score=0.20,
        ),
    ]

    allocations = _allocate_broker_sale(
        tranches,
        shares=100,
        fill_date="2026-01-06",
        policy=ReductionPolicy.RISK_PRIORITY.value,
    )

    assert [item["tranche_id"] for item in allocations] == ["damaged-core"]
    assert [item.tranche_id for item in tranches] == ["healthy-core"]


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


def test_decisive_strategic_owner_can_fill_above_ordinary_symbol_cap() -> None:
    symbol = "causal_dominant"
    panel = {
        symbol: _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 100_000_000,
                    "amount": 10_000_000_000.0,
                },
                {
                    "date": "2026-01-06",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 100_000_000,
                    "amount": 10_000_000_000.0,
                },
            ]
        )
    }
    account = AccountState.empty(2_000_000.0)
    account.strategic_epoch = 1
    account.strategic_cohort_symbols = [symbol]
    account.strategic_cohort_targets = {symbol: 1.0}
    account.candidate_tenure.update(
        {
            "strategic_cohort_active": 1,
            "strategic_dominant_epoch": 1,
        }
    )
    account.pending_orders = [
        PendingOrder("2026-01-05", symbol, "BUY", 1.0, "dominant strategic owner", "CORE")
    ]

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel=panel,
    )

    assert len(fills) == 1
    position_value = account.positions[symbol].shares * fills[0].price
    post_fill_equity = account.cash + position_value
    assert position_value / post_fill_equity > DEFAULT_CONFIG.max_symbol_weight
    assert position_value / post_fill_equity <= DEFAULT_CONFIG.strategic_dominant_max_weight
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
    retained = PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE", attempts=2)
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


def test_zero_weight_sell_keeps_original_intent_when_daily_reason_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sz000001",
        "SELL",
        0.0,
        "recovery exit",
        "RECOVERY",
        remaining_shares=1_000,
        order_id="O000000001",
        reason_code="recovery_exit",
        exit_kind="strategy",
    )
    planned = PendingOrder(
        "2026-01-06",
        "sz000001",
        "SELL",
        0.0,
        "lifecycle exit",
        "CORE",
        reason_code="lifecycle_exit",
        exit_kind="lifecycle",
    )
    target = Target(
        "sz000001",
        0.0,
        "CORE",
        0.0,
        0.0,
        "lifecycle exit",
        reason_code="lifecycle_exit",
        exit_kind="lifecycle",
    )

    merged = merge_pending_orders(retained=[retained], planned=(planned,), targets=(target,))

    assert merged == (retained,)


def test_partial_risk_sell_survives_a_subthreshold_risk_escalation() -> None:
    retained = PendingOrder(
        "2026-01-05",
        "sh601869",
        "SELL",
        0.104959,
        "capital budget gross cap",
        "CORE",
        remaining_shares=28_600,
        order_id="O000000044",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="capital_budget",
        exit_kind="capital_budget",
    )
    planned = PendingOrder(
        "2026-01-06",
        "sh601869",
        "SELL",
        0.104248,
        "portfolio risk-off gross cap",
        "CORE",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_off",
        exit_kind="risk_off",
    )
    target = Target(
        "sh601869",
        0.104248,
        "CORE",
        0.0,
        0.0,
        "portfolio risk-off gross cap",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_off",
        exit_kind="risk_off",
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert merged == (retained,)


def test_submitted_buy_survives_economically_equivalent_target_drift() -> None:
    retained = PendingOrder(
        "2026-01-05",
        "sz300502",
        "BUY",
        0.315,
        "strategic restore",
        "CORE",
        remaining_shares=3_600,
        order_id="O000000001",
        reason_code="strategic_cohort",
    )
    planned = replace(retained, signal_date="2026-01-06", target_weight=0.317, order_id="")
    target = Target(
        "sz300502",
        0.317,
        "CORE",
        0.90,
        1.0,
        "strategic restore",
        reason_code="strategic_cohort",
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert merged == (retained,)

    equivalent_target = replace(target, weight=0.350)
    equivalent_plan = replace(planned, target_weight=0.350)
    equivalent = merge_pending_orders(
        retained=[retained],
        planned=(equivalent_plan,),
        targets=(equivalent_target,),
        cfg=DEFAULT_CONFIG,
    )
    assert equivalent == (retained,)

    material_target = replace(target, weight=0.370)
    material_plan = replace(planned, target_weight=0.370)
    replaced = merge_pending_orders(
        retained=[retained],
        planned=(material_plan,),
        targets=(material_target,),
        cfg=DEFAULT_CONFIG,
    )
    assert replaced == (material_plan,)


def test_broker_order_ledger_counts_submission_and_replacement_not_fills():
    account = AccountState.empty(2e6)
    retained = PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE")
    same = PendingOrder("2026-01-06", "sz000001", "BUY", 0.5, "refresh", "CORE")
    target = Target("sz000001", 0.5, "CORE", 0.8, 1.0, "entry")
    current = merge_pending_orders(
        retained=[retained],
        planned=(same,),
        targets=(target,),
    )
    reconcile_account_orders(
        account=account,
        previous=[retained],
        current=current,
        submitted_date="2026-01-06",
    )
    assert current == (retained,)
    assert len(account.order_ledger) == 1

    replacement = PendingOrder("2026-01-07", "sz000001", "SELL", 0.0, "risk", "CORE")
    exit_target = Target("sz000001", 0.0, "CORE", 0.0, 0.0, "risk")
    replaced = merge_pending_orders(
        retained=list(current),
        planned=(replacement,),
        targets=(exit_target,),
    )
    reconcile_account_orders(
        account=account,
        previous=list(current),
        current=replaced,
        submitted_date="2026-01-07",
    )
    assert len(account.order_ledger) == 2
    assert account.order_ledger[0].status == "REPLACED"
    assert account.order_ledger[0].replaced_by == account.order_ledger[1].order_id


def test_blocked_then_filled_instruction_remains_one_broker_order():
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
                    "open": 10.8,
                    "high": 11,
                    "low": 10.5,
                    "close": 10.9,
                    "volume": 1e8,
                    "amount": 1.09e9,
                },
            ]
        )
    }
    account = AccountState.empty(2e6)
    account.pending_orders = [PendingOrder("2026-01-05", "sz000001", "BUY", 0.5, "entry", "CORE")]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].last_event == "LIMIT_BLOCKED"
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel)
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].status == "FILLED"
    assert account.fills[0].order_id == account.order_ledger[0].order_id


def test_execution_policy_metadata_flows_from_target_to_order_ledger_and_fill():
    target = Target(
        "sz000001",
        0.0,
        "CORE",
        0.0,
        0.0,
        "portfolio structural reduction",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="portfolio_damage",
        exit_kind="portfolio_risk",
    )
    position = Position(
        symbol="sz000001",
        shares=100,
        avg_cost=10.0,
        entry_date="2026-01-01",
        highest_close=12.0,
        tranches=[
            Tranche(
                "core",
                "CORE",
                100,
                10.0,
                "2026-01-01",
                "2026-01-02",
                12.0,
            )
        ],
    )
    account = AccountState(
        initial_cash=1_000.0,
        cash=0.0,
        positions={position.symbol: position},
        operating_peak=1_000.0,
        capital_peak=1_000.0,
    )
    orders = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={position.symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    assert len(orders) == 1
    assert orders[0].reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert orders[0].reason_code == "portfolio_damage"
    assert orders[0].exit_kind == "portfolio_risk"

    account.pending_orders = list(orders)
    panel = {
        position.symbol: _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 1_000_000,
                    "amount": 10_000_000,
                },
                {
                    "date": "2026-01-06",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 1_000_000,
                    "amount": 10_000_000,
                },
            ]
        )
    }
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel=panel,
    )
    assert len(fills) == 1
    ledger = account.order_ledger[0]
    fill = fills[0]
    assert (
        ledger.reduction_policy,
        ledger.reason_code,
        ledger.exit_kind,
    ) == (
        ReductionPolicy.RISK_PRIORITY.value,
        "portfolio_damage",
        "portfolio_risk",
    )
    assert (
        fill.reduction_policy,
        fill.reason_code,
        fill.exit_kind,
    ) == (
        ledger.reduction_policy,
        ledger.reason_code,
        ledger.exit_kind,
    )


def test_buy_tranche_uses_the_fill_all_in_unit_cost():
    symbol = "sz000001"
    account = AccountState.empty(2_000_000.0)
    account.pending_orders = [PendingOrder("2026-01-05", symbol, "BUY", 0.50, "entry", "CORE")]
    panel = {
        symbol: _frame(
            [
                {
                    "date": day,
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "volume": 100_000_000,
                    "amount": 10_100_000_000,
                }
                for day in ("2026-01-05", "2026-01-06")
            ]
        )
    }
    fill = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel=panel,
    )[0]
    position = account.positions[symbol]
    tranche = position.tranches[0]
    all_in_unit_cost = (fill.gross_value + fill.commission + fill.transfer_fee) / fill.shares
    assert tranche.avg_cost == pytest.approx(all_in_unit_cost)
    assert position.avg_cost == pytest.approx(all_in_unit_cost)


def test_merge_replaces_same_weight_order_when_execution_policy_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sz000001",
        "SELL",
        0.0,
        "exit",
        "CORE",
    )
    planned = PendingOrder(
        "2026-01-06",
        "sz000001",
        "SELL",
        0.0,
        "exit",
        "CORE",
    )
    target = Target("sz000001", 0.0, "CORE", 0.0, 0.0, "exit")
    changes: tuple[dict[str, str], ...] = (
        {"reduction_policy": ReductionPolicy.RISK_PRIORITY.value},
        {"reason_code": "sector_shock"},
        {"exit_kind": "portfolio_risk"},
    )
    for change in changes:
        replacement = replace(planned, **change)
        changed_target = replace(target, **change)
        merged = merge_pending_orders(
            retained=[retained],
            planned=(replacement,),
            targets=(changed_target,),
        )
        assert merged == (replacement,)


def test_merge_replaces_same_weight_order_when_lifecycle_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sz000001",
        "BUY",
        0.50,
        "controlled rebound",
        "RECOVERY",
        remaining_shares=200,
        attempts=1,
        order_id="O000000001",
    )
    replacement = PendingOrder(
        "2026-01-06",
        "sz000001",
        "BUY",
        0.50,
        "mature leader",
        "CORE",
    )
    target = Target(
        "sz000001",
        0.50,
        "CORE",
        0.90,
        0.95,
        "mature leader",
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(replacement,),
        targets=(target,),
    )

    assert merged == (replacement,)
    assert merged[0].lifecycle == "CORE"
    assert merged[0].order_id == ""


def test_merge_retains_partial_risk_sell_with_nonzero_target_weight():
    retained = PendingOrder(
        "2026-01-05",
        "sz000001",
        "SELL",
        0.30,
        "risk trim",
        "CORE",
        remaining_shares=200,
        attempts=1,
        order_id="O000000001",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="sector_shock",
        exit_kind="portfolio_risk",
    )
    planned = replace(retained, signal_date="2026-01-06", order_id="")
    target = Target(
        "sz000001",
        0.30,
        "CORE",
        0.0,
        0.0,
        "risk trim",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="sector_shock",
        exit_kind="portfolio_risk",
    )
    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
    )
    assert merged == (retained,)
    assert merged[0].remaining_shares == 200
    assert merged[0].order_id == "O000000001"


def test_risk_priority_is_t1_aware_and_survives_partial_fills_across_days():
    symbol = "sz000001"
    tranches = [
        Tranche(
            "core-healthy",
            "CORE",
            100,
            10.0,
            "2026-01-01",
            "2026-01-02",
            30.0,
            mfe=2.0,
            mae=-0.01,
            entry_score=0.90,
        ),
        Tranche(
            "core-damaged",
            "CORE",
            100,
            12.0,
            "2026-01-02",
            "2026-01-03",
            13.0,
            mfe=0.08,
            mae=-0.40,
            entry_score=0.20,
        ),
        Tranche(
            "recovery",
            "RECOVERY",
            100,
            11.0,
            "2026-01-03",
            "2026-01-04",
            14.0,
            mfe=0.10,
            mae=-0.15,
        ),
        Tranche(
            "add1",
            "ADD1",
            100,
            14.0,
            "2026-01-04",
            "2026-01-05",
            15.0,
            mfe=0.05,
            mae=-0.10,
        ),
        Tranche(
            "add2",
            "ADD2",
            100,
            15.0,
            "2026-01-05",
            "2026-01-06",
            16.0,
            mfe=0.04,
            mae=-0.20,
        ),
        Tranche(
            "satellite-sellable",
            "SATELLITE",
            100,
            16.0,
            "2026-01-05",
            "2026-01-06",
            17.0,
            mfe=0.03,
            mae=-0.25,
            entry_score=0.42,
            entry_confidence=0.73,
            entry_regime="TREND",
            entry_industry_strength=0.61,
        ),
        Tranche(
            "satellite-t1",
            "SATELLITE",
            100,
            17.0,
            "2026-01-06",
            "2026-01-07",
            18.0,
            mfe=0.01,
            mae=-0.05,
        ),
    ]
    position = Position(
        symbol=symbol,
        shares=700,
        avg_cost=sum(item.avg_cost for item in tranches) / len(tranches),
        entry_date="2026-01-01",
        highest_close=30.0,
        lifecycle="SATELLITE",
        tranches=tranches,
    )
    order = PendingOrder(
        "2026-01-05",
        symbol,
        "SELL",
        0.0,
        "crisis reduction",
        "CORE",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="crisis",
        exit_kind="portfolio_risk",
    )
    account = AccountState(
        initial_cash=7_000.0,
        cash=0.0,
        positions={symbol: position},
        pending_orders=[order],
        operating_peak=7_000.0,
        capital_peak=7_000.0,
    )
    rows = [
        {
            "date": day,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 60_000,
            "amount": 600_000,
        }
        for day in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")
    ]
    panel = {symbol: _frame(rows)}
    planner = ExecutionPlanner(DEFAULT_CONFIG)

    first = planner.execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel=panel,
    )
    assert [item["tranche_id"] for item in first[0].sold_tranches] == [
        "satellite-sellable",
        "add2",
        "add1",
    ]
    first_allocation = first[0].sold_tranches[0]
    assert {
        key: first_allocation[key]
        for key in (
            "tranche_id",
            "shares",
            "cost",
            "unit_cost",
            "avg_cost",
            "cost_basis",
            "lifecycle",
            "mfe",
            "mae",
            "entry_date",
            "entry_score",
            "entry_confidence",
            "entry_regime",
            "entry_industry_strength",
        )
    } == {
        "tranche_id": "satellite-sellable",
        "shares": 100,
        "cost": 16.0,
        "unit_cost": 16.0,
        "avg_cost": 16.0,
        "cost_basis": 1_600.0,
        "lifecycle": "SATELLITE",
        "mfe": 0.03,
        "mae": -0.25,
        "entry_date": "2026-01-05",
        "entry_score": 0.42,
        "entry_confidence": 0.73,
        "entry_regime": "TREND",
        "entry_industry_strength": 0.61,
    }
    for cost_name in (
        "commission",
        "stamp_duty",
        "transfer_fee",
        "slippage_cost",
    ):
        assert sum(float(item[cost_name]) for item in first[0].sold_tranches) == pytest.approx(
            getattr(first[0], cost_name)
        )
        for item in first[0].sold_tranches:
            assert float(item[cost_name]) == pytest.approx(
                getattr(first[0], cost_name) * int(item["shares"]) / first[0].shares
            )
    assert sum(float(item["fees"]) for item in first[0].sold_tranches) == pytest.approx(
        first[0].commission + first[0].stamp_duty + first[0].transfer_fee
    )
    remaining = account.positions[symbol]
    assert remaining.shares == 400
    assert remaining.avg_cost == 12.5
    assert remaining.entry_date == "2026-01-01"
    assert remaining.lifecycle == "SATELLITE"
    assert remaining.highest_close == 30.0
    assert account.pending_orders[0].remaining_shares == 400
    assert account.pending_orders[0].reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert account.order_ledger[0].status == OrderStatus.PARTIALLY_FILLED.value
    assert account.order_ledger[0].remaining_shares == 400

    second = planner.execute_open(
        date=pd.Timestamp("2026-01-07"),
        account=account,
        panel=panel,
    )
    assert [item["tranche_id"] for item in second[0].sold_tranches] == [
        "satellite-t1",
        "recovery",
        "core-damaged",
    ]
    remaining = account.positions[symbol]
    assert remaining.shares == 100
    assert remaining.avg_cost == 10.0
    assert remaining.entry_date == "2026-01-01"
    assert remaining.lifecycle == "CORE"
    assert remaining.highest_close == 30.0
    assert account.pending_orders[0].remaining_shares == 100
    assert account.pending_orders[0].reason_code == "crisis"
    assert account.pending_orders[0].exit_kind == "portfolio_risk"
    assert first[0].order_id == second[0].order_id

    third = planner.execute_open(
        date=pd.Timestamp("2026-01-08"),
        account=account,
        panel=panel,
    )
    assert [item["tranche_id"] for item in third[0].sold_tranches] == ["core-healthy"]
    assert symbol not in account.positions
    assert account.pending_orders == []
    assert account.order_ledger[0].status == OrderStatus.FILLED.value
    assert account.order_ledger[0].filled_shares == 700
    assert all(
        fill.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
        and fill.reason_code == "crisis"
        and fill.exit_kind == "portfolio_risk"
        for fill in (*first, *second, *third)
    )


def test_fifo_exit_keeps_historical_lot_order_and_rebuilds_position():
    symbol = "sz000001"
    position = Position(
        symbol=symbol,
        shares=200,
        avg_cost=12.5,
        entry_date="2026-01-01",
        highest_close=20.0,
        lifecycle="ADD2",
        tranches=[
            Tranche(
                "new-add2",
                "ADD2",
                100,
                15.0,
                "2026-01-02",
                "2026-01-03",
                18.0,
            ),
            Tranche(
                "old-core",
                "CORE",
                100,
                10.0,
                "2026-01-01",
                "2026-01-02",
                20.0,
            ),
        ],
    )
    account = AccountState(
        initial_cash=2_000.0,
        cash=0.0,
        positions={symbol: position},
        pending_orders=[PendingOrder("2026-01-05", symbol, "SELL", 0.0, "exit", "CORE")],
        operating_peak=2_000.0,
        capital_peak=2_000.0,
    )
    panel = {
        symbol: _frame(
            [
                {
                    "date": day,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "volume": 20_000,
                    "amount": 200_000,
                }
                for day in ("2026-01-05", "2026-01-06")
            ]
        )
    }
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel=panel,
    )
    assert fills[0].reduction_policy == ReductionPolicy.FIFO.value
    assert [item["tranche_id"] for item in fills[0].sold_tranches] == ["old-core"]
    remaining = account.positions[symbol]
    assert remaining.shares == 100
    assert remaining.avg_cost == 15.0
    assert remaining.entry_date == "2026-01-02"
    assert remaining.lifecycle == "ADD2"
    assert remaining.highest_close == 18.0
    assert account.pending_orders[0].remaining_shares == 100


def test_sell_funded_recovery_handoff_waits_when_incumbent_sale_is_limit_blocked() -> None:
    signal_date = "2026-01-05"
    fill_date = "2026-01-06"
    release_date = "2026-01-07"
    incumbent = Position(
        symbol="old_core",
        shares=4_000,
        avg_cost=100.0,
        entry_date="2025-12-01",
        highest_close=100.0,
        tranches=[
            Tranche(
                "old-core",
                "CORE",
                4_000,
                100.0,
                "2025-12-01",
                "2025-12-02",
                100.0,
            )
        ],
    )
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=600_000.0,
        positions={incumbent.symbol: incumbent},
        candidate_tenure={"recovery_owner_handoff": 1},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    targets = (
        Target("old_core", 0.0, "RECOVERY", 0.0, 0.0, "recovery cohort construction"),
        Target("new_owner", 0.40, "RECOVERY", 0.9, 1.0, "recovery cohort construction"),
    )
    account.pending_orders = list(
        plan_orders(
            signal_date=signal_date,
            targets=targets,
            account=account,
            prices={"old_core": 100.0, "new_owner": 100.0},
            cfg=DEFAULT_CONFIG,
        )
    )
    panel = {
        "old_core": _frame(
            [
                {
                    "date": signal_date,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "amount": 100_000_000,
                },
                {
                    "date": fill_date,
                    "open": 90.0,
                    "high": 90.0,
                    "low": 90.0,
                    "close": 90.0,
                    "volume": 1_000_000,
                    "amount": 90_000_000,
                },
                {
                    "date": release_date,
                    "open": 90.0,
                    "high": 91.0,
                    "low": 89.0,
                    "close": 90.0,
                    "volume": 1_000_000,
                    "amount": 90_000_000,
                },
            ]
        ),
        "new_owner": _frame(
            [
                {
                    "date": signal_date,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "amount": 100_000_000,
                },
                {
                    "date": fill_date,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "amount": 100_000_000,
                },
                {
                    "date": release_date,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "amount": 100_000_000,
                },
            ]
        ),
    }

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp(fill_date),
        account=account,
        panel=panel,
    )

    assert not [fill for fill in fills if fill.side == "BUY"]
    assert {order.side for order in account.pending_orders} == {"SELL", "BUY"}
    assert account.positions["old_core"].shares == 4_000
    assert "new_owner" not in account.positions

    released = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp(release_date),
        account=account,
        panel=panel,
    )
    assert {fill.side for fill in released} == {"SELL", "BUY"}
    assert "old_core" not in account.positions
    equity = account.cash + account.positions["new_owner"].shares * 100.0
    gross = account.positions["new_owner"].shares * 100.0 / equity
    assert gross <= 0.40 + 1e-12
