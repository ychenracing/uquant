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
    merge_pending_orders,
    plan_orders,
)
from uquant.types import (
    AccountState,
    AttributionMechanism,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Target,
    Tranche,
)


def test_merge_retains_partial_risk_sell_with_nonzero_target_weight():
    retained = PendingOrder(
        "2026-01-05",
        "sh603986",
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
        "sh603986",
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
    symbol = "sh603986"
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
    order = _canonical_pending(
        "2026-01-05",
        symbol,
        "SELL",
        0.0,
        "crisis reduction",
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
    symbol = "sh603986"
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
        pending_orders=[
            _canonical_pending("2026-01-05", symbol, "SELL", 0.0, "exit")
        ],
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
    old_symbol = "sz300308"
    new_symbol = "sz300502"
    incumbent = Position(
        symbol=old_symbol,
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
    old_identity = _attribution_identity(
        signal_date=signal_date,
        symbol=old_symbol,
        target_weight=0.0,
        origin_subsystem=OriginSubsystem.RECOVERY.value,
        mechanism=AttributionMechanism.RECOVERY_COHORT.value,
    )
    new_identity = _attribution_identity(
        signal_date=signal_date,
        symbol=new_symbol,
        target_weight=0.40,
        origin_subsystem=OriginSubsystem.RECOVERY.value,
        mechanism=AttributionMechanism.RECOVERY_COHORT.value,
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
        Target(
            old_symbol,
            0.0,
            "CORE",
            0.0,
            0.0,
            "recovery cohort construction",
            **old_identity,
        ),
        Target(
            new_symbol,
            0.40,
            "CORE",
            0.9,
            1.0,
            "recovery cohort construction",
            **new_identity,
        ),
    )
    account.pending_orders = list(
        plan_orders(
            signal_date=signal_date,
            targets=targets,
            account=account,
            prices={old_symbol: 100.0, new_symbol: 100.0},
            cfg=DEFAULT_CONFIG,
        )
    )
    panel = {
        old_symbol: _frame(
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
                        "open": 80.0,
                        "high": 80.0,
                        "low": 80.0,
                        "close": 80.0,
                    "volume": 1_000_000,
                        "amount": 80_000_000,
                },
                {
                    "date": release_date,
                        "open": 80.0,
                        "high": 81.0,
                        "low": 79.0,
                        "close": 80.0,
                    "volume": 1_000_000,
                        "amount": 80_000_000,
                },
            ]
        ),
        new_symbol: _frame(
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
    assert account.positions[old_symbol].shares == 4_000
    assert new_symbol not in account.positions

    released = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp(release_date),
        account=account,
        panel=panel,
    )
    assert {fill.side for fill in released} == {"SELL", "BUY"}
    assert old_symbol not in account.positions
    equity = account.cash + account.positions[new_symbol].shares * 100.0
    gross = account.positions[new_symbol].shares * 100.0 / equity
    assert gross <= 0.40 + 1e-12
