# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

import copy

import pandas as pd
import pytest

from uquant.account import load_account, save_account
from uquant.broker import _allocate_broker_sale, sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    reconcile_account_orders,
)
from uquant.types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


def _attribution_identity(
    *,
    signal_date: str,
    symbol: str,
    target_weight: float,
    origin_subsystem: str,
    mechanism: str,
    reduction_policy: str = ReductionPolicy.FIFO.value,
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
) -> dict[str, str | None]:
    lifecycle = "CORE"
    industry = default_ai_universe().industry_of(symbol, signal_date)
    if industry == "unknown":
        # SELL-only unit fixtures may model pre-universe inventory. Production
        # BUY fixtures must use a point-in-time manifest member.
        industry = "legacy_unmapped" if origin_subsystem != OriginSubsystem.LEADER.value else "optical"
    manifest = REQUIRED_AI_UNIVERSE_SHA256 if industry != "legacy_unmapped" else "0" * 64
    fields: dict[str, str | None] = {
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": manifest,
    }
    fields["event_id"] = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=origin_subsystem,
        mechanism=mechanism,
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=manifest,
        reduction_policy=reduction_policy,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    return fields


def _canonical_pending(
    signal_date: str,
    symbol: str,
    side: str,
    target_weight: float,
    reason: str,
    lifecycle: str = "CORE",
    **metadata: object,
) -> PendingOrder:
    assert lifecycle == "CORE"
    origin = (
        OriginSubsystem.LEADER.value
        if side == "BUY"
        else OriginSubsystem.RISK.value
    )
    mechanism = (
        AttributionMechanism.LEADER_SELECTION.value
        if side == "BUY"
        else AttributionMechanism.RISK_OFF.value
    )
    identity = _attribution_identity(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        origin_subsystem=origin,
        mechanism=mechanism,
        reduction_policy=str(
            metadata.get("reduction_policy", ReductionPolicy.FIFO.value)
        ),
        reason_code=str(metadata.get("reason_code", "strategy_target")),
        exit_kind=str(metadata.get("exit_kind", "strategy")),
    )
    return PendingOrder(
        signal_date,
        symbol,
        side,
        target_weight,
        reason,
        lifecycle,
        **metadata,
        **identity,
    )


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def test_broker_snapshot_reconciles_real_fills_idempotently():
    identity = _attribution_identity(
        signal_date="2026-01-05",
        symbol="sz300308",
        target_weight=0.50,
        origin_subsystem=OriginSubsystem.LEADER.value,
        mechanism=AttributionMechanism.LEADER_SELECTION.value,
    )
    pending = PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="confirmed mature leader core",
        lifecycle="CORE",
        order_id="O000000001",
        **identity,
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
        **identity,
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
    identity = _attribution_identity(
        signal_date="2026-01-05",
        symbol="sz300308",
        target_weight=0.50,
        origin_subsystem=OriginSubsystem.LEADER.value,
        mechanism=AttributionMechanism.LEADER_SELECTION.value,
    )
    pending = PendingOrder(
        "2026-01-05",
        "sz300308",
        "BUY",
        0.50,
        "entry",
        "CORE",
        order_id="O000000001",
        **identity,
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
        **identity,
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
    order_identity = _attribution_identity(
        signal_date="2026-01-05",
        symbol=symbol,
        target_weight=0.0,
        origin_subsystem=OriginSubsystem.RISK.value,
        mechanism=AttributionMechanism.RISK_GROSS_CAP.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    tranche_identity = _attribution_identity(
        signal_date="2026-01-02",
        symbol=symbol,
        target_weight=0.0,
        origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
        mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
    )
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
        **order_identity,
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
        **order_identity,
    )
    account = AccountState(
        initial_cash=2_000.0,
        cash=0.0,
        positions={
                symbol: Position(
                    symbol,
                    shares=150,
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
                        **tranche_identity,
                    )
                ],
            )
        },
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        account_migrations=[
            {
                "migrated_at_utc": "2026-01-01T00:00:00+00:00",
                "from_schema": 3,
                "to_schema": ACCOUNT_SCHEMA_VERSION,
                "from_code_hash": "old-code",
                "to_code_hash": "code",
            }
        ],
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
            "positions": [],
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
    assert symbol not in account.positions

    path = tmp_path / "degraded-account.json"
    save_account(account, path)
    restored = load_account(path)
    assert restored.to_dict() == account.to_dict()


def test_existing_order_id_rejects_immutable_metadata_drift():
    identity = _attribution_identity(
        signal_date="2026-01-05",
        symbol="sz300308",
        target_weight=0.50,
        origin_subsystem=OriginSubsystem.LEADER.value,
        mechanism=AttributionMechanism.LEADER_SELECTION.value,
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
        **identity,
    )
    drifted = _canonical_pending(
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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sz300308", "BUY", 0.5, "entry")
    ]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-05"), account=account, panel=panel) == []
    fills = planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel)
    assert fills and fills[0].fill_date > fills[0].signal_date
    account.pending_orders = [
        _canonical_pending("2026-01-06", "sz300308", "SELL", 0.0, "exit")
    ]
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []


def test_limit_and_suspension_keep_pending():
    panel = {
        "sh603986": _frame(
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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh603986", "BUY", 0.5, "entry")
    ]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert account.pending_orders
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders


def test_continuous_up_limits_remain_pending_until_market_reopens():
    panel = {
        "sh603986": _frame(
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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh603986", "BUY", 0.5, "entry")
    ]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders[0].attempts == 2
    fills = planner.execute_open(date=pd.Timestamp("2026-01-08"), account=account, panel=panel)
    assert len(fills) == 1
    assert fills[0].side == "BUY"


def test_continuous_down_limits_retain_sell_until_market_reopens():
    panel = {
        "sh603986": _frame(
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
        symbol="sh603986",
        shares=10000,
        avg_cost=10,
        entry_date="2026-01-01",
        tranches=[Tranche("old", "CORE", 10000, 10, "2026-01-01", "2026-01-02", 10)],
    )
    account = AccountState(
        initial_cash=100000,
        cash=0,
        positions={"sh603986": position},
        operating_peak=100000,
        capital_peak=100000,
    )
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh603986", "SELL", 0.0, "exit")
    ]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel) == []
    assert account.pending_orders[0].attempts == 2
    fills = planner.execute_open(date=pd.Timestamp("2026-01-08"), account=account, panel=panel)
    assert len(fills) == 1
    assert fills[0].side == "SELL"
    assert "sh603986" not in account.positions


def test_large_opening_gap_reprices_target_and_preserves_weight_cap():
    panel = {
        "sh603986": _frame(
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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh603986", "BUY", 0.60, "entry")
    ]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    )
    assert len(fills) == 1
    position_value = account.positions["sh603986"].shares * fills[0].price
    post_fill_equity = account.cash + position_value
    assert position_value / post_fill_equity <= DEFAULT_CONFIG.max_symbol_weight + 1e-12
    assert account.cash >= 0


def test_decisive_strategic_owner_can_fill_above_ordinary_symbol_cap() -> None:
    symbol = "sh603986"
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
        _canonical_pending(
            "2026-01-05", symbol, "BUY", 1.0, "dominant strategic owner"
        )
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



from _execution_order_lifecycle_cases import (
    test_fee_formula_is_recomputable,
    test_sellable_shares_are_tranche_based,
    test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200,
    test_sells_release_cash_before_buys,
    test_compatible_blocked_order_survives_daily_replanning,
    test_new_exit_target_cancels_stale_blocked_buy,
    test_zero_weight_sell_is_replaced_when_causal_execution_policy_changes,
    test_partial_risk_sell_survives_a_subthreshold_risk_escalation,
    test_submitted_buy_survives_economically_equivalent_target_drift,
    test_broker_order_ledger_counts_submission_and_replacement_not_fills,
    test_blocked_then_filled_instruction_remains_one_broker_order,
    test_execution_policy_metadata_flows_from_target_to_order_ledger_and_fill,
    test_buy_tranche_uses_the_fill_all_in_unit_cost,
    test_merge_replaces_only_when_same_weight_machine_execution_policy_changes,
    test_merge_replaces_same_weight_order_when_lifecycle_changes,
)

from _execution_risk_and_fill_cases import (
    test_merge_retains_partial_risk_sell_with_nonzero_target_weight,
    test_risk_priority_is_t1_aware_and_survives_partial_fills_across_days,
    test_fifo_exit_keeps_historical_lot_order_and_rebuilds_position,
    test_sell_funded_recovery_handoff_waits_when_incumbent_sale_is_limit_blocked,
)
