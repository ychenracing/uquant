"""Sector-transition risk ownership."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..risk_sector import (
    SectorGuardTransition,
    SectorObservation,
    observe_deployed_sector,
)
from ..types import AccountState, Risk, RiskAssessment
from .confirmed_break import assess_confirmed_concentrated_break
from .recovery_state import reset_recovery_owner_rearm as _reset_recovery_owner_rearm
from .transition_resolution import RiskTransitionResolution, resolve_risk_transition


@dataclass(frozen=True, slots=True)
class BreakConditions:
    """Read-only outputs from the existing break-confirmation input slice."""

    shock_rearmed: bool
    concentrated_structure_break: bool
    emergency_tail_break: bool
    narrow_anchor_guard: bool
    immediate_severe_break: bool
    persistent_market_break: bool
    strategic_active: bool
    recovery_anchor_elapsed: int
    held_cohort_break_confirmed: bool
    strategic_current_gross: float
    strategic_tail_break: bool


def _acute_sector_evacuation_required(
    transition: SectorGuardTransition,
    cfg: SystemConfig,
    *,
    leadership_divergence: float,
    single_holding_observation: SectorObservation | None = None,
    single_holding_is_leader: bool = False,
) -> bool:
    """Identify a newly confirmed, full-book fast collapse.

    An ordinary synchronized sector break keeps the reviewed 40% gross cap.
    Evacuation is reserved for the first observed session where both
    equal-weight and economic-weight losses cross the existing fast-risk line
    and almost all deployed capital is losing while the technology leadership
    premium independently exceeds the existing sector-guard boundary.  Waiting for the ordinary
    two-shock sector confirmation repeats the same evidence and exposes the
    entire book to a second gap before the next-open order can execute.
    No later outcome or universe identity enters.
    """
    observation = transition.observation or single_holding_observation
    single_holding_systemic_shock = bool(
        observation is not None
        and observation.symbol_count == 1
        # A single name cannot establish breadth. It may use the existing
        # first-shock owner only while it remains the structural leader of the
        # already-confirmed technology premium; this rejects an idiosyncratic
        # laggard gap while protecting a concentrated winning book.
        and single_holding_is_leader
        and observation.equal_return <= cfg.risk_fast_return
        and observation.positive_breadth == 0.0
    )
    return bool(
        observation is not None
        and (transition.shock or single_holding_systemic_shock)
        and leadership_divergence >= cfg.sector_guard_divergence
        and observation.equal_return <= cfg.risk_fast_return
        and observation.weighted_return <= cfg.risk_fast_return
        and observation.negative_exposure >= cfg.sector_weighted_negative_exposure
    )


def _shock_rearmed(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    cfg: SystemConfig,
) -> bool:
    if not account.last_shock_date or not user_panel:
        return True
    rearm_days = (
        cfg.incomplete_universe_rearm_days
        if account.candidate_tenure.get("last_shock_incomplete_universe", 0) == 1
        else cfg.shock_rearm_days
    )
    rearmed = len(tech.loc[pd.Timestamp(account.last_shock_date) : date]) - 1 >= rearm_days
    if account.positions and all(
        position.entry_date and pd.Timestamp(position.entry_date) > pd.Timestamp(account.last_shock_date)
        for position in account.positions.values()
        if position.shares > 0
    ):
        return True
    return rearmed


def _cohort_break_state(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    account: AccountState,
    cfg: SystemConfig,
    shock_rearmed: bool,
    held_damage: list[bool],
    held_damage_ratio: float,
    operating_dd: float,
    votes: int,
    sector_stress: float,
) -> tuple[bool, int, bool]:
    strategic_active = account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    recovery_anchor_elapsed = 0
    if account.recovery_anchor_date:
        recovery_anchor_elapsed = len(tech.loc[pd.Timestamp(account.recovery_anchor_date) : date]) - 1
    mature_live_cohort = bool(
        (
            strategic_active
            and account.candidate_tenure.get("strategic_cohort_days", 0) >= cfg.strategic_cohort_guard_days
        )
        or (account.anchor_weights and recovery_anchor_elapsed >= cfg.recovery_cohort_tail_guard_days)
    )
    synchronized = bool(
        shock_rearmed
        and not account.protected_weights
        and mature_live_cohort
        and len(held_damage) >= 2
        and held_damage_ratio >= 1.0 - 1e-12
        and operating_dd
        >= (cfg.strategic_cohort_tail_line if strategic_active else cfg.recovery_cohort_tail_line)
        and account.risk_streaks["concentrated_break"] >= cfg.concentrated_break_confirm_days
    )
    partial = _market_backed_partial_cohort_break(
        account=account,
        cfg=cfg,
        shock_rearmed=shock_rearmed,
        strategic_active=strategic_active,
        mature_live_cohort=mature_live_cohort,
        held_damage=held_damage,
        operating_dd=operating_dd,
        votes=votes,
        sector_stress=sector_stress,
    )
    return strategic_active, recovery_anchor_elapsed, bool(synchronized or partial)


def _market_backed_partial_cohort_break(
    *,
    account: AccountState,
    cfg: SystemConfig,
    shock_rearmed: bool,
    strategic_active: bool,
    mature_live_cohort: bool,
    held_damage: list[bool],
    operating_dd: float,
    votes: int,
    sector_stress: float,
) -> bool:
    observed = bool(
        shock_rearmed
        and not account.protected_weights
        and not strategic_active
        and account.anchor_weights
        and mature_live_cohort
        and len(held_damage) >= 2
        and sum(held_damage) >= 2
        and operating_dd >= cfg.concentrated_break_dd
        and votes >= 3
        and sector_stress >= 0.50
    )
    key = "market_backed_recovery_break"
    account.risk_streaks[key] = account.risk_streaks.get(key, 0) + 1 if observed else 0
    return account.risk_streaks[key] >= cfg.concentrated_break_confirm_days


def _strategic_tail_state(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    strategic_active: bool,
    operating_dd: float,
    votes: int,
    sector_stress: float,
    transition_damage: float,
) -> tuple[float, bool]:
    current_gross = sum(
        position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
        for symbol, position in account.positions.items()
        if symbol in account.strategic_cohort_symbols
        and symbol in user_panel
        and date in user_panel[symbol].index
        and position.shares > 0
    )
    observed = bool(
        strategic_active
        and account.candidate_tenure.get("strategic_cohort_days", 0) >= cfg.strategic_cohort_guard_days
        and operating_dd >= cfg.strategic_cohort_tail_line
    )
    key = "strategic_tail_break"
    account.risk_streaks[key] = account.risk_streaks.get(key, 0) + 1 if observed else 0
    confirmed = (
        observed
        and account.risk_streaks[key] >= cfg.strategic_cohort_tail_confirm_days
        and votes >= 4
        and sector_stress >= 0.50
        and transition_damage >= cfg.transition_damage_freeze
    )
    return current_gross, confirmed


def _assess_break_conditions(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    held_damage: list[bool],
    held_damage_ratio: float,
    held_ret5: list[float],
    operating_dd: float,
    votes: int,
    sector_stress: float,
    transition_damage: float,
    market_context: dict[str, float],
) -> BreakConditions:
    """Run the existing shock-rearm and cohort-break derivation in order."""

    shock_rearmed = _shock_rearmed(
        date=date,
        tech=tech,
        user_panel=user_panel,
        account=account,
        cfg=cfg,
    )
    concentrated_structure_break = (
        len(held_damage) >= 1
        and operating_dd >= cfg.concentrated_break_dd
        and held_damage_ratio >= cfg.concentrated_break_ratio
    )
    emergency_tail_break = (
        any(held_damage) and operating_dd >= cfg.portfolio_break_dd and votes >= cfg.portfolio_break_votes
    )
    concentrated_break = shock_rearmed and not account.protected_weights and concentrated_structure_break
    break_key = "concentrated_break"
    account.risk_streaks[break_key] = account.risk_streaks.get(break_key, 0) + 1 if concentrated_break else 0
    narrow_structure = (
        shock_rearmed
        and not account.protected_weights
        and bool(account.anchor_weights)
        and len(held_damage) >= 2
        and sum(held_damage) >= 2
        and operating_dd >= cfg.concentrated_break_dd
    )
    narrow_anchor_guard = (
        narrow_structure
        and market_context["tech_ret120"] - market_context["broad_ret120"] >= cfg.narrow_anchor_divergence
    )
    immediate_severe_break = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
    persistent_market_break = (
        concentrated_structure_break
        and account.risk_streaks[break_key] >= cfg.concentrated_break_confirm_days
        and (votes >= 3 or (bool(held_ret5) and float(np.mean(held_ret5)) <= -0.08))
    )
    strategic_active, recovery_anchor_elapsed, held_cohort_break_confirmed = _cohort_break_state(
        date=date,
        tech=tech,
        account=account,
        cfg=cfg,
        shock_rearmed=shock_rearmed,
        held_damage=held_damage,
        held_damage_ratio=held_damage_ratio,
        operating_dd=operating_dd,
        votes=votes,
        sector_stress=sector_stress,
    )
    strategic_current_gross, strategic_tail_break = _strategic_tail_state(
        date=date,
        user_panel=user_panel,
        account=account,
        equity=equity,
        cfg=cfg,
        strategic_active=strategic_active,
        operating_dd=operating_dd,
        votes=votes,
        sector_stress=sector_stress,
        transition_damage=transition_damage,
    )
    return BreakConditions(
        shock_rearmed=shock_rearmed,
        concentrated_structure_break=concentrated_structure_break,
        emergency_tail_break=emergency_tail_break,
        narrow_anchor_guard=narrow_anchor_guard,
        immediate_severe_break=immediate_severe_break,
        persistent_market_break=persistent_market_break,
        strategic_active=strategic_active,
        recovery_anchor_elapsed=recovery_anchor_elapsed,
        held_cohort_break_confirmed=held_cohort_break_confirmed,
        strategic_current_gross=strategic_current_gross,
        strategic_tail_break=strategic_tail_break,
    )


@dataclass(slots=True)
class _AcuteContext:
    date: pd.Timestamp
    user_panel: dict[str, pd.DataFrame]
    account: AccountState
    equity: float
    cfg: SystemConfig
    market_context: dict[str, float]
    sector_guard: SectorGuardTransition
    concentrated_confirmed: bool
    held_ret5: list[float]
    votes: int
    continuous_evidence: dict[str, object]
    average_fast: float
    declining: float
    below: float
    sector_stress: float
    correlation: float
    vol_ratio: float
    leader_failure: float
    held_damage_ratio: float
    held_loss_ratio: float
    held_repair_ratio: float
    tech_speed: float
    broad_speed: float
    operating_dd: float
    capital_dd: float
    strategic_active: bool
    strategic_current_gross: float


def _acute_trigger_state(
    ctx: _AcuteContext,
) -> tuple[Risk, bool, SectorObservation | None]:
    previous = Risk(ctx.account.risk)
    live_symbols = {symbol for symbol, position in ctx.account.positions.items() if position.shares > 0}
    single_observation = (
        observe_deployed_sector(
            date=ctx.date,
            panel=ctx.user_panel,
            symbols=live_symbols,
            cfg=ctx.cfg,
            minimum_symbols=1,
        )
        if len(live_symbols) == 1
        else None
    )
    single_is_leader = bool(
        len(live_symbols) == 1
        and all(
            symbol in ctx.user_panel
            and ctx.date in ctx.user_panel[symbol].index
            and scalar(ctx.user_panel[symbol].loc[ctx.date], "ret120", -1.0)
            >= ctx.market_context["tech_ret120"]
            for symbol in live_symbols
        )
    )
    acute = bool(
        _acute_sector_evacuation_required(
            ctx.sector_guard,
            ctx.cfg,
            leadership_divergence=(ctx.market_context["tech_ret120"] - ctx.market_context["broad_ret120"]),
            single_holding_observation=single_observation,
            single_holding_is_leader=single_is_leader,
        )
        and (ctx.sector_guard.triggered or not ctx.concentrated_confirmed)
    )
    return previous, acute, single_observation


def _acute_evidence(
    ctx: _AcuteContext,
    *,
    observation: SectorObservation | None,
) -> dict[str, object]:
    return {
        **ctx.continuous_evidence,
        **ctx.market_context,
        "ai_fast_return": ctx.average_fast,
        "declining_ratio": ctx.declining,
        "below_ma20_ratio": ctx.below,
        "sector_stress_ratio": ctx.sector_stress,
        "median_correlation": ctx.correlation,
        "volatility_ratio": ctx.vol_ratio,
        "leader_failure_ratio": ctx.leader_failure,
        "held_damage_ratio": ctx.held_damage_ratio,
        "held_loss_ratio": ctx.held_loss_ratio,
        "held_repair_ratio": ctx.held_repair_ratio,
        "tech_speed": ctx.tech_speed,
        "broad_speed": ctx.broad_speed,
        "operating_drawdown": ctx.operating_dd,
        "capital_drawdown": ctx.capital_dd,
        "strategic_cohort_active": ctx.strategic_active,
        "strategic_current_gross": ctx.strategic_current_gross,
        "sector_guard_active": ctx.account.sector_guard_active,
        "acute_sector_evacuation": True,
        "sector_guard_shock_count": ctx.sector_guard.shock_count,
        "sector_guard_active_sessions": ctx.sector_guard.active_sessions,
        "sector_guard_equal_return": (observation.equal_return if observation is not None else None),
        "sector_guard_weighted_return": (observation.weighted_return if observation is not None else None),
        "sector_guard_negative_exposure": (
            observation.negative_exposure if observation is not None else None
        ),
    }


def _acute_evacuation_assessment(
    ctx: _AcuteContext,
    *,
    previous: Risk,
    single_observation: SectorObservation | None,
) -> RiskAssessment:
    account = ctx.account
    if not account.sector_guard_active:
        account.sector_guard_active = True
        account.sector_guard_started = str(ctx.date.date())
        account.sector_guard_symbols = sorted(
            symbol for symbol, position in account.positions.items() if position.shares > 0
        )
        account.sector_recovery_streak = 0
    _reset_recovery_owner_rearm(account)
    if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1:
        account.protected_weights.clear()
    account.candidate_tenure["post_shock_restore_complete"] = 0
    if not account.protected_weights:
        account.protected_weights = dict(account.anchor_weights)
    if not account.protected_weights:
        account.protected_weights = {
            symbol: position.shares * scalar(ctx.user_panel[symbol].loc[ctx.date], "close") / ctx.equity
            for symbol, position in account.positions.items()
            if symbol in ctx.user_panel and ctx.date in ctx.user_panel[symbol].index and position.shares > 0
        }
    account.shock_start_date = str(ctx.date.date())
    account.last_shock_date = str(ctx.date.date())
    account.candidate_tenure["acute_sector_evacuation"] = 1
    state = Risk.CRISIS if previous is Risk.CRISIS or ctx.concentrated_confirmed else Risk.RISK_OFF
    if state is Risk.CRISIS and previous is not Risk.CRISIS:
        severe = bool(ctx.held_ret5) and float(np.mean(ctx.held_ret5)) <= ctx.cfg.severe_shock_ret5
        account.shock_severity = "SEVERE" if severe and ctx.votes >= 4 else "CONCENTRATED"
    shock = (
        account.shock_state
        if previous is Risk.CRISIS
        else "SHOCK"
        if state is Risk.CRISIS
        else "SECTOR_GUARD"
    )
    account.risk = state.value
    account.shock_state = shock
    account.risk_streaks["concentrated_repair"] = 0
    account.risk_events.append(
        {
            "date": str(ctx.date.date()),
            "from": previous.value,
            "to": state.value,
            "votes": ctx.votes,
            "reasons": ["confirmed acute holdings collapse"],
            "severity": account.shock_severity,
            "route": "sector_guard_acute",
            "target_gross_cap": 0.0,
        }
    )
    observation = ctx.sector_guard.observation or single_observation
    return RiskAssessment(
        state=state,
        target_gross_cap=0.0,
        votes=ctx.votes,
        evidence=_acute_evidence(ctx, observation=observation),
        reasons=("confirmed acute holdings collapse",),
        shock_state=account.shock_state,
        freeze_new_risk=True,
        reduction_level=3,
        severity=account.shock_severity,
    )


def _capital_cooldown_assessment(ctx: _AcuteContext) -> RiskAssessment:
    cooldown = ctx.account.candidate_tenure.get("capital_guard_cooldown", 0)
    ctx.account.candidate_tenure["capital_guard_cooldown"] = cooldown - 1
    ctx.account.risk = Risk.CRISIS.value
    ctx.account.shock_state = "CAPITAL_GUARD_COOLDOWN"
    evidence = _acute_evidence(ctx, observation=None)
    for key in (
        "strategic_cohort_active",
        "strategic_current_gross",
        "sector_guard_active",
        "acute_sector_evacuation",
        "sector_guard_shock_count",
        "sector_guard_active_sessions",
        "sector_guard_equal_return",
        "sector_guard_weighted_return",
        "sector_guard_negative_exposure",
    ):
        evidence.pop(key)
    return RiskAssessment(
        state=Risk.CRISIS,
        target_gross_cap=0.0,
        votes=ctx.votes,
        evidence=evidence,
        reasons=("capital guard cooldown after failed restoration",),
        shock_state="CAPITAL_GUARD_COOLDOWN",
        freeze_new_risk=True,
        reduction_level=3,
        severity="SEVERE",
    )


def _assess_acute_and_cooldown(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    market_context: dict[str, float],
    sector_guard: SectorGuardTransition,
    concentrated_confirmed: bool,
    held_ret5: list[float],
    votes: int,
    continuous_evidence: dict[str, object],
    average_fast: float,
    declining: float,
    below: float,
    sector_stress: float,
    correlation: float,
    vol_ratio: float,
    leader_failure: float,
    held_damage_ratio: float,
    held_loss_ratio: float,
    held_repair_ratio: float,
    tech_speed: float,
    broad_speed: float,
    operating_dd: float,
    capital_dd: float,
    strategic_active: bool,
    strategic_current_gross: float,
) -> RiskAssessment | tuple[Risk, bool]:
    """Run acute evacuation and capital-cooldown short circuits in order."""

    ctx = _AcuteContext(
        date=date,
        user_panel=user_panel,
        account=account,
        equity=equity,
        cfg=cfg,
        market_context=market_context,
        sector_guard=sector_guard,
        concentrated_confirmed=concentrated_confirmed,
        held_ret5=held_ret5,
        votes=votes,
        continuous_evidence=continuous_evidence,
        average_fast=average_fast,
        declining=declining,
        below=below,
        sector_stress=sector_stress,
        correlation=correlation,
        vol_ratio=vol_ratio,
        leader_failure=leader_failure,
        held_damage_ratio=held_damage_ratio,
        held_loss_ratio=held_loss_ratio,
        held_repair_ratio=held_repair_ratio,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        strategic_active=strategic_active,
        strategic_current_gross=strategic_current_gross,
    )
    previous, acute, observation = _acute_trigger_state(ctx)
    if acute:
        return _acute_evacuation_assessment(
            ctx,
            previous=previous,
            single_observation=observation,
        )
    if account.candidate_tenure.get("capital_guard_cooldown", 0) > 0:
        return _capital_cooldown_assessment(ctx)
    return previous, acute


_assess_confirmed_concentrated_break = assess_confirmed_concentrated_break
_resolve_risk_transition = resolve_risk_transition

acute_sector_evacuation_required = _acute_sector_evacuation_required
assess_acute_and_cooldown = _assess_acute_and_cooldown
assess_break_conditions = _assess_break_conditions


__all__ = (
    "BreakConditions",
    "RiskTransitionResolution",
    "_acute_sector_evacuation_required",
    "_assess_acute_and_cooldown",
    "_assess_break_conditions",
    "_assess_confirmed_concentrated_break",
    "_resolve_risk_transition",
    "acute_sector_evacuation_required",
    "assess_acute_and_cooldown",
    "assess_break_conditions",
)
