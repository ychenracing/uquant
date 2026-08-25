"""Recovery-owner rearm and persistent crisis-cap ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..config import SystemConfig
from ..leader import credible_recovery_reserve
from ..types import AccountState, LeaderScore, Risk
from .protected_recovery import assess_protected_recovery


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Read-only outputs from the existing recovery and relapse slice."""

    credible_reserve: bool
    incomplete_universe_tail_break: bool
    reference_anchor_confirmed: bool
    capital_impaired_restoration_relapse: bool
    market_backed_restoration_relapse: bool
    terminal_market_backed_restoration_relapse: bool
    capital_drawdown_relapse: bool
    concentrated_confirmed: bool


@dataclass(slots=True)
class _RecoveryStateContext:
    date: pd.Timestamp
    tech: pd.DataFrame
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    equity: float
    cfg: SystemConfig
    shock_rearmed: bool
    strategic_active: bool
    operating_dd: float
    capital_dd: float
    recovery_anchor_elapsed: int
    emergency_tail_break: bool
    concentrated_structure_break: bool
    immediate_severe_break: bool
    persistent_market_break: bool
    reference_anchor_armed: bool
    held_damage_ratio: float
    votes: int
    sector_stress: float
    immediate_reference_break: bool
    anchor_break_key: str
    held_cohort_break_confirmed: bool
    strategic_tail_break: bool


def _credible_recovery_state(ctx: _RecoveryStateContext) -> tuple[bool, bool]:
    live_members = {
        symbol
        for symbol, position in ctx.account.positions.items()
        if position.shares > 0 and position.lifecycle == "RECOVERY"
    }
    owner_observed = bool(
        ctx.account.anchor_weights
        or live_members
        or ctx.account.candidate_tenure.get("tactical_active", 0) == 1
    )
    book_complete = bool(
        not owner_observed
        or len(set(ctx.account.anchor_weights) | live_members) >= min(3, ctx.cfg.max_positions)
    )
    anchor_industries = {
        ctx.leaders[symbol].industry for symbol in ctx.account.anchor_weights if symbol in ctx.leaders
    }
    reserve_observed = bool(
        len(ctx.account.anchor_weights) >= 2
        and any(
            symbol not in ctx.account.anchor_weights
            and symbol in ctx.leaders
            and credible_recovery_reserve(
                score=ctx.leaders[symbol],
                frame=frame,
                date=ctx.date,
                occupied_industries=anchor_industries,
                cfg=ctx.cfg,
            )
            for symbol, frame in ctx.user_panel.items()
        )
    )
    if reserve_observed:
        ctx.account.candidate_tenure["recovery_reserve_qualified"] = 1
    credible = bool(
        ctx.account.candidate_tenure.get("recovery_reserve_qualified", 0) == 1
        or ctx.account.candidate_tenure.get("recovery_substitution_completed", 0) >= 1
    )
    return credible, book_complete


def _incomplete_universe_break(
    ctx: _RecoveryStateContext,
    *,
    credible_reserve: bool,
    recovery_book_complete: bool,
) -> bool:
    account = ctx.account
    return bool(
        ctx.shock_rearmed
        and not account.protected_weights
        and bool(account.positions)
        and not ctx.strategic_active
        and not recovery_book_complete
        and ctx.operating_dd
        >= (
            ctx.cfg.unbacked_universe_tail_dd
            if not credible_reserve
            and account.candidate_tenure.get("tactical_active", 0) == 0
            and (
                not account.anchor_weights
                or (
                    len(account.anchor_weights) >= 1
                    and ctx.recovery_anchor_elapsed >= ctx.cfg.unbacked_recovery_anchor_min_days
                )
            )
            else ctx.cfg.incomplete_universe_tail_dd
        )
    )


def _account_recovery_break(ctx: _RecoveryStateContext) -> bool:
    account = ctx.account
    return bool(
        ctx.shock_rearmed
        and not account.protected_weights
        and not account.anchor_weights
        and not ctx.strategic_active
        and (
            ctx.emergency_tail_break
            or (ctx.concentrated_structure_break and ctx.immediate_severe_break)
            or ctx.persistent_market_break
        )
    )


def _reference_recovery_break(ctx: _RecoveryStateContext) -> bool:
    account = ctx.account
    return bool(
        ctx.shock_rearmed
        and not account.protected_weights
        and ctx.reference_anchor_armed
        and ctx.held_damage_ratio >= ctx.cfg.concentrated_break_ratio
        and ctx.operating_dd >= ctx.cfg.incomplete_universe_tail_dd
        and ctx.votes >= 4
        and ctx.sector_stress >= 0.50
        and (ctx.immediate_reference_break or account.risk_streaks[ctx.anchor_break_key] >= 2)
    )


def _recovery_break_state(
    ctx: _RecoveryStateContext,
    *,
    credible_reserve: bool,
    recovery_book_complete: bool,
) -> tuple[bool, bool, bool]:
    incomplete = _incomplete_universe_break(
        ctx,
        credible_reserve=credible_reserve,
        recovery_book_complete=recovery_book_complete,
    )
    account_break = _account_recovery_break(ctx)
    reference = _reference_recovery_break(ctx)
    return incomplete and not account_break and not reference, account_break, reference


def _sessions_since_recovery(ctx: _RecoveryStateContext) -> float:
    dates = [
        pd.Timestamp(event["date"])
        for event in ctx.account.risk_events
        if event.get("from") == Risk.CRISIS.value
        and event.get("to") != Risk.CRISIS.value
        and event.get("date")
        and pd.Timestamp(event["date"]) <= ctx.date
    ]
    return len(ctx.tech.loc[max(dates) : ctx.date]) - 1 if dates and ctx.user_panel else math.inf


def _last_shock_was_market_backed(ctx: _RecoveryStateContext) -> bool:
    return bool(
        ctx.account.last_shock_date
        and any(
            event.get("date") == ctx.account.last_shock_date
            and event.get("to") == Risk.CRISIS.value
            and any(
                reason
                in {
                    "market-backed drawdown relapse in restored holdings",
                    "market-backed portfolio break in incomplete restoration",
                }
                for reason in event.get("reasons", ())
                if isinstance(reason, str)
            )
            for event in ctx.account.risk_events
        )
    )


def _capital_impaired_relapse(ctx: _RecoveryStateContext, *, sessions_since_recovery: float) -> bool:
    account = ctx.account
    return bool(
        account.positions
        and account.protected_weights
        and ctx.equity < account.initial_cash - 1e-12
        and ctx.capital_dd >= ctx.cfg.capital_dd_crisis
        and ctx.operating_dd >= ctx.cfg.capital_guard_relapse_dd
        and sessions_since_recovery >= ctx.cfg.capital_guard_min_recovery_days
        and (
            ctx.held_damage_ratio >= ctx.cfg.concentrated_break_ratio
            or (ctx.votes >= 2 and ctx.sector_stress >= 0.50)
        )
    )


def _market_backed_relapse(ctx: _RecoveryStateContext, *, sessions_since_recovery: float) -> bool:
    account = ctx.account
    return bool(
        account.positions
        and account.protected_weights
        and account.risk == Risk.CAUTION.value
        and (
            ctx.shock_rearmed
            or (
                not _last_shock_was_market_backed(ctx)
                and account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
                and ctx.operating_dd >= ctx.cfg.portfolio_break_dd
            )
        )
        and not ctx.strategic_active
        and not account.anchor_weights
        and ctx.equity >= account.initial_cash - 1e-12
        and ctx.operating_dd >= ctx.cfg.capital_guard_relapse_dd
        and sessions_since_recovery >= ctx.cfg.capital_guard_min_recovery_days
        and ctx.held_damage_ratio >= ctx.cfg.concentrated_break_ratio
        and ctx.votes >= 3
        and ctx.sector_stress >= 0.50
    )


def _restoration_relapse_state(
    ctx: _RecoveryStateContext,
) -> tuple[bool, bool, bool, bool]:
    sessions_since_recovery = _sessions_since_recovery(ctx)
    account = ctx.account
    capital_impaired = _capital_impaired_relapse(ctx, sessions_since_recovery=sessions_since_recovery)
    market_backed = _market_backed_relapse(ctx, sessions_since_recovery=sessions_since_recovery)
    terminal_market = bool(
        market_backed
        and account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
        and ctx.operating_dd >= ctx.cfg.portfolio_break_dd
    )
    return capital_impaired, market_backed, terminal_market, bool(capital_impaired or market_backed)


def _reset_recovery_owner_rearm(account: AccountState) -> None:
    """Close the prior recovery-owner epoch when a new shock takes control."""

    for key in (
        "recovery_owner_handoff",
        "recovery_owner_rearm_submitted",
        "recovery_owner_rearm_complete",
        "post_shock_restore_submitted",
        "post_shock_restore_deferred_expansion",
    ):
        account.candidate_tenure[key] = 0


reset_recovery_owner_rearm = _reset_recovery_owner_rearm


def _persistent_crisis_cap(
    severity: str,
    cfg: SystemConfig,
    *,
    reserve_backed: bool = False,
) -> float:
    """Keep severity—not a position label—as the persistent cap owner."""
    if severity == "INCOMPLETE_UNIVERSE":
        return cfg.incomplete_universe_crisis_gross
    if severity == "INCOMPLETE_UNIVERSE_UNBACKED":
        return 0.0
    if severity == "COHORT_BREAK":
        # An independently qualified reserve lets a mature recovery owner stay
        # inside the existing risk-off budget while it repairs or substitutes;
        # without that breadth, the same synchronized break remains a
        # concentrated crisis.  This distinction is evidence-based and never
        # depends on configured pool size.
        return cfg.risk_off_gross if reserve_backed else cfg.concentrated_crisis_gross
    if severity in {"SEVERE", "ANCHOR_BREAK"}:
        return cfg.severe_crisis_gross
    if severity == "CONCENTRATED":
        return cfg.concentrated_crisis_gross
    return cfg.market_crisis_gross


def _assess_recovery_state(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    shock_rearmed: bool,
    strategic_active: bool,
    operating_dd: float,
    capital_dd: float,
    recovery_anchor_elapsed: int,
    emergency_tail_break: bool,
    concentrated_structure_break: bool,
    immediate_severe_break: bool,
    persistent_market_break: bool,
    reference_anchor_armed: bool,
    held_damage_ratio: float,
    votes: int,
    sector_stress: float,
    immediate_reference_break: bool,
    anchor_break_key: str,
    held_cohort_break_confirmed: bool,
    strategic_tail_break: bool,
) -> RecoveryAssessment:
    """Run recovery breadth, reserve, and restoration-relapse ownership in order."""

    ctx = _RecoveryStateContext(
        date=date,
        tech=tech,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        shock_rearmed=shock_rearmed,
        strategic_active=strategic_active,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        recovery_anchor_elapsed=recovery_anchor_elapsed,
        emergency_tail_break=emergency_tail_break,
        concentrated_structure_break=concentrated_structure_break,
        immediate_severe_break=immediate_severe_break,
        persistent_market_break=persistent_market_break,
        reference_anchor_armed=reference_anchor_armed,
        held_damage_ratio=held_damage_ratio,
        votes=votes,
        sector_stress=sector_stress,
        immediate_reference_break=immediate_reference_break,
        anchor_break_key=anchor_break_key,
        held_cohort_break_confirmed=held_cohort_break_confirmed,
        strategic_tail_break=strategic_tail_break,
    )
    credible_reserve, book_complete = _credible_recovery_state(ctx)
    incomplete, account_break, reference = _recovery_break_state(
        ctx,
        credible_reserve=credible_reserve,
        recovery_book_complete=book_complete,
    )
    capital_impaired, market_backed, terminal_market, capital_relapse = _restoration_relapse_state(ctx)
    concentrated = (
        account_break
        or reference
        or held_cohort_break_confirmed
        or incomplete
        or (shock_rearmed and strategic_tail_break and reference)
        or capital_relapse
    )
    return RecoveryAssessment(
        credible_reserve=credible_reserve,
        incomplete_universe_tail_break=incomplete,
        reference_anchor_confirmed=reference,
        capital_impaired_restoration_relapse=capital_impaired,
        market_backed_restoration_relapse=market_backed,
        terminal_market_backed_restoration_relapse=terminal_market,
        capital_drawdown_relapse=capital_relapse,
        concentrated_confirmed=concentrated,
    )


_assess_protected_recovery = assess_protected_recovery

assess_recovery_state = _assess_recovery_state
persistent_crisis_cap = _persistent_crisis_cap


__all__ = (
    "RecoveryAssessment",
    "_assess_protected_recovery",
    "_assess_recovery_state",
    "_persistent_crisis_cap",
    "_reset_recovery_owner_rearm",
    "assess_recovery_state",
    "persistent_crisis_cap",
    "reset_recovery_owner_rearm",
)
