from __future__ import annotations

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import _attach_target_attribution
from uquant.execution import plan_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Position,
    Risk,
    RiskAssessment,
    Target,
)


def test_restoration_never_bypasses_the_absolute_minimum_ticket() -> None:
    target = Target(
        "restore",
        0.30,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
    )
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=716_000.0,
        positions={
            "restore": Position(
                "restore",
                shares=284_000,
                avg_cost=1.0,
                entry_date="2026-01-01",
            )
        },
        protected_weights={"restore": 0.30},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )

    planned = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={"restore": 1.0},
        cfg=DEFAULT_CONFIG,
    )

    assert DEFAULT_CONFIG.min_trade_value > 0.30 * 1_000_000.0 - 284_000.0
    assert planned == ()

def test_post_shock_restore_is_buy_only_when_members_drift_apart():
    winner = "sz300308"
    laggard = "sz300502"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=100_000.0,
        positions={
            winner: Position(
                winner,
                shares=6_600,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
            laggard: Position(
                laggard,
                shares=2_400,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={winner: 0.60, laggard: 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={winner: 100.0, laggard: 100.0},
    )
    targets = _attach_target_attribution(signal_date="2026-01-05", targets=targets)
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={winner: 100.0, laggard: 100.0},
        cfg=DEFAULT_CONFIG,
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {winner: 0.60, laggard: 0.30}
    )
    assert [(order.side, order.symbol) for order in planned] == [("BUY", laggard)]

def test_post_shock_restore_labels_required_sells_as_recovery_cohort() -> None:
    """Exercise the real unioned-position path behind a restoration SELL."""
    restored = "sh688233"
    added = "sh688361"
    reduced = "sz300604"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=665_700.0,
        positions={
            reduced: Position(reduced, shares=3_343, avg_cost=100.0),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={restored: 0.34, added: 0.23, reduced: 0.266},
        shock_severity="SEVERE",
        capital_budget_level=1,
        capital_budget_repair_streak=2,
    )
    leaders = {
        symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {})
        for symbol in account.protected_weights
    }
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={"freeze_new_risk": True, "transition_damage": 0.14},
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.protected_weights},
        leaders=leaders,
        account=account,
        prices={symbol: 100.0 for symbol in account.protected_weights},
    )
    targets = _attach_target_attribution(signal_date="2026-01-05", targets=targets)
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={symbol: 100.0 for symbol in account.protected_weights},
        cfg=DEFAULT_CONFIG,
    )

    assert {order.symbol for order in planned if order.side == "BUY"} == {
        restored,
        added,
    }
    sell = next(order for order in planned if order.side == "SELL")
    assert sell.symbol == reduced
    assert sell.origin_subsystem == OriginSubsystem.RECOVERY.value
    assert sell.mechanism == AttributionMechanism.RECOVERY_COHORT.value

def test_small_restore_gap_remains_executable_instead_of_hanging_forever():
    first_symbol = "sz300308"
    second_symbol = "sz300502"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=140_000.0,
        positions={
            first_symbol: Position(
                first_symbol,
                shares=5_600,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
            second_symbol: Position(
                second_symbol,
                shares=3_000,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={first_symbol: 0.60, second_symbol: 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={first_symbol: 100.0, second_symbol: 100.0},
    )
    targets = _attach_target_attribution(signal_date="2026-01-05", targets=targets)
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={first_symbol: 100.0, second_symbol: 100.0},
        cfg=DEFAULT_CONFIG,
    )

    assert account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
    assert {target.reason for target in targets} == {
        "confirmed post-shock restoration",
        "post-shock restoration; retain winner drift",
    }
    assert [(order.side, order.symbol) for order in planned] == [("BUY", first_symbol)]
