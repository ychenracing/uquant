"""Confirmed risk-state transition and gross-cap ownership."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..holding_history import protected_weights_for_current_episode
from ..risk_sector import SectorGuardTransition, SectorObservation
from ..types import AccountState, Risk
from .protected_recovery import capture_protected_holdings, persistent_crisis_cap
from .recovery_state import reset_recovery_owner_rearm


@dataclass(frozen=True, slots=True)
class RiskTransitionResolution:
    """Read-only outputs from the confirmed-transition slice."""

    state: Risk
    shock: str
    cap: float
    sector_guard_forced: bool
    observation: SectorObservation | None


@dataclass(slots=True)
class _RiskTransitionContext:
    date: pd.Timestamp
    user_panel: dict[str, pd.DataFrame]
    account: AccountState
    equity: float
    cfg: SystemConfig
    previous: Risk
    shock_rearmed: bool
    capital_dd: float
    votes: int
    sector_stress: float
    narrow_anchor_guard: bool
    operating_dd: float
    independent_damage: bool
    reasons: list[str]
    sector_guard: SectorGuardTransition
    held_ret5: list[float]
    credible_reserve: bool
    strategic_active: bool
    overlay_cap: float


def _observed_risk(ctx: _RiskTransitionContext) -> Risk:
    account = ctx.account
    cfg = ctx.cfg
    if (
        ctx.shock_rearmed
        and not protected_weights_for_current_episode(account)
        and ctx.capital_dd >= cfg.capital_dd_crisis
        and ctx.votes >= 4
    ):
        return Risk.CRISIS
    if ctx.narrow_anchor_guard:
        ctx.reasons.append("narrow-market concentrated anchor damage")
        return Risk.RISK_OFF
    if (
        (ctx.capital_dd >= cfg.capital_dd_risk_off or ctx.operating_dd >= 0.10)
        and ctx.votes >= 3
        and ctx.sector_stress >= 0.50
        # Broad/index warnings without damage in the owned book are a level-1
        # freeze, not permission to manufacture a sale. A level-2 reduction
        # needs confirmed structural damage or an active reduction rung.
        and (ctx.independent_damage or account.capital_budget_level >= 2)
    ):
        return Risk.RISK_OFF
    if ctx.operating_dd >= cfg.operating_dd_caution or ctx.votes >= 2:
        return Risk.CAUTION
    return Risk.NORMAL


def _confirmed_risk(ctx: _RiskTransitionContext, observed: Risk) -> Risk:
    account = ctx.account
    key = f"risk_{observed.value.lower()}"
    account.risk_streaks[key] = account.risk_streaks.get(key, 0) + 1
    for other in Risk:
        other_key = f"risk_{other.value.lower()}"
        if other_key != key:
            account.risk_streaks[other_key] = 0
    required = {
        Risk.NORMAL: ctx.cfg.recovery_risk_confirm_days if ctx.previous is not Risk.NORMAL else 1,
        Risk.CAUTION: ctx.cfg.caution_confirm_days,
        Risk.RISK_OFF: ctx.cfg.risk_off_confirm_days,
        Risk.CRISIS: ctx.cfg.crisis_confirm_days,
    }[observed]
    if ctx.narrow_anchor_guard and observed is Risk.RISK_OFF:
        required = 1
    return observed if account.risk_streaks[key] >= required else ctx.previous


def _transition_shock(account: AccountState, *, previous: Risk, observed: Risk, state: Risk) -> str:
    if state is Risk.CRISIS:
        return "SHOCK" if previous is not Risk.CRISIS else "PERSISTENT_STRESS"
    if previous is Risk.CRISIS and state in {Risk.RISK_OFF, Risk.CAUTION}:
        return "RECOVERY"
    if account.shock_state == "RECOVERY" and observed in {Risk.RISK_OFF, Risk.CRISIS}:
        return "FAILED_REPAIR"
    return "NONE" if state is Risk.NORMAL else account.shock_state


def _apply_sector_guard(ctx: _RiskTransitionContext, *, state: Risk, shock: str) -> tuple[Risk, str, bool]:
    forced = bool(ctx.sector_guard.active and state is not Risk.CRISIS)
    if not forced:
        return state, shock, forced
    state = Risk.RISK_OFF
    shock = "SECTOR_GUARD"
    guard_reason = "confirmed synchronized holdings shock"
    if guard_reason not in ctx.reasons:
        ctx.reasons.append(guard_reason)
    return state, shock, forced


def _prepare_new_crisis(ctx: _RiskTransitionContext) -> None:
    account = ctx.account
    reset_recovery_owner_rearm(account)
    capture_protected_holdings(
        account=account, date=ctx.date, user_panel=ctx.user_panel, equity=ctx.equity, use_anchors=False,
    )
    account.shock_start_date = str(ctx.date.date())
    account.last_shock_date = str(ctx.date.date())
    account.candidate_tenure["last_shock_incomplete_universe"] = 0
    severe_held_move = bool(ctx.held_ret5) and float(np.mean(ctx.held_ret5)) <= ctx.cfg.severe_shock_ret5
    account.shock_severity = (
        "SEVERE" if severe_held_move and ctx.votes >= 4 else "CONCENTRATED" if severe_held_move else "MARKET"
    )


def _record_transition(ctx: _RiskTransitionContext, *, state: Risk, sector_guard_forced: bool) -> None:
    if state == ctx.previous:
        return
    ctx.account.risk_events.append(
        {
            "date": str(ctx.date.date()),
            "from": ctx.previous.value,
            "to": state.value,
            "votes": ctx.votes,
            "reasons": ctx.reasons,
            "severity": ctx.account.shock_severity,
            "route": "sector_guard" if sector_guard_forced else "risk_state",
        }
    )


def _transition_cap(ctx: _RiskTransitionContext, *, state: Risk, sector_guard_forced: bool) -> float:
    crisis_cap = persistent_crisis_cap(
        ctx.account.shock_severity,
        ctx.cfg,
        reserve_backed=bool(ctx.credible_reserve and ctx.account.anchor_weights and not ctx.strategic_active),
    )
    cap = {
        Risk.NORMAL: ctx.cfg.max_gross,
        # CAUTION is the level-1 early warning: freeze additions, scouts, and
        # rotation without manufacturing a sale.
        Risk.CAUTION: ctx.cfg.max_gross,
        Risk.RISK_OFF: ctx.cfg.risk_off_gross,
        Risk.CRISIS: crisis_cap,
    }[state]
    if ctx.narrow_anchor_guard and state is Risk.RISK_OFF:
        cap = ctx.cfg.narrow_anchor_guard_gross
    cap = min(cap, ctx.overlay_cap)
    if sector_guard_forced:
        cap = min(cap, ctx.cfg.sector_guard_gross)
    return cap


def resolve_risk_transition(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    previous: Risk,
    shock_rearmed: bool,
    capital_dd: float,
    votes: int,
    sector_stress: float,
    narrow_anchor_guard: bool,
    operating_dd: float,
    independent_damage: bool,
    reasons: list[str],
    sector_guard: SectorGuardTransition,
    held_ret5: list[float],
    credible_reserve: bool,
    strategic_active: bool,
    overlay_cap: float,
) -> RiskTransitionResolution:
    """Run the confirmed state, shock, event, and cap stages in order."""

    ctx = _RiskTransitionContext(
        date=date,
        user_panel=user_panel,
        account=account,
        equity=equity,
        cfg=cfg,
        previous=previous,
        shock_rearmed=shock_rearmed,
        capital_dd=capital_dd,
        votes=votes,
        sector_stress=sector_stress,
        narrow_anchor_guard=narrow_anchor_guard,
        operating_dd=operating_dd,
        independent_damage=independent_damage,
        reasons=reasons,
        sector_guard=sector_guard,
        held_ret5=held_ret5,
        credible_reserve=credible_reserve,
        strategic_active=strategic_active,
        overlay_cap=overlay_cap,
    )
    observed = _observed_risk(ctx)
    state = _confirmed_risk(ctx, observed)
    shock = _transition_shock(account, previous=previous, observed=observed, state=state)
    state, shock, sector_guard_forced = _apply_sector_guard(ctx, state=state, shock=shock)
    if previous is Risk.CRISIS and state is not Risk.CRISIS:
        account.operating_peak = equity
    if state is Risk.CRISIS and previous is not Risk.CRISIS:
        _prepare_new_crisis(ctx)
    _record_transition(ctx, state=state, sector_guard_forced=sector_guard_forced)
    account.risk = state.value
    account.shock_state = shock
    cap = _transition_cap(ctx, state=state, sector_guard_forced=sector_guard_forced)
    return RiskTransitionResolution(
        state=state,
        shock=shock,
        cap=cap,
        sector_guard_forced=sector_guard_forced,
        observation=sector_guard.observation,
    )


__all__ = ("RiskTransitionResolution", "resolve_risk_transition")
