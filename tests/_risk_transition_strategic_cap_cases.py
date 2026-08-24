from __future__ import annotations

import pandas as pd
import pytest
from test_risk_transitions import (
    _assess,
    _damaged_holding_frame,
    _isolated_risk_config,
    _leader,
    _market_frame,
    _reference_context,
)

import uquant.risk as risk_module
from uquant.config import DEFAULT_CONFIG
from uquant.risk import (
    assess_risk,
)
from uquant.risk_sector import SectorGuardTransition, SectorObservation
from uquant.types import AccountState, Position, Risk, RiskAssessment


def test_strategic_label_cannot_bypass_confirmed_capital_budget_damage() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    symbols = ("strategic_a", "strategic_b", "strategic_c")
    damaged = _damaged_holding_frame(dates)
    account = AccountState(
        initial_cash=260.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[0].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=225.0,
        capital_peak=260.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        sector_guard_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "damaged"},
        account=account,
        cfg=cfg,
        user_panel={symbol: damaged for symbol in symbols},
        equity=225.0,
    )

    assert account.capital_budget_level >= 2
    assert assessment.target_gross_cap <= cfg.capital_budget_level2_cap
    assert assessment.reduction_level >= 2

def test_mature_strategic_cohort_break_uses_concentrated_cohort_severity() -> None:
    dates = pd.bdate_range("2026-01-02", periods=160)
    symbols = ("strategic_a", "strategic_b", "strategic_c")
    damaged = _damaged_holding_frame(dates)
    account = AccountState(
        initial_cash=300.0,
        cash=15.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[0].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": DEFAULT_CONFIG.strategic_cohort_guard_days,
        },
        operating_peak=300.0,
        capital_peak=300.0,
    )
    cfg = _isolated_risk_config()

    assessment: RiskAssessment | None = None
    for date in dates[-cfg.concentrated_break_confirm_days :]:
        assessment = _assess(
            date=date,
            dates=dates,
            states={date: "healthy"},
            account=account,
            cfg=cfg,
            user_panel={symbol: damaged for symbol in symbols},
            equity=240.0,
        )

    assert assessment is not None
    assert assessment.state is Risk.CRISIS
    assert assessment.severity == "COHORT_BREAK"
    assert account.shock_severity == "COHORT_BREAK"
    assert assessment.target_gross_cap == pytest.approx(cfg.concentrated_crisis_gross)
    assert assessment.reasons == ("confirmed dynamic cohort structural break",)

def test_chronic_overlay_cap_is_a_hard_minimum_on_fast_recovery_path() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    held = _market_frame(dates)
    account = AccountState.empty(100.0)
    account.cash = 50.0
    account.positions = {
        "held": Position(
            "held",
            shares=1,
            avg_cost=100.0,
            entry_date=str(dates[-30].date()),
            highest_close=110.0,
        )
    }
    account.risk = Risk.CRISIS.value
    account.shock_state = "PERSISTENT_STRESS"
    account.shock_severity = "MARKET"
    account.shock_start_date = str(dates[-10].date())
    account.protected_weights = {"held": 0.50}
    account.risk_streaks["independent_market_repair"] = DEFAULT_CONFIG.fast_v_recovery_confirm_days - 1
    account.chronic_level = 3
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        capital_budget_ladder_enabled=False,
        sector_guard_enabled=False,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
        user_panel={"held": held},
    )

    assert assessment.state is Risk.CAUTION
    assert assessment.shock_state == "FAST_V_RECOVERY"
    assert assessment.target_gross_cap == pytest.approx(cfg.chronic_severe_cap)
    assert assessment.target_gross_cap < cfg.fast_v_recovery_gross
    assert account.protected_weights == {"held": 0.50}
    assert account.shock_severity == "MARKET"
    assert account.operating_peak == pytest.approx(100.0)

def test_confirmed_acute_sector_evacuation_preempts_concentrated_crisis_cap() -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    first_shock, second_shock = dates[-2:]
    held = _market_frame(dates)
    held.loc[first_shock, ["close", "ma20", "ret5"]] = (95.0, 100.0, -0.10)
    held.loc[second_shock, ["close", "ma20", "ret5"]] = (85.54, 100.0, -0.13)
    broad = _market_frame(dates)
    tech = _market_frame(dates)
    broad["ret120"] = 0.0
    tech["ret120"] = 0.60
    reference_panel, reference_leaders, reference_returns = _reference_context(dates)
    symbols = ("held_a", "held_b", "held_c")
    account = AccountState(
        initial_cash=30_000.0,
        cash=0.0,
        positions={
            symbol: Position(symbol, shares=100, avg_cost=100.0)
            for symbol in symbols
        },
        operating_peak=30_000.0,
        capital_peak=30_000.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
        sector_recovery_ma=3,
    )

    assessments = [
            assess_risk(
                date=date,
                broad=broad,
                tech=tech,
                reference_panel=reference_panel,
                reference_returns=reference_returns,
                user_panel={symbol: held for symbol in symbols},
                leaders={
                    **reference_leaders,
                    **{symbol: _leader(symbol) for symbol in symbols},
                },
                account=account,
                equity=sum(
                    account.positions[symbol].shares * float(held.loc[date, "close"])
                    for symbol in symbols
                ),
                cfg=cfg,
            )
        for date in (first_shock, second_shock)
    ]

    assert account.sector_guard_active
    assert assessments[0].target_gross_cap == 0.0
    assert assessments[0].evidence["acute_sector_evacuation"] is True
    assert assessments[0].evidence["sector_guard_active"] is True

def test_first_full_book_fast_shock_triggers_acute_evacuation() -> None:
    transition = SectorGuardTransition(
        active=False,
        triggered=False,
        recovered=False,
        shock=True,
        shock_count=1,
        active_sessions=0,
        observation=SectorObservation(
            symbol_count=3,
            equal_return=-0.05,
            weighted_return=-0.05,
            positive_breadth=0.0,
            negative_exposure=1.0,
            recovery_breadth=1.0,
        ),
    )

    assert risk_module._acute_sector_evacuation_required(
        transition,
        DEFAULT_CONFIG,
        leadership_divergence=DEFAULT_CONFIG.sector_guard_divergence,
    )

def test_single_live_holding_fast_shock_uses_same_acute_evacuation_owner() -> None:
    transition = SectorGuardTransition(
        active=False,
        triggered=False,
        recovered=False,
        shock=False,
        shock_count=0,
        active_sessions=0,
        observation=None,
    )
    observation = SectorObservation(
        symbol_count=1,
        equal_return=-0.055,
        weighted_return=-0.055,
        positive_breadth=0.0,
        negative_exposure=1.0,
        recovery_breadth=0.0,
    )

    assert risk_module._acute_sector_evacuation_required(
        transition,
        DEFAULT_CONFIG,
        leadership_divergence=DEFAULT_CONFIG.sector_guard_divergence,
        single_holding_observation=observation,
        single_holding_is_leader=True,
    )
    assert not risk_module._acute_sector_evacuation_required(
        transition,
        DEFAULT_CONFIG,
        leadership_divergence=DEFAULT_CONFIG.sector_guard_divergence,
        single_holding_observation=observation,
        single_holding_is_leader=False,
    )
