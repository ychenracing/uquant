"""Fixed-order Base Risk assessment and freeze-only Sentinel integration."""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import cross_section_returns, scalar
from ..leader import INDUSTRY, REFERENCE_UNIVERSE
from ..market_risk import (
    EVIDENCE_FAMILY_MEMBERS,
    build_base_market_family_snapshot,
)
from ..market_risk import evidence_family_votes as _evidence_family_votes
from ..reference import ReferenceContext
from ..risk_sector import (
    SectorGuardTransition,
    update_sector_guard,
)
from ..risk_sentinel.integration import integrate_freeze_only
from ..risk_sentinel.models import SentinelAssessment
from ..types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment
from .anchors import _assess_dynamic_anchors
from .capital import (
    _apply_capital_overlays,
    _observe_capital_budget,
    _portfolio_drawdowns,
)
from .recovery_state import (
    _assess_protected_recovery,
    _assess_recovery_state,
    _reset_recovery_owner_rearm,
)
from .strategic_guard import _update_strategic_damage_guard
from .transitions import (
    _assess_acute_and_cooldown,
    _assess_break_conditions,
    _assess_confirmed_concentrated_break,
    _resolve_risk_transition,
)

# Compatibility export only. Production anchors live in AccountState and are
# selected from reference evidence; no symbol receives a static risk role.
REFERENCE_ANCHORS: tuple[str, ...] = ()


def _risk_runtime_seam(
    name: Literal["_assess_base_risk", "_update_dynamic_anchors"],
) -> Callable[..., Any]:
    """Resolve only the two historically live facade monkeypatch seams."""

    facade = sys.modules["uquant.risk"]
    if name == "_assess_base_risk":
        return cast(Callable[..., Any], vars(facade)["_assess_base_risk"])
    return cast(Callable[..., Any], vars(facade)["_update_dynamic_anchors"])


@dataclass(frozen=True, slots=True)
class MarketBookEvidence:
    """Read-only outputs from the ordered market and live-book evidence slice."""

    market_context: dict[str, float]
    average_fast: float
    declining: float
    below: float
    sector_stress: float
    correlation: float
    vol_ratio: float
    leader_failure: float
    operating_dd: float
    capital_dd: float
    tech_speed: float
    broad_speed: float
    transition_damage: float
    trend_health: float
    breadth20: float
    breadth60: float
    declining_name: float
    declining_group: float
    below_name: float
    below_group: float
    reasons: list[str]
    family_votes: dict[str, bool]
    votes: int
    sector_guard: SectorGuardTransition
    held_damage: list[bool]
    held_ret5: list[float]
    held_damage_ratio: float
    held_loss_ratio: float
    held_repair_ratio: float


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

    present = [
        symbol
        for symbol in REFERENCE_UNIVERSE
        if symbol in reference_panel and date in reference_panel[symbol].index
    ]
    expected = [
        symbol
        for symbol in REFERENCE_UNIVERSE
        if symbol in reference_panel and reference_panel[symbol].index.min() <= date
    ]
    industries = {INDUSTRY.get(symbol, "unknown") for symbol in present}
    expected_industries = {INDUSTRY.get(symbol, "unknown") for symbol in expected} - {"unknown"}
    minimum_symbols = max(3, math.ceil(0.80 * len(expected)))
    minimum_industries = min(5, len(expected_industries))
    if len(present) < minimum_symbols or len(industries - {"unknown"}) < minimum_industries:
        raise RuntimeError("independent risk basket coverage is insufficient")
    market_context = {
        "broad_ret5": scalar(broad.loc[date], "ret5", 0.0),
        "tech_ret5": scalar(tech.loc[date], "ret5", 0.0),
        "broad_ret20": scalar(broad.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "tech_ret20": scalar(tech.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "broad_ret60": scalar(broad.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "tech_ret60": scalar(tech.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "broad_ret120": scalar(broad.loc[date], f"ret{cfg.trend_slow}", 0.0),
        "tech_ret120": scalar(tech.loc[date], f"ret{cfg.trend_slow}", 0.0),
    }
    if not cfg.risk_overlay_enabled:
        account.risk = Risk.NORMAL.value
        account.shock_state = "NONE"
        return RiskAssessment(
            state=Risk.NORMAL,
            target_gross_cap=cfg.max_gross,
            votes=0,
            evidence={
                "counterfactual_risk_overlay_disabled": True,
                "evidence_families": EVIDENCE_FAMILY_MEMBERS,
                "family_votes": {family: False for family in EVIDENCE_FAMILY_MEMBERS},
                "family_vote_count": 0,
                **market_context,
            },
            reasons=("risk overlay disabled for causal counterfactual",),
            shock_state="NONE",
        )
    fast_returns: list[float] = []
    below_ma20: list[bool] = []
    above_ma60: list[bool] = []
    leader_failures: list[bool] = []
    sector_returns: dict[str, list[float]] = {}
    sector_below20: dict[str, list[bool]] = {}
    sector_above60: dict[str, list[bool]] = {}
    for symbol in present:
        row = reference_panel[symbol].loc[date]
        ret5 = scalar(row, "ret5")
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        industry = leaders[symbol].industry if symbol in leaders else INDUSTRY.get(symbol, "unknown")
        if math.isfinite(ret5):
            fast_returns.append(ret5)
            sector_returns.setdefault(industry, []).append(ret5)
        if math.isfinite(close) and math.isfinite(ma20):
            below_ma20.append(close < ma20)
            sector_below20.setdefault(industry, []).append(close < ma20)
        if math.isfinite(close) and math.isfinite(ma60):
            above_ma60.append(close > ma60)
            sector_above60.setdefault(industry, []).append(close > ma60)
        if (
            symbol in leaders
            and leaders[symbol].mature
            and (
                not cfg.same_day_leader_pipeline_enabled
                or account.leader_tenure.get(symbol, 0) >= cfg.leader_tenure_days
            )
        ):
            leader_failures.append(ret5 < -0.06 or (math.isfinite(close) and close < ma20))
    declining_name = float(np.mean(np.array(fast_returns) < 0)) if fast_returns else 0.0
    below_name = float(np.mean(below_ma20)) if below_ma20 else 0.0
    declining_group = (
        float(np.mean([float(np.mean(values)) < 0.0 for values in sector_returns.values()]))
        if sector_returns
        else declining_name
    )
    below_group = (
        float(np.mean([float(np.mean(values)) for values in sector_below20.values()]))
        if sector_below20
        else below_name
    )
    name_weight = cfg.risk_breadth_name_weight
    declining = name_weight * declining_name + (1.0 - name_weight) * declining_group
    below = name_weight * below_name + (1.0 - name_weight) * below_group
    breadth60_name = float(np.mean(above_ma60)) if above_ma60 else 0.0
    breadth60_group = (
        float(np.mean([float(np.mean(values)) for values in sector_above60.values()]))
        if sector_above60
        else breadth60_name
    )
    breadth60 = name_weight * breadth60_name + (1.0 - name_weight) * breadth60_group
    average_fast = float(np.mean(fast_returns)) if fast_returns else 0.0
    stressed_sectors = [float(np.mean(values)) < -0.04 for values in sector_returns.values() if values]
    sector_stress = float(np.mean(stressed_sectors)) if stressed_sectors else 0.0
    returns = (
        reference_returns.loc[:date].tail(61)
        if reference_returns is not None
        else cross_section_returns(reference_panel, date)
    )
    correlation = (
        float(
            returns.tail(cfg.correlation_window)
            .corr()
            .where(~np.eye(len(returns.columns), dtype=bool))
            .stack()
            .median()
        )
        if len(returns.columns) >= 4
        else float("nan")
    )
    if reference_context is not None:
        declining_name = reference_context.name_declining
        declining_group = reference_context.group_declining
        declining = reference_context.declining
        below_name = 1.0 - reference_context.name_breadth20
        below_group = 1.0 - reference_context.group_breadth20
        below = 1.0 - reference_context.breadth20
        breadth60_name = reference_context.name_breadth60
        breadth60_group = reference_context.group_breadth60
        breadth60 = reference_context.breadth60
        sector_stress = reference_context.sector_stress
        correlation = reference_context.median_correlation
    recent_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(10).std(ddof=0))
    normal_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(60).std(ddof=0))
    vol_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
    leader_failure = float(np.mean(leader_failures)) if leader_failures else 0.0
    operating_dd, capital_dd = _portfolio_drawdowns(account, equity)
    tech_speed = min(scalar(tech.loc[date], "ret5", 0.0), scalar(tech.loc[date], "ret10", 0.0))
    broad_speed = min(scalar(broad.loc[date], "ret5", 0.0), scalar(broad.loc[date], "ret10", 0.0))

    breadth20 = 1.0 - below
    previous_breadth20 = account.risk_signal_state.get("breadth20", breadth20)
    previous_breadth60 = account.risk_signal_state.get("breadth60", breadth60)
    breadth_drop = min(
        1.0,
        max(0.0, previous_breadth20 - breadth20) / 0.15
        + 0.5 * max(0.0, previous_breadth60 - breadth60) / 0.15,
    )
    correlation_damage = (
        min(1.0, max(0.0, (correlation - 0.45) / 0.35)) if math.isfinite(correlation) else 0.0
    )
    volatility_damage = min(1.0, max(0.0, (vol_ratio - 1.0) / 1.25))
    transition_damage = min(
        1.0,
        0.22 * (1.0 - breadth20)
        + 0.18 * (1.0 - breadth60)
        + 0.16 * breadth_drop
        + 0.14 * leader_failure
        + 0.10 * correlation_damage
        + 0.10 * volatility_damage
        + 0.10 * sector_stress,
    )
    trend_health = min(
        1.0,
        max(
            0.0,
            0.32 * breadth20
            + 0.28 * breadth60
            + 0.16 * (1.0 - leader_failure)
            + 0.12 * (1.0 - correlation_damage)
            + 0.12 * (1.0 - volatility_damage),
        ),
    )
    account.risk_signal_state.update(
        {
            "breadth20": breadth20,
            "breadth60": breadth60,
            "leader_failure": leader_failure,
            "correlation": correlation if math.isfinite(correlation) else 0.0,
            "volatility_ratio": vol_ratio,
            "transition_damage": transition_damage,
            "trend_health": trend_health,
        }
    )
    choppy_context = account.opportunity in {
        "CHOPPY",
        "WEAK",
    }
    chronic_observed = bool(
        cfg.chronic_overlay_enabled and choppy_context and transition_damage >= cfg.transition_damage_freeze
    )
    account.chronic_streak = account.chronic_streak + 1 if chronic_observed else 0
    if account.chronic_streak >= cfg.chronic_confirm_days:
        account.chronic_level = (
            3
            if transition_damage >= 0.80
            else 2
            if transition_damage >= 0.68
            else max(1, account.chronic_level)
        )
        account.chronic_repair_streak = 0
    elif transition_damage <= cfg.transition_damage_repair:
        account.chronic_repair_streak += 1
        if account.chronic_repair_streak >= cfg.chronic_repair_days:
            account.chronic_level = 0
            account.chronic_repair_streak = 0
    else:
        account.chronic_repair_streak = 0

    reasons: list[str] = []
    market_snapshot = build_base_market_family_snapshot(
        average_fast_return=average_fast,
        declining_ratio=declining,
        below_ma20_ratio=below,
        sector_stress_ratio=sector_stress,
        median_correlation=correlation,
        volatility_ratio=vol_ratio,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        cfg=cfg,
    )
    indicator_state = market_snapshot.with_leadership(
        leader_failure=leader_failure >= 0.50,
    )
    reason_by_indicator = {
        "sector_breadth_shock": "sector breadth shock",
        "below_ma20_structure": "MA20 structural damage",
        "multi_industry_sync": "multi-industry synchronization",
        "correlation_shock": "correlation shock",
        "volatility_shock": "volatility shock",
        "leader_failure": "leader failure",
        "index_velocity": "index velocity shock",
        "live_book_damage": "live book structural damage",
        "capital_damage": "portfolio capital damage",
    }
    for indicator, active in indicator_state.items():
        if active:
            reasons.append(reason_by_indicator[indicator])
    family_votes = _evidence_family_votes(indicator_state)
    votes = (
        sum(family_votes.values()) if cfg.evidence_family_voting_enabled else sum(indicator_state.values())
    )

    sector_guard = update_sector_guard(
        date=date,
        calendar=pd.DatetimeIndex(tech.index),
        panel=user_panel,
        account=account,
        leadership_divergence=(market_context["tech_ret120"] - market_context["broad_ret120"]),
        cfg=cfg,
    )
    if sector_guard.triggered:
        _reset_recovery_owner_rearm(account)
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_on",
                "shock_count": sector_guard.shock_count,
                "leadership_divergence": (market_context["tech_ret120"] - market_context["broad_ret120"]),
                "equal_weight_return": (
                    sector_guard.observation.equal_return if sector_guard.observation is not None else None
                ),
                "exposure_weighted_return": (
                    sector_guard.observation.weighted_return if sector_guard.observation is not None else None
                ),
            }
        )
    if sector_guard.recovered:
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_off",
                "active_sessions": sector_guard.active_sessions,
            }
        )
    held_damage: list[bool] = []
    held_repair: list[bool] = []
    held_loss: list[bool] = []
    held_ret5: list[float] = []
    for symbol, position in account.positions.items():
        frame = user_panel.get(symbol)
        if position.shares <= 0 or frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        held_ret5.append(ret5)
        ret1 = float(frame.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
        held_damage.append(math.isfinite(close) and math.isfinite(ma20) and close < ma20 and ret5 <= -0.05)
        held_loss.append(math.isfinite(close) and close < position.avg_cost)
        held_repair.append(math.isfinite(ret1) and ret1 > 0)
    held_damage_ratio = float(np.mean(held_damage)) if held_damage else 0.0
    held_loss_ratio = float(np.mean(held_loss)) if held_loss else 0.0
    held_repair_ratio = float(np.mean(held_repair)) if held_repair else 0.0
    indicator_state.update(
        live_book_damage=(sector_guard.active or held_damage_ratio >= cfg.concentrated_break_ratio),
        capital_damage=(
            capital_dd >= cfg.capital_budget_level2_dd
            or (operating_dd >= cfg.operating_dd_caution and held_damage_ratio > 0.0)
        ),
    )
    reasons.extend(
        reason_by_indicator[indicator]
        for indicator in ("live_book_damage", "capital_damage")
        if indicator_state[indicator]
    )
    family_votes = _evidence_family_votes(indicator_state)
    votes = (
        sum(family_votes.values())
        if cfg.evidence_family_voting_enabled
        else sum(
            indicator_state[name]
            for name in (
                "sector_breadth_shock",
                "below_ma20_structure",
                "multi_industry_sync",
                "correlation_shock",
                "volatility_shock",
                "leader_failure",
                "index_velocity",
            )
        )
    )
    return MarketBookEvidence(
        market_context=market_context,
        average_fast=average_fast,
        declining=declining,
        below=below,
        sector_stress=sector_stress,
        correlation=correlation,
        vol_ratio=vol_ratio,
        leader_failure=leader_failure,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        transition_damage=transition_damage,
        trend_health=trend_health,
        breadth20=breadth20,
        breadth60=breadth60,
        declining_name=declining_name,
        declining_group=declining_group,
        below_name=below_name,
        below_group=below_group,
        reasons=reasons,
        family_votes=family_votes,
        votes=votes,
        sector_guard=sector_guard,
        held_damage=held_damage,
        held_ret5=held_ret5,
        held_damage_ratio=held_damage_ratio,
        held_loss_ratio=held_loss_ratio,
        held_repair_ratio=held_repair_ratio,
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
    market_book = _assess_market_and_book_evidence(
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
    if isinstance(market_book, RiskAssessment):
        return market_book
    market_context = market_book.market_context
    average_fast = market_book.average_fast
    declining = market_book.declining
    below = market_book.below
    sector_stress = market_book.sector_stress
    correlation = market_book.correlation
    vol_ratio = market_book.vol_ratio
    leader_failure = market_book.leader_failure
    operating_dd = market_book.operating_dd
    capital_dd = market_book.capital_dd
    tech_speed = market_book.tech_speed
    broad_speed = market_book.broad_speed
    transition_damage = market_book.transition_damage
    trend_health = market_book.trend_health
    breadth20 = market_book.breadth20
    breadth60 = market_book.breadth60
    declining_name = market_book.declining_name
    declining_group = market_book.declining_group
    below_name = market_book.below_name
    below_group = market_book.below_group
    reasons = market_book.reasons
    family_votes = market_book.family_votes
    votes = market_book.votes
    sector_guard = market_book.sector_guard
    held_damage = market_book.held_damage
    held_ret5 = market_book.held_ret5
    held_damage_ratio = market_book.held_damage_ratio
    held_loss_ratio = market_book.held_loss_ratio
    held_repair_ratio = market_book.held_repair_ratio
    anchor_assessment = _assess_dynamic_anchors(
        date=date,
        reference_panel=reference_panel,
        leaders=leaders,
        account=account,
        cfg=cfg,
        transition_damage=transition_damage,
        votes=votes,
        update_dynamic_anchors=cast(
            Callable[..., tuple[str, ...]],
            _risk_runtime_seam("_update_dynamic_anchors"),
        ),
    )
    anchor_symbols = anchor_assessment.symbols
    anchor_groups = anchor_assessment.groups
    reference_anchor_armed = anchor_assessment.reference_armed
    reference_anchor_break = anchor_assessment.reference_break
    anchor_break_key = anchor_assessment.break_key
    immediate_reference_break = anchor_assessment.immediate_reference_break
    break_conditions = _assess_break_conditions(
        date=date,
        tech=tech,
        user_panel=user_panel,
        account=account,
        equity=equity,
        cfg=cfg,
        held_damage=held_damage,
        held_damage_ratio=held_damage_ratio,
        held_ret5=held_ret5,
        operating_dd=operating_dd,
        votes=votes,
        sector_stress=sector_stress,
        transition_damage=transition_damage,
        market_context=market_context,
    )
    shock_rearmed = break_conditions.shock_rearmed
    concentrated_structure_break = break_conditions.concentrated_structure_break
    emergency_tail_break = break_conditions.emergency_tail_break
    narrow_anchor_guard = break_conditions.narrow_anchor_guard
    immediate_severe_break = break_conditions.immediate_severe_break
    persistent_market_break = break_conditions.persistent_market_break
    strategic_active = break_conditions.strategic_active
    recovery_anchor_elapsed = break_conditions.recovery_anchor_elapsed
    held_cohort_break_confirmed = break_conditions.held_cohort_break_confirmed
    strategic_current_gross = break_conditions.strategic_current_gross
    strategic_tail_break = break_conditions.strategic_tail_break
    recovery_assessment = _assess_recovery_state(
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
    credible_reserve = recovery_assessment.credible_reserve
    incomplete_universe_tail_break = recovery_assessment.incomplete_universe_tail_break
    reference_anchor_confirmed = recovery_assessment.reference_anchor_confirmed
    capital_impaired_restoration_relapse = recovery_assessment.capital_impaired_restoration_relapse
    market_backed_restoration_relapse = recovery_assessment.market_backed_restoration_relapse
    terminal_market_backed_restoration_relapse = (
        recovery_assessment.terminal_market_backed_restoration_relapse
    )
    capital_drawdown_relapse = recovery_assessment.capital_drawdown_relapse
    concentrated_confirmed = recovery_assessment.concentrated_confirmed
    capital_observation = _observe_capital_budget(
        account=account,
        cfg=cfg,
        sector_guard=sector_guard,
        reference_anchor_break=reference_anchor_break,
        held_damage_ratio=held_damage_ratio,
        transition_damage=transition_damage,
        votes=votes,
        capital_dd=capital_dd,
        operating_dd=operating_dd,
        sector_stress=sector_stress,
        strategic_active=strategic_active,
    )
    independent_damage = capital_observation.independent_damage
    observed_budget_level = capital_observation.observed_budget_level
    strategic_damage_guard = _update_strategic_damage_guard(
        account=account,
        operating_drawdown=operating_dd,
        transition_damage=transition_damage,
        votes=votes,
        cfg=cfg,
    )
    capital_overlays = _apply_capital_overlays(
        account=account,
        cfg=cfg,
        observed_budget_level=observed_budget_level,
        transition_damage=transition_damage,
        votes=votes,
        held_damage_ratio=held_damage_ratio,
        capital_dd=capital_dd,
        operating_dd=operating_dd,
        strategic_damage_guard=strategic_damage_guard,
    )
    strategic_guard_level2_overlay = capital_overlays.strategic_guard_level2_overlay
    freeze_new_risk = capital_overlays.freeze_new_risk
    overlay_cap = capital_overlays.overlay_cap
    overlay_reduction_level = capital_overlays.overlay_reduction_level
    continuous_evidence = {
        "breadth20": breadth20,
        "breadth60": breadth60,
        "name_weighted_declining_ratio": declining_name,
        "group_balanced_declining_ratio": declining_group,
        "name_weighted_below_ma20_ratio": below_name,
        "group_balanced_below_ma20_ratio": below_group,
        "transition_damage": transition_damage,
        "trend_health": trend_health,
        "freeze_new_risk": freeze_new_risk,
        "chronic_level": account.chronic_level,
        "capital_budget_level": account.capital_budget_level,
        "independent_damage": independent_damage,
        "strategic_damage_guard": strategic_damage_guard,
        "strategic_guard_level2_overlay": strategic_guard_level2_overlay,
        "risk_anchor_symbols": list(anchor_symbols),
        "risk_anchor_signature": account.risk_anchor_signature,
        "risk_anchor_group_count": len(anchor_groups),
        "evidence_families": EVIDENCE_FAMILY_MEMBERS,
        "family_votes": family_votes,
        "family_vote_count": votes,
        **(reference_context.evidence() if reference_context is not None else {}),
    }

    transition_short_circuit = _assess_acute_and_cooldown(
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
    if isinstance(transition_short_circuit, RiskAssessment):
        return transition_short_circuit
    previous, acute_sector_evacuation = transition_short_circuit
    protected_recovery = _assess_protected_recovery(
        date=date,
        broad=broad,
        tech=tech,
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
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        credible_reserve=credible_reserve,
        freeze_new_risk=freeze_new_risk,
        overlay_cap=overlay_cap,
        overlay_reduction_level=overlay_reduction_level,
        sector_guard=sector_guard,
        shock_rearmed=shock_rearmed,
        strategic_active=strategic_active,
    )
    if protected_recovery is not None:
        return protected_recovery
    confirmed_break = _assess_confirmed_concentrated_break(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        previous=previous,
        concentrated_confirmed=concentrated_confirmed,
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
        capital_impaired_restoration_relapse=(capital_impaired_restoration_relapse),
        market_backed_restoration_relapse=market_backed_restoration_relapse,
        terminal_market_backed_restoration_relapse=(terminal_market_backed_restoration_relapse),
        incomplete_universe_tail_break=incomplete_universe_tail_break,
        reference_anchor_confirmed=reference_anchor_confirmed,
        held_cohort_break_confirmed=held_cohort_break_confirmed,
        capital_drawdown_relapse=capital_drawdown_relapse,
        immediate_reference_break=immediate_reference_break,
    )
    if confirmed_break is not None:
        return confirmed_break
    transition_resolution = _resolve_risk_transition(
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
    state = transition_resolution.state
    shock = transition_resolution.shock
    cap = transition_resolution.cap
    observation = transition_resolution.observation
    return RiskAssessment(
        state=state,
        target_gross_cap=cap,
        votes=votes,
        evidence={
            **continuous_evidence,
            **market_context,
            "ai_fast_return": average_fast,
            "declining_ratio": declining,
            "below_ma20_ratio": below,
            "sector_stress_ratio": sector_stress,
            "median_correlation": correlation,
            "volatility_ratio": vol_ratio,
            "leader_failure_ratio": leader_failure,
            "held_damage_ratio": held_damage_ratio,
            "held_repair_ratio": held_repair_ratio,
            "tech_speed": tech_speed,
            "broad_speed": broad_speed,
            "operating_drawdown": operating_dd,
            "capital_drawdown": capital_dd,
            "strategic_cohort_active": strategic_active,
            "strategic_current_gross": strategic_current_gross,
            "sector_guard_active": sector_guard.active,
            "acute_sector_evacuation": acute_sector_evacuation,
            "sector_guard_shock_count": sector_guard.shock_count,
            "sector_guard_active_sessions": sector_guard.active_sessions,
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
        reasons=tuple(reasons),
        shock_state=shock,
        freeze_new_risk=freeze_new_risk or state is not Risk.NORMAL,
        reduction_level=max(
            overlay_reduction_level,
            3 if state is Risk.CRISIS else 2 if state is Risk.RISK_OFF else 1 if state is Risk.CAUTION else 0,
        ),
        severity=account.shock_severity,
    )


def assess_risk(
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
) -> RiskAssessment:
    """Return formal uquant risk with the optional freeze-only Sentinel overlay.

    The base assessor remains the sole owner of state, severity, reductions,
    and gross caps.  Integration is deliberately applied only to its immutable
    result, so Sentinel cannot mutate the durable account or create a parallel
    risk transition.
    """

    base = _risk_runtime_seam("_assess_base_risk")(
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
