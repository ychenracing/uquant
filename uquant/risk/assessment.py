"""Fixed-order Base Risk assessment and freeze-only Sentinel integration."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..config import SystemConfig
from ..market_risk import EVIDENCE_FAMILY_MEMBERS
from ..reference import ReferenceContext
from ..risk_sentinel.integration import integrate_freeze_only
from ..risk_sentinel.models import SentinelAssessment
from ..types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment
from .anchors import (
    assess_dynamic_anchors as _assess_dynamic_anchors,
)
from .anchors import update_dynamic_anchors as _update_dynamic_anchors
from .capital import (
    apply_capital_overlays as _apply_capital_overlays,
)
from .capital import observe_capital_budget as _observe_capital_budget
from .confirmed_break import (
    assess_confirmed_concentrated_break as _assess_confirmed_concentrated_break,
)
from .market_book import MarketBookEvidence, assess_market_and_book_evidence
from .protected_recovery import (
    assess_protected_recovery as _assess_protected_recovery,
)
from .recovery_state import assess_recovery_state as _assess_recovery_state
from .strategic_guard import update_strategic_damage_guard as _update_strategic_damage_guard
from .transition_resolution import resolve_risk_transition as _resolve_risk_transition
from .transitions import (
    assess_acute_and_cooldown as _assess_acute_and_cooldown,
)
from .transitions import assess_break_conditions as _assess_break_conditions

# Compatibility export only. Production anchors live in AccountState and are
# selected from reference evidence; no symbol receives a static risk role.
REFERENCE_ANCHORS: tuple[str, ...] = ()

type BaseRiskAssessor = Callable[..., RiskAssessment]
type DynamicAnchorUpdater = Callable[..., tuple[str, ...]]

_DYNAMIC_ANCHOR_UPDATER: ContextVar[DynamicAnchorUpdater] = ContextVar(
    "uquant_risk_dynamic_anchor_updater",
    default=_update_dynamic_anchors,
)


@contextmanager
def _dynamic_anchor_updater_scope(updater: DynamicAnchorUpdater) -> Iterator[None]:
    token = _DYNAMIC_ANCHOR_UPDATER.set(updater)
    try:
        yield
    finally:
        _DYNAMIC_ANCHOR_UPDATER.reset(token)


def _assess_market_and_book_evidence(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    reference_context: ReferenceContext | None,
) -> MarketBookEvidence | RiskAssessment:
    """Run the existing market, breadth, sector, and live-book slice in order."""

    return assess_market_and_book_evidence(
        date=date,
        broad=broad,
        tech=tech,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        reference_context=reference_context,
    )


@dataclass(frozen=True, slots=True)
class _BaseRecoveryStages:
    anchor: Any
    breaks: Any
    recovery: Any


def _base_recovery_stages(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    market: MarketBookEvidence,
) -> _BaseRecoveryStages:
    anchor = _assess_dynamic_anchors(
        date=date,
        reference_panel=reference_panel,
        leaders=leaders,
        account=account,
        cfg=cfg,
        transition_damage=market.transition_damage,
        votes=market.votes,
        update_dynamic_anchors=_DYNAMIC_ANCHOR_UPDATER.get(),
    )
    breaks = _assess_break_conditions(
        date=date,
        tech=tech,
        user_panel=user_panel,
        account=account,
        equity=equity,
        cfg=cfg,
        held_damage=market.held_damage,
        held_damage_ratio=market.held_damage_ratio,
        held_ret5=market.held_ret5,
        operating_dd=market.operating_dd,
        votes=market.votes,
        sector_stress=market.sector_stress,
        transition_damage=market.transition_damage,
        market_context=market.market_context,
    )
    recovery = _assess_recovery_state(
        date=date,
        tech=tech,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        shock_rearmed=breaks.shock_rearmed,
        strategic_active=breaks.strategic_active,
        operating_dd=market.operating_dd,
        capital_dd=market.capital_dd,
        recovery_anchor_elapsed=breaks.recovery_anchor_elapsed,
        emergency_tail_break=breaks.emergency_tail_break,
        concentrated_structure_break=breaks.concentrated_structure_break,
        immediate_severe_break=breaks.immediate_severe_break,
        persistent_market_break=breaks.persistent_market_break,
        reference_anchor_armed=anchor.reference_armed,
        held_damage_ratio=market.held_damage_ratio,
        votes=market.votes,
        sector_stress=market.sector_stress,
        immediate_reference_break=anchor.immediate_reference_break,
        anchor_break_key=anchor.break_key,
        held_cohort_break_confirmed=breaks.held_cohort_break_confirmed,
        strategic_tail_break=breaks.strategic_tail_break,
    )
    return _BaseRecoveryStages(anchor, breaks, recovery)


@dataclass(frozen=True, slots=True)
class _BaseCapitalStages:
    observation: Any
    strategic_damage_guard: Any
    overlays: Any


def _base_capital_stages(
    *,
    account: AccountState,
    cfg: SystemConfig,
    market: MarketBookEvidence,
    recovery: _BaseRecoveryStages,
) -> _BaseCapitalStages:
    breaks = recovery.breaks
    observation = _observe_capital_budget(
        account=account,
        cfg=cfg,
        sector_guard=market.sector_guard,
        reference_anchor_break=recovery.anchor.reference_break,
        held_damage_ratio=market.held_damage_ratio,
        transition_damage=market.transition_damage,
        votes=market.votes,
        capital_dd=market.capital_dd,
        operating_dd=market.operating_dd,
        sector_stress=market.sector_stress,
        strategic_active=breaks.strategic_active,
    )
    strategic_guard = _update_strategic_damage_guard(
        account=account,
        operating_drawdown=market.operating_dd,
        transition_damage=market.transition_damage,
        votes=market.votes,
        cfg=cfg,
    )
    overlays = _apply_capital_overlays(
        account=account,
        cfg=cfg,
        observed_budget_level=observation.observed_budget_level,
        transition_damage=market.transition_damage,
        votes=market.votes,
        held_damage_ratio=market.held_damage_ratio,
        capital_dd=market.capital_dd,
        operating_dd=market.operating_dd,
        strategic_damage_guard=strategic_guard,
    )
    return _BaseCapitalStages(observation, strategic_guard, overlays)


def _continuous_risk_evidence(
    *,
    account: AccountState,
    market: MarketBookEvidence,
    recovery: _BaseRecoveryStages,
    capital: _BaseCapitalStages,
    reference_context: ReferenceContext | None,
) -> dict[str, object]:
    return {
        "breadth20": market.breadth20,
        "breadth60": market.breadth60,
        "name_weighted_declining_ratio": market.declining_name,
        "group_balanced_declining_ratio": market.declining_group,
        "name_weighted_below_ma20_ratio": market.below_name,
        "group_balanced_below_ma20_ratio": market.below_group,
        "transition_damage": market.transition_damage,
        "trend_health": market.trend_health,
        "freeze_new_risk": capital.overlays.freeze_new_risk,
        "chronic_level": account.chronic_level,
        "capital_budget_level": account.capital_budget_level,
        "independent_damage": capital.observation.independent_damage,
        "strategic_damage_guard": capital.strategic_damage_guard,
        "strategic_guard_level2_overlay": capital.overlays.strategic_guard_level2_overlay,
        "risk_anchor_symbols": list(recovery.anchor.symbols),
        "risk_anchor_signature": account.risk_anchor_signature,
        "risk_anchor_group_count": len(recovery.anchor.groups),
        "evidence_families": dict(EVIDENCE_FAMILY_MEMBERS),
        "family_votes": market.family_votes,
        "family_vote_count": market.votes,
        **(reference_context.evidence() if reference_context is not None else {}),
    }


@dataclass(slots=True)
class _BaseRiskContext:
    date: pd.Timestamp
    broad: pd.DataFrame
    tech: pd.DataFrame
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    equity: float
    cfg: SystemConfig
    market: MarketBookEvidence
    recovery: _BaseRecoveryStages
    capital: _BaseCapitalStages
    continuous: dict[str, object]


def _acute_base_resolution(ctx: _BaseRiskContext) -> RiskAssessment | tuple[Risk, bool]:
    market = ctx.market
    breaks = ctx.recovery.breaks
    return _assess_acute_and_cooldown(
        date=ctx.date,
        user_panel=ctx.user_panel,
        account=ctx.account,
        equity=ctx.equity,
        cfg=ctx.cfg,
        market_context=market.market_context,
        sector_guard=market.sector_guard,
        concentrated_confirmed=ctx.recovery.recovery.concentrated_confirmed,
        held_ret5=market.held_ret5,
        votes=market.votes,
        continuous_evidence=ctx.continuous,
        average_fast=market.average_fast,
        declining=market.declining,
        below=market.below,
        sector_stress=market.sector_stress,
        correlation=market.correlation,
        vol_ratio=market.vol_ratio,
        leader_failure=market.leader_failure,
        held_damage_ratio=market.held_damage_ratio,
        held_loss_ratio=market.held_loss_ratio,
        held_repair_ratio=market.held_repair_ratio,
        tech_speed=market.tech_speed,
        broad_speed=market.broad_speed,
        operating_dd=market.operating_dd,
        capital_dd=market.capital_dd,
        strategic_active=breaks.strategic_active,
        strategic_current_gross=breaks.strategic_current_gross,
    )


def _protected_base_resolution(ctx: _BaseRiskContext, *, previous: Risk) -> RiskAssessment | None:
    market = ctx.market
    breaks = ctx.recovery.breaks
    recovery = ctx.recovery.recovery
    overlays = ctx.capital.overlays
    return _assess_protected_recovery(
        date=ctx.date,
        broad=ctx.broad,
        tech=ctx.tech,
        user_panel=ctx.user_panel,
        leaders=ctx.leaders,
        account=ctx.account,
        equity=ctx.equity,
        cfg=ctx.cfg,
        previous=previous,
        votes=market.votes,
        continuous_evidence=ctx.continuous,
        market_context=market.market_context,
        average_fast=market.average_fast,
        declining=market.declining,
        below=market.below,
        sector_stress=market.sector_stress,
        correlation=market.correlation,
        vol_ratio=market.vol_ratio,
        leader_failure=market.leader_failure,
        held_damage_ratio=market.held_damage_ratio,
        held_repair_ratio=market.held_repair_ratio,
        tech_speed=market.tech_speed,
        broad_speed=market.broad_speed,
        operating_dd=market.operating_dd,
        capital_dd=market.capital_dd,
        credible_reserve=recovery.credible_reserve,
        freeze_new_risk=overlays.freeze_new_risk,
        overlay_cap=overlays.overlay_cap,
        overlay_reduction_level=overlays.overlay_reduction_level,
        sector_guard=market.sector_guard,
        shock_rearmed=breaks.shock_rearmed,
        strategic_active=breaks.strategic_active,
    )


def _confirmed_break_resolution(ctx: _BaseRiskContext, *, previous: Risk) -> RiskAssessment | None:
    market = ctx.market
    breaks = ctx.recovery.breaks
    recovery = ctx.recovery.recovery
    return _assess_confirmed_concentrated_break(
        date=ctx.date,
        user_panel=ctx.user_panel,
        leaders=ctx.leaders,
        account=ctx.account,
        equity=ctx.equity,
        cfg=ctx.cfg,
        previous=previous,
        concentrated_confirmed=recovery.concentrated_confirmed,
        votes=market.votes,
        continuous_evidence=ctx.continuous,
        market_context=market.market_context,
        average_fast=market.average_fast,
        declining=market.declining,
        below=market.below,
        sector_stress=market.sector_stress,
        correlation=market.correlation,
        vol_ratio=market.vol_ratio,
        leader_failure=market.leader_failure,
        held_damage_ratio=market.held_damage_ratio,
        held_repair_ratio=market.held_repair_ratio,
        held_ret5=market.held_ret5,
        tech_speed=market.tech_speed,
        broad_speed=market.broad_speed,
        operating_dd=market.operating_dd,
        capital_dd=market.capital_dd,
        strategic_active=breaks.strategic_active,
        strategic_current_gross=breaks.strategic_current_gross,
        overlay_cap=ctx.capital.overlays.overlay_cap,
        credible_reserve=recovery.credible_reserve,
        capital_impaired_restoration_relapse=recovery.capital_impaired_restoration_relapse,
        market_backed_restoration_relapse=recovery.market_backed_restoration_relapse,
        terminal_market_backed_restoration_relapse=(recovery.terminal_market_backed_restoration_relapse),
        incomplete_universe_tail_break=recovery.incomplete_universe_tail_break,
        reference_anchor_confirmed=recovery.reference_anchor_confirmed,
        held_cohort_break_confirmed=breaks.held_cohort_break_confirmed,
        capital_drawdown_relapse=recovery.capital_drawdown_relapse,
        immediate_reference_break=ctx.recovery.anchor.immediate_reference_break,
    )


def _final_base_resolution(
    ctx: _BaseRiskContext, *, previous: Risk, acute_sector_evacuation: bool
) -> RiskAssessment:
    market = ctx.market
    breaks = ctx.recovery.breaks
    overlays = ctx.capital.overlays
    recovery = ctx.recovery.recovery
    resolution = _resolve_risk_transition(
        date=ctx.date,
        user_panel=ctx.user_panel,
        account=ctx.account,
        equity=ctx.equity,
        cfg=ctx.cfg,
        previous=previous,
        shock_rearmed=breaks.shock_rearmed,
        capital_dd=market.capital_dd,
        votes=market.votes,
        sector_stress=market.sector_stress,
        narrow_anchor_guard=breaks.narrow_anchor_guard,
        operating_dd=market.operating_dd,
        independent_damage=ctx.capital.observation.independent_damage,
        reasons=market.reasons,
        sector_guard=market.sector_guard,
        held_ret5=market.held_ret5,
        credible_reserve=recovery.credible_reserve,
        strategic_active=breaks.strategic_active,
        overlay_cap=overlays.overlay_cap,
    )
    observation = resolution.observation
    return RiskAssessment(
        state=resolution.state,
        target_gross_cap=resolution.cap,
        votes=market.votes,
        evidence={
            **ctx.continuous,
            **market.market_context,
            "ai_fast_return": market.average_fast,
            "declining_ratio": market.declining,
            "below_ma20_ratio": market.below,
            "sector_stress_ratio": market.sector_stress,
            "median_correlation": market.correlation,
            "volatility_ratio": market.vol_ratio,
            "leader_failure_ratio": market.leader_failure,
            "held_damage_ratio": market.held_damage_ratio,
            "held_repair_ratio": market.held_repair_ratio,
            "tech_speed": market.tech_speed,
            "broad_speed": market.broad_speed,
            "operating_drawdown": market.operating_dd,
            "capital_drawdown": market.capital_dd,
            "strategic_cohort_active": breaks.strategic_active,
            "strategic_current_gross": breaks.strategic_current_gross,
            "sector_guard_active": market.sector_guard.active,
            "acute_sector_evacuation": acute_sector_evacuation,
            "sector_guard_shock_count": market.sector_guard.shock_count,
            "sector_guard_active_sessions": market.sector_guard.active_sessions,
            "sector_guard_equal_return": (observation.equal_return if observation is not None else None),
            "sector_guard_weighted_return": (
                observation.weighted_return if observation is not None else None
            ),
            "sector_guard_negative_exposure": (
                observation.negative_exposure if observation is not None else None
            ),
            "sector_guard_positive_breadth": (
                observation.positive_breadth if observation is not None else None
            ),
        },
        reasons=tuple(market.reasons),
        shock_state=resolution.shock,
        freeze_new_risk=overlays.freeze_new_risk or resolution.state is not Risk.NORMAL,
        reduction_level=max(
            overlays.overlay_reduction_level,
            3
            if resolution.state is Risk.CRISIS
            else 2
            if resolution.state is Risk.RISK_OFF
            else 1
            if resolution.state is Risk.CAUTION
            else 0,
        ),
        severity=ctx.account.shock_severity,
    )


def _assess_base_risk(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    reference_context: ReferenceContext | None = None,
    configured_universe_size: int | None = None,
) -> RiskAssessment:
    """Assess market, breadth, correlation, holding, and drawdown risk.

    This function is the sole authority for gross-exposure caps. It updates the
    account's persistent shock/recovery state and returns the evidence used by
    the portfolio allocator and daily report.
    """

    if date not in broad.index or date not in tech.index:
        raise RuntimeError("risk indices missing at decision date")
    del configured_universe_size
    market = _assess_market_and_book_evidence(
        date=date,
        broad=broad,
        tech=tech,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        reference_context=reference_context,
    )
    if isinstance(market, RiskAssessment):
        return market
    recovery = _base_recovery_stages(
        date=date,
        tech=tech,
        reference_panel=reference_panel,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        market=market,
    )
    capital = _base_capital_stages(account=account, cfg=cfg, market=market, recovery=recovery)
    continuous = _continuous_risk_evidence(
        account=account,
        market=market,
        recovery=recovery,
        capital=capital,
        reference_context=reference_context,
    )
    ctx = _BaseRiskContext(
        date=date,
        broad=broad,
        tech=tech,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        market=market,
        recovery=recovery,
        capital=capital,
        continuous=continuous,
    )
    acute = _acute_base_resolution(ctx)
    if isinstance(acute, RiskAssessment):
        return acute
    previous, acute_sector_evacuation = acute
    protected = _protected_base_resolution(ctx, previous=previous)
    if protected is not None:
        return protected
    confirmed = _confirmed_break_resolution(ctx, previous=previous)
    if confirmed is not None:
        return confirmed
    return _final_base_resolution(
        ctx,
        previous=previous,
        acute_sector_evacuation=acute_sector_evacuation,
    )


def assess_risk_with_capabilities(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    reference_context: ReferenceContext | None = None,
    configured_universe_size: int | None = None,
    sentinel_assessment: SentinelAssessment | None = None,
    sentinel_opportunity: Opportunity | str | None = None,
    base_risk_assessor: BaseRiskAssessor,
    dynamic_anchor_updater: DynamicAnchorUpdater,
) -> RiskAssessment:
    """Return formal uquant risk with the optional freeze-only Sentinel overlay.

    The base assessor remains the sole owner of state, severity, reductions,
    and gross caps.  Integration is deliberately applied only to its immutable
    result, so Sentinel cannot mutate the durable account or create a parallel
    risk transition.
    """

    with _dynamic_anchor_updater_scope(dynamic_anchor_updater):
        base = base_risk_assessor(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=reference_returns,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            equity=equity,
            cfg=cfg,
            reference_context=reference_context,
            configured_universe_size=configured_universe_size,
        )
    return integrate_freeze_only(
        base=base,
        sentinel=sentinel_assessment,
        cfg=cfg,
        opportunity=sentinel_opportunity,
    )


assess_base_risk = _assess_base_risk
