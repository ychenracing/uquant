"""Confirmed concentrated-break transition ownership."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..types import AccountState, LeaderScore, Risk, RiskAssessment
from .protected_recovery import persistent_crisis_cap
from .recovery_state import reset_recovery_owner_rearm
from .strategic_guard import strategic_crisis_severity


@dataclass(slots=True)
class ConfirmedBreakContext:
    date: pd.Timestamp
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    equity: float
    cfg: SystemConfig
    previous: Risk
    votes: int
    continuous_evidence: dict[str, object]
    market_context: dict[str, float]
    average_fast: float
    declining: float
    below: float
    sector_stress: float
    correlation: float
    vol_ratio: float
    leader_failure: float
    held_damage_ratio: float
    held_repair_ratio: float
    held_ret5: list[float]
    tech_speed: float
    broad_speed: float
    operating_dd: float
    capital_dd: float
    strategic_active: bool
    strategic_current_gross: float
    overlay_cap: float
    credible_reserve: bool
    capital_impaired_restoration_relapse: bool
    market_backed_restoration_relapse: bool
    terminal_market_backed_restoration_relapse: bool
    incomplete_universe_tail_break: bool
    reference_anchor_confirmed: bool
    held_cohort_break_confirmed: bool
    capital_drawdown_relapse: bool
    immediate_reference_break: bool


def _prepare_confirmed_break(ctx: ConfirmedBreakContext) -> None:
    account = ctx.account
    reset_recovery_owner_rearm(account)
    if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1:
        # A new independent event owns a new pre-cut economic snapshot; never
        # resurrect targets from an already completed repair.
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
    if ctx.capital_impaired_restoration_relapse or ctx.terminal_market_backed_restoration_relapse:
        account.candidate_tenure["capital_guard_cooldown"] = ctx.cfg.capital_guard_cooldown_days
    account.candidate_tenure["last_shock_incomplete_universe"] = int(
        ctx.incomplete_universe_tail_break and ctx.credible_reserve
    )


def _confirmed_break_severity(ctx: ConfirmedBreakContext) -> str:
    account = ctx.account
    severe_held_move = bool(ctx.held_ret5) and float(np.mean(ctx.held_ret5)) <= ctx.cfg.severe_shock_ret5
    if ctx.incomplete_universe_tail_break:
        return "INCOMPLETE_UNIVERSE" if ctx.credible_reserve else "INCOMPLETE_UNIVERSE_UNBACKED"
    if ctx.held_cohort_break_confirmed:
        return "COHORT_BREAK"
    if ctx.reference_anchor_confirmed and ctx.strategic_active:
        return strategic_crisis_severity(
            strategic_active=True,
            reference_anchor_confirmed=True,
            live_core_positions=sum(
                position.shares > 0 for position in account.positions.values() if position.lifecycle == "CORE"
            ),
        )
    if ctx.reference_anchor_confirmed:
        held_industries = {
            ctx.leaders[symbol].industry
            for symbol, position in account.positions.items()
            if position.shares > 0 and symbol in ctx.leaders
        }
        return "SEVERE" if ctx.immediate_reference_break and len(held_industries) >= 2 else "CONCENTRATED"
    if ctx.strategic_active:
        return strategic_crisis_severity(
            strategic_active=True,
            reference_anchor_confirmed=False,
            live_core_positions=sum(
                position.shares > 0 for position in account.positions.values() if position.lifecycle == "CORE"
            ),
        )
    return (
        "SEVERE" if severe_held_move and ctx.votes >= 4 else "CONCENTRATED" if severe_held_move else "NORMAL"
    )


def _confirmed_break_reason(ctx: ConfirmedBreakContext) -> str:
    return (
        "confirmed dynamic cohort structural break"
        if ctx.held_cohort_break_confirmed
        else "market-backed portfolio break in incomplete restoration"
        if ctx.terminal_market_backed_restoration_relapse
        else "market-backed drawdown relapse in restored holdings"
        if ctx.market_backed_restoration_relapse
        else "capital drawdown relapse in restored holdings"
        if ctx.capital_drawdown_relapse
        else "reserve-backed incomplete-universe tail guard"
        if ctx.incomplete_universe_tail_break and ctx.credible_reserve
        else "unbacked incomplete-universe capital exit"
        if ctx.incomplete_universe_tail_break
        else "confirmed strategic cohort capital guard"
        if ctx.strategic_active
        else "confirmed concentrated leader break"
    )


def _confirmed_break_route(ctx: ConfirmedBreakContext) -> str:
    return (
        "strategic_cohort"
        if ctx.strategic_active
        else "incomplete_universe_reserve"
        if ctx.incomplete_universe_tail_break and ctx.credible_reserve
        else "incomplete_universe_unbacked"
        if ctx.incomplete_universe_tail_break
        else "dynamic_cohort"
        if ctx.held_cohort_break_confirmed
        else "reference_anchor"
        if ctx.reference_anchor_confirmed
        else "account_holdings"
    )


def _confirmed_break_evidence(ctx: ConfirmedBreakContext) -> dict[str, object]:
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
        "held_repair_ratio": ctx.held_repair_ratio,
        "tech_speed": ctx.tech_speed,
        "broad_speed": ctx.broad_speed,
        "operating_drawdown": ctx.operating_dd,
        "capital_drawdown": ctx.capital_dd,
        "strategic_cohort_active": ctx.strategic_active,
        "strategic_current_gross": ctx.strategic_current_gross,
    }


def assess_confirmed_concentrated_break(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    previous: Risk,
    concentrated_confirmed: bool,
    votes: int,
    continuous_evidence: dict[str, object],
    market_context: dict[str, float],
    average_fast: float,
    declining: float,
    below: float,
    sector_stress: float,
    correlation: float,
    vol_ratio: float,
    leader_failure: float,
    held_damage_ratio: float,
    held_repair_ratio: float,
    held_ret5: list[float],
    tech_speed: float,
    broad_speed: float,
    operating_dd: float,
    capital_dd: float,
    strategic_active: bool,
    strategic_current_gross: float,
    overlay_cap: float,
    credible_reserve: bool,
    capital_impaired_restoration_relapse: bool,
    market_backed_restoration_relapse: bool,
    terminal_market_backed_restoration_relapse: bool,
    incomplete_universe_tail_break: bool,
    reference_anchor_confirmed: bool,
    held_cohort_break_confirmed: bool,
    capital_drawdown_relapse: bool,
    immediate_reference_break: bool,
) -> RiskAssessment | None:
    """Run the confirmed concentrated-break stages in historical order."""
    if not concentrated_confirmed:
        return None
    ctx = ConfirmedBreakContext(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        previous=previous,
        votes=votes,
        continuous_evidence=continuous_evidence,
        market_context=market_context,
        average_fast=average_fast,
        declining=declining,
        below=below,
        sector_stress=sector_stress,
        correlation=correlation,
        vol_ratio=vol_ratio,
        leader_failure=leader_failure,
        held_damage_ratio=held_damage_ratio,
        held_repair_ratio=held_repair_ratio,
        held_ret5=held_ret5,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        strategic_active=strategic_active,
        strategic_current_gross=strategic_current_gross,
        overlay_cap=overlay_cap,
        credible_reserve=credible_reserve,
        capital_impaired_restoration_relapse=capital_impaired_restoration_relapse,
        market_backed_restoration_relapse=market_backed_restoration_relapse,
        terminal_market_backed_restoration_relapse=terminal_market_backed_restoration_relapse,
        incomplete_universe_tail_break=incomplete_universe_tail_break,
        reference_anchor_confirmed=reference_anchor_confirmed,
        held_cohort_break_confirmed=held_cohort_break_confirmed,
        capital_drawdown_relapse=capital_drawdown_relapse,
        immediate_reference_break=immediate_reference_break,
    )
    _prepare_confirmed_break(ctx)
    account.shock_severity = _confirmed_break_severity(ctx)
    state = Risk.CRISIS
    shock = "SHOCK"
    crisis_gross = min(
        persistent_crisis_cap(
            account.shock_severity,
            cfg,
            reserve_backed=bool(credible_reserve and account.anchor_weights and not strategic_active),
        ),
        overlay_cap,
    )
    reason = _confirmed_break_reason(ctx)
    account.risk = state.value
    account.shock_state = shock
    account.risk_streaks["concentrated_repair"] = 0
    account.risk_events.append(
        {
            "date": str(date.date()),
            "from": previous.value,
            "to": state.value,
            "votes": votes,
            "reasons": [reason],
            "severity": account.shock_severity,
            "route": _confirmed_break_route(ctx),
            "target_gross_cap": crisis_gross,
        }
    )
    return RiskAssessment(
        state=state,
        target_gross_cap=crisis_gross,
        votes=votes,
        evidence=_confirmed_break_evidence(ctx),
        reasons=(reason,),
        shock_state=shock,
        freeze_new_risk=True,
        reduction_level=3,
        severity=account.shock_severity,
    )


__all__ = ("ConfirmedBreakContext", "assess_confirmed_concentrated_break")
