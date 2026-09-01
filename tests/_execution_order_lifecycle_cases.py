from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest
from test_execution import (
    _attribution_identity,
    _canonical_pending,
    _frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    fee_components,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.types import (
    ATTRIBUTION_IDENTITY_FIELDS,
    AccountState,
    AttributionMechanism,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Target,
    Tranche,
)


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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh688008", "BUY", 0.60, "entry")
    ]
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    fills = ExecutionPlanner(cfg).execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel)
    assert fills and fills[0].shares >= 200
    assert account.pending_orders and account.pending_orders[0].remaining_shares > 0

def test_sells_release_cash_before_buys():
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
                    "open": 10,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
            ]
        ),
        "sz002371": _frame(
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
        symbol="sh603986",
        shares=10000,
        avg_cost=10,
        entry_date="2026-01-01",
        tranches=[Tranche("old", "CORE", 10000, 10, "2026-01-01", "2026-01-02", 10)],
    )
    account = AccountState(
        initial_cash=100000, cash=0, positions={"sh603986": old}, operating_peak=100000, capital_peak=100000
    )
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sz002371", "BUY", 0.5, "entry"),
        _canonical_pending("2026-01-05", "sh603986", "SELL", 0.0, "exit"),
    ]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    )
    assert [fill.side for fill in fills] == ["SELL", "BUY"]

def test_compatible_blocked_order_survives_daily_replanning():
    retained = PendingOrder("2026-01-05", "sh603986", "BUY", 0.5, "entry", "CORE", attempts=2)
    target = Target("sh603986", 0.5, "CORE", 0.8, 1.0, "mature anchored leader")
    merged = merge_pending_orders(retained=[retained], planned=(), targets=(target,))
    assert merged == (retained,)
    assert merged[0].attempts == 2

def test_new_exit_target_cancels_stale_blocked_buy():
    retained = PendingOrder("2026-01-05", "sh603986", "BUY", 0.5, "entry", "CORE")
    planned = PendingOrder("2026-01-06", "sh603986", "SELL", 0.0, "risk", "CORE")
    target = Target("sh603986", 0.0, "CORE", 0.0, 0.0, "risk")
    merged = merge_pending_orders(retained=[retained], planned=(planned,), targets=(target,))
    assert merged == (planned,)

def test_zero_weight_sell_is_replaced_when_causal_execution_policy_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sh603986",
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
        "sh603986",
        "SELL",
        0.0,
        "lifecycle exit",
        "CORE",
        reason_code="lifecycle_exit",
        exit_kind="lifecycle",
    )
    target = Target(
        "sh603986",
        0.0,
        "CORE",
        0.0,
        0.0,
        "lifecycle exit",
        reason_code="lifecycle_exit",
        exit_kind="lifecycle",
    )

    merged = merge_pending_orders(retained=[retained], planned=(planned,), targets=(target,))

    assert merged == (planned,)

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
    retained = _canonical_pending("2026-01-05", "sh603986", "BUY", 0.5, "entry")
    same = replace(retained, signal_date="2026-01-06", reason="refresh", order_id="")
    retained_identity = {
        field: getattr(retained, field) for field in ATTRIBUTION_IDENTITY_FIELDS
    }
    target = Target(
        "sh603986",
        0.5,
        "CORE",
        0.8,
        1.0,
        "entry",
        **retained_identity,
    )
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

    replacement = _canonical_pending(
        "2026-01-07", "sh603986", "SELL", 0.0, "risk"
    )
    replacement_identity = {
        field: getattr(replacement, field)
        for field in ATTRIBUTION_IDENTITY_FIELDS
    }
    exit_target = Target(
        "sh603986",
        0.0,
        "CORE",
        0.0,
        0.0,
        "risk",
        **replacement_identity,
    )
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
    account.pending_orders = list(replaced)

    panel = {
        "sh603986": _frame(
            [
                {
                    "date": "2026-01-07",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
                {
                    "date": "2026-01-08",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10,
                    "volume": 1e8,
                    "amount": 1e9,
                },
            ]
        )
    }
    assert ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-08"), account=account, panel=panel
    ) == []
    assert account.order_ledger[1].cancel_reason == "target already satisfied"

def test_blocked_then_filled_instruction_remains_one_broker_order():
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
    account.pending_orders = [
        _canonical_pending("2026-01-05", "sh603986", "BUY", 0.5, "entry")
    ]
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    assert planner.execute_open(date=pd.Timestamp("2026-01-06"), account=account, panel=panel) == []
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].last_event == "LIMIT_BLOCKED"
    assert planner.execute_open(date=pd.Timestamp("2026-01-07"), account=account, panel=panel)
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].status == "FILLED"
    assert account.fills[0].order_id == account.order_ledger[0].order_id

def test_execution_policy_metadata_flows_from_target_to_order_ledger_and_fill():
    identity = _attribution_identity(
        signal_date="2026-01-05",
        symbol="sh603986",
        target_weight=0.0,
        origin_subsystem=OriginSubsystem.RISK.value,
        mechanism=AttributionMechanism.RISK_GROSS_CAP.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="portfolio_damage",
        exit_kind="portfolio_risk",
    )
    target = Target(
        "sh603986",
        0.0,
        "CORE",
        0.0,
        0.0,
        "portfolio structural reduction",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="portfolio_damage",
        exit_kind="portfolio_risk",
        **identity,
    )
    position = Position(
        symbol="sh603986",
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
    symbol = "sh603986"
    account = AccountState.empty(2_000_000.0)
    account.pending_orders = [
        _canonical_pending("2026-01-05", symbol, "BUY", 0.50, "entry")
    ]
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

def test_merge_replaces_only_when_same_weight_machine_execution_policy_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sh603986",
        "SELL",
        0.0,
        "exit",
        "CORE",
    )
    planned = PendingOrder(
        "2026-01-06",
        "sh603986",
        "SELL",
        0.0,
        "exit",
        "CORE",
    )
    target = Target("sh603986", 0.0, "CORE", 0.0, 0.0, "exit")
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
        assert merged == (
            replacement
            if set(change) == {"reduction_policy"}
            else retained,
        )

def test_merge_replaces_same_weight_order_when_lifecycle_changes():
    retained = PendingOrder(
        "2026-01-05",
        "sh603986",
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
        "sh603986",
        "BUY",
        0.50,
        "mature leader",
        "CORE",
    )
    target = Target(
        "sh603986",
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
