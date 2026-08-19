from __future__ import annotations

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
)


def _sentinel_risk(cap: float) -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=cap,
        votes=0,
        evidence={
            "sentinel_cap": cap,
            "sentinel_cap_binding": True,
            "sentinel_effective_level": "DEFENSIVE",
        },
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=True,
        reduction_level=0,
        severity="NORMAL",
    )


def test_sentinel_cap_reuses_risk_priority_and_stable_risk_mechanism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = "sz300308"
    account = AccountState(
        initial_cash=100.0,
        cash=5.0,
        positions={symbol: Position(symbol, shares=95, avg_cost=1.0)},
        strategic_epoch=1,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.95},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    strategy_target = Target(
        symbol=symbol,
        weight=0.95,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=1.0,
        confidence=1.0,
        reason="healthy strategic incumbent",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (strategy_target,),
    )

    reduced = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.TREND,
        risk=_sentinel_risk(0.70),
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )

    assert sum(item.weight for item in reduced) == pytest.approx(0.70)
    assert reduced[0].reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert reduced[0].reason_code == "sentinel_gross_cap"
    assert reduced[0].exit_kind == "portfolio_risk"
    assert reduced[0].origin_subsystem == OriginSubsystem.RISK.value
    assert reduced[0].mechanism == AttributionMechanism.RISK_GROSS_CAP.value


def test_nonbinding_sentinel_candidate_keeps_base_reduction_owner() -> None:
    risk = RiskAssessment(
        state=Risk.RISK_OFF,
        target_gross_cap=0.40,
        votes=0,
        evidence={
            "sentinel_cap": 0.70,
            "sentinel_cap_binding": False,
        },
        reasons=(),
        shock_state="NONE",
    )

    assert PortfolioAllocator._risk_reduction_metadata(risk) == (
        "portfolio risk-off gross cap",
        "risk_off",
        "risk_off",
    )
