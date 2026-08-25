from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _frozen_caution,
    _identity,
    _leader,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
    Tranche,
)


@pytest.mark.parametrize(
    "frozen",
    (
        RiskAssessment(
            Risk.CAUTION,
            1.0,
            1,
            {"transition_damage": 0.20},
            ("level-1 capital repair",),
            "NONE",
            freeze_new_risk=True,
            reduction_level=1,
        ),
        RiskAssessment(
            Risk.NORMAL,
            1.0,
            1,
            {"transition_damage": 0.20, "freeze_new_risk": True},
            ("continuous transition damage",),
            "NONE",
            reduction_level=1,
        ),
        RiskAssessment(
            Risk.RISK_OFF,
            0.50,
            3,
            {"transition_damage": 0.20},
            ("risk-off state",),
            "NONE",
            reduction_level=2,
        ),
    ),
    ids=("field", "evidence", "state"),
)
def test_every_freeze_source_persistently_blocks_empty_book_buys(
    frozen: RiskAssessment,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_repair_candidate"
    close = np.ones(len(dates), dtype=float)
    close[-1] = 0.94
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 0.90,
            "ma60": 0.90,
            "ma120": 0.90,
            "ret5": -0.05,
            "ret20": -0.10,
            "ret60": -0.30,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    account = AccountState.empty(100.0)
    account.capital_budget_level = 1
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for _ in range(5):
        targets = allocator.allocate(
            date=dates[-1],
            opportunity=Opportunity.CHOPPY,
            risk=frozen,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, 0.90)},
            account=account,
            prices={symbol: 0.94},
        )

        assert targets == ()
        assert (
            plan_orders(
                signal_date=str(dates[-1].date()),
                targets=targets,
                account=account,
                prices={symbol: 0.94},
                cfg=DEFAULT_CONFIG,
            )
            == ()
        )
        assert account.candidate_tenure.get("tactical_active", 0) == 0

def test_frozen_strategic_member_preserves_partial_sell_identity_and_cancels_buy():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    selling, buying = "strategic_sell", "strategic_buy"
    durable_sell = PendingOrder(
        "2026-01-05",
        selling,
        "SELL",
        0.10,
        "portfolio risk gross cap",
        Lifecycle.CORE.value,
        remaining_shares=10,
        order_id="O000000101",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    unfinished_buy = PendingOrder(
        "2026-01-05",
        buying,
        "BUY",
        0.30,
        "strategic cohort entry",
        Lifecycle.CORE.value,
        remaining_shares=10,
        order_id="O000000102",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=60.0,
        positions={
            selling: Position(selling, shares=20, avg_cost=1.0),
            buying: Position(buying, shares=20, avg_cost=1.0),
        },
        pending_orders=[durable_sell, unfinished_buy],
        strategic_cohort_symbols=[selling, buying],
        strategic_cohort_targets={selling: 0.30, buying: 0.30},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    frozen = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"freeze_new_risk": True},
        (),
        "RECOVERY",
        freeze_new_risk=True,
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen,
        user_panel={selling: frame, buying: frame},
        leaders={selling: _leader(selling, 0.90), buying: _leader(buying, 0.89)},
        account=account,
        prices={selling: 1.0, buying: 1.0},
    )
    by_symbol = {target.symbol: target for target in targets}
    assert by_symbol[selling] == Target(
        selling,
        0.10,
        Lifecycle.CORE.value,
        0.0,
        0.0,
        "portfolio risk gross cap",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    assert by_symbol[buying].weight == pytest.approx(0.20)
    assert by_symbol[buying].reason_code == "risk_freeze_hold"
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=targets,
        account=account,
        prices={selling: 1.0, buying: 1.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=list(account.pending_orders),
        planned=planned,
        targets=targets,
    )
    assert merged == (durable_sell,)
    assert merged[0].order_id == "O000000101"
    assert merged[0].remaining_shares == 10

def test_partial_fill_direction_survives_real_daily_execute_replan_cycle():
    symbol = "sz000001"
    dates = pd.to_datetime(("2026-01-05", "2026-01-06", "2026-01-07"))
    sell_frame = pd.DataFrame(
        {
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 20_000.0,
            "amount": 200_000.0,
        },
        index=dates,
    )
    sell_order = PendingOrder(
        "2026-01-05",
        symbol,
        "SELL",
        0.30,
        "portfolio risk gross cap",
        Lifecycle.CORE.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
        **_identity(
            signal_date="2026-01-05",
            symbol=symbol,
            target_weight=0.30,
            lifecycle=Lifecycle.CORE.value,
            origin_subsystem=OriginSubsystem.RISK.value,
            mechanism=AttributionMechanism.RISK_GROSS_CAP.value,
            reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
            reason_code="risk_gross_cap",
            exit_kind="risk",
        ),
    )
    selling = AccountState(
        initial_cash=10_000.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=1_000,
                avg_cost=10.0,
                entry_date="2026-01-02",
                highest_close=10.0,
                tranches=[
                    Tranche(
                        "core",
                        Lifecycle.CORE.value,
                        1_000,
                        10.0,
                        "2026-01-02",
                        "2026-01-05",
                        10.0,
                        lowest_close=10.0,
                    )
                ],
            )
        },
        pending_orders=[sell_order],
        operating_peak=10_000.0,
        capital_peak=10_000.0,
    )
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    first_sell = planner.execute_open(
        date=dates[1],
        account=selling,
        panel={symbol: sell_frame},
    )
    assert len(first_sell) == 1
    assert first_sell[0].shares == 100
    ledger = selling.order_ledger[0]
    assert (ledger.requested_shares, ledger.filled_shares, ledger.remaining_shares) == (
        700,
        100,
        600,
    )

    previous_sells = list(selling.pending_orders)
    sell_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[1],
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: _leader(symbol, 0.90)},
        account=selling,
        prices={symbol: 10.0},
    )
    planned_sells = plan_orders(
        signal_date="2026-01-06",
        targets=sell_targets,
        account=selling,
        prices={symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    current_sells = merge_pending_orders(
        retained=previous_sells,
        planned=planned_sells,
        targets=sell_targets,
    )
    selling.pending_orders = list(
        reconcile_account_orders(
            account=selling,
            previous=previous_sells,
            current=current_sells,
            submitted_date="2026-01-06",
        )
    )
    assert selling.pending_orders[0].order_id == first_sell[0].order_id

    second_sell = planner.execute_open(
        date=dates[2],
        account=selling,
        panel={symbol: sell_frame},
    )
    assert second_sell[0].order_id == first_sell[0].order_id
    assert second_sell[0].shares == 100
    assert (ledger.requested_shares, ledger.filled_shares, ledger.remaining_shares) == (
        700,
        200,
        500,
    )

    star = "sh688008"
    buy_frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100_000.0,
            "amount": 10_000_000.0,
        },
        index=dates,
    )
    buying = AccountState.empty(1_000_000.0)
    buying.pending_orders = [
        PendingOrder(
            "2026-01-05",
            star,
            "BUY",
            0.60,
            "leader add",
            Lifecycle.CORE.value,
            **_identity(
                signal_date="2026-01-05",
                symbol=star,
                target_weight=0.60,
                lifecycle=Lifecycle.CORE.value,
                origin_subsystem=OriginSubsystem.LEADER.value,
                mechanism=AttributionMechanism.LEADER_SELECTION.value,
            ),
        )
    ]
    buy_planner = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=0.002))
    first_buy = buy_planner.execute_open(
        date=dates[1],
        account=buying,
        panel={star: buy_frame},
    )
    assert first_buy[0].shares == 200
    buy_ledger = buying.order_ledger[0]
    assert (buy_ledger.requested_shares, buy_ledger.filled_shares, buy_ledger.remaining_shares) == (
        5_900,
        200,
        5_700,
    )

    previous_buys = list(buying.pending_orders)
    buy_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[1],
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={star: _leader(star, 0.90)},
        account=buying,
        prices={star: 100.0},
    )
    planned_buys = plan_orders(
        signal_date="2026-01-06",
        targets=buy_targets,
        account=buying,
        prices={star: 100.0},
        cfg=DEFAULT_CONFIG,
    )
    current_buys = merge_pending_orders(
        retained=previous_buys,
        planned=planned_buys,
        targets=buy_targets,
    )
    buying.pending_orders = list(
        reconcile_account_orders(
            account=buying,
            previous=previous_buys,
            current=current_buys,
            submitted_date="2026-01-06",
        )
    )
    assert buying.pending_orders == []
    assert buy_ledger.status == "CANCELLED"
    assert (buy_ledger.requested_shares, buy_ledger.filled_shares, buy_ledger.remaining_shares) == (
        5_900,
        200,
        5_700,
    )

def test_active_strategic_cohort_does_not_start_missing_buys_while_frozen():
    date = pd.Timestamp("2026-01-06")
    symbol = "unfilled_strategic_member"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=1_000_000.0,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        candidate_tenure={"strategic_cohort_active": 1},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert targets == ()
    assert account.strategic_cohort_targets == {symbol: 0.30}
    assert account.candidate_tenure.get("strategic_cohort_active") == 1
