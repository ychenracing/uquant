"""Market, breadth, sector, and live-book risk evidence stages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import cross_section_returns, scalar
from ..leader import INDUSTRY, REFERENCE_UNIVERSE
from ..market_risk import (
    EVIDENCE_FAMILY_MEMBERS,
    build_base_market_family_snapshot,
    evidence_family_votes,
)
from ..reference import ReferenceContext
from ..risk_sector import SectorGuardTransition, update_sector_guard
from ..types import AccountState, LeaderScore, Risk, RiskAssessment
from .capital import portfolio_drawdowns
from .recovery_state import reset_recovery_owner_rearm


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


def _present_reference_symbols(
    date: pd.Timestamp,
    reference_panel: dict[str, pd.DataFrame],
) -> list[str]:
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
    if len(present) < max(3, math.ceil(0.80 * len(expected))) or len(industries - {"unknown"}) < min(
        5, len(expected_industries)
    ):
        raise RuntimeError("independent risk basket coverage is insufficient")
    return present


def _market_context(
    date: pd.Timestamp, broad: pd.DataFrame, tech: pd.DataFrame, cfg: SystemConfig
) -> dict[str, float]:
    return {
        "broad_ret5": scalar(broad.loc[date], "ret5", 0.0),
        "tech_ret5": scalar(tech.loc[date], "ret5", 0.0),
        "broad_ret20": scalar(broad.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "tech_ret20": scalar(tech.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "broad_ret60": scalar(broad.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "tech_ret60": scalar(tech.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "broad_ret120": scalar(broad.loc[date], f"ret{cfg.trend_slow}", 0.0),
        "tech_ret120": scalar(tech.loc[date], f"ret{cfg.trend_slow}", 0.0),
    }


def _disabled_overlay_assessment(
    account: AccountState, cfg: SystemConfig, market_context: dict[str, float]
) -> RiskAssessment:
    account.risk = Risk.NORMAL.value
    account.shock_state = "NONE"
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=cfg.max_gross,
        votes=0,
        evidence={
            "counterfactual_risk_overlay_disabled": True,
            "evidence_families": dict(EVIDENCE_FAMILY_MEMBERS),
            "family_votes": {family: False for family in EVIDENCE_FAMILY_MEMBERS},
            "family_vote_count": 0,
            **market_context,
        },
        reasons=("risk overlay disabled for causal counterfactual",),
        shock_state="NONE",
    )


@dataclass(slots=True)
class _ReferenceObservations:
    fast_returns: list[float]
    below_ma20: list[bool]
    above_ma60: list[bool]
    leader_failures: list[bool]
    sector_returns: dict[str, list[float]]
    sector_below20: dict[str, list[bool]]
    sector_above60: dict[str, list[bool]]


def _collect_reference_observations(
    *,
    present: list[str],
    date: pd.Timestamp,
    reference_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    cfg: SystemConfig,
) -> _ReferenceObservations:
    result = _ReferenceObservations([], [], [], [], {}, {}, {})
    for symbol in present:
        row = reference_panel[symbol].loc[date]
        ret5 = scalar(row, "ret5")
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        industry = leaders[symbol].industry if symbol in leaders else INDUSTRY.get(symbol, "unknown")
        if math.isfinite(ret5):
            result.fast_returns.append(ret5)
            result.sector_returns.setdefault(industry, []).append(ret5)
        if math.isfinite(close) and math.isfinite(ma20):
            result.below_ma20.append(close < ma20)
            result.sector_below20.setdefault(industry, []).append(close < ma20)
        if math.isfinite(close) and math.isfinite(ma60):
            result.above_ma60.append(close > ma60)
            result.sector_above60.setdefault(industry, []).append(close > ma60)
        if (
            symbol in leaders
            and leaders[symbol].mature
        ):
            result.leader_failures.append(ret5 < -0.06 or (math.isfinite(close) and close < ma20))
    return result


@dataclass(frozen=True, slots=True)
class _BreadthMetrics:
    average_fast: float
    declining: float
    below: float
    breadth60: float
    declining_name: float
    declining_group: float
    below_name: float
    below_group: float
    sector_stress: float
    leader_failure: float


def _breadth_metrics(observed: _ReferenceObservations, cfg: SystemConfig) -> _BreadthMetrics:
    declining_name = float(np.mean(np.array(observed.fast_returns) < 0)) if observed.fast_returns else 0.0
    below_name = float(np.mean(observed.below_ma20)) if observed.below_ma20 else 0.0
    declining_group = (
        float(np.mean([float(np.mean(values)) < 0.0 for values in observed.sector_returns.values()]))
        if observed.sector_returns
        else declining_name
    )
    below_group = (
        float(np.mean([float(np.mean(values)) for values in observed.sector_below20.values()]))
        if observed.sector_below20
        else below_name
    )
    breadth60_name = float(np.mean(observed.above_ma60)) if observed.above_ma60 else 0.0
    breadth60_group = (
        float(np.mean([float(np.mean(values)) for values in observed.sector_above60.values()]))
        if observed.sector_above60
        else breadth60_name
    )
    weight = cfg.risk_breadth_name_weight
    stressed = [float(np.mean(values)) < -0.04 for values in observed.sector_returns.values() if values]
    return _BreadthMetrics(
        average_fast=float(np.mean(observed.fast_returns)) if observed.fast_returns else 0.0,
        declining=weight * declining_name + (1.0 - weight) * declining_group,
        below=weight * below_name + (1.0 - weight) * below_group,
        breadth60=weight * breadth60_name + (1.0 - weight) * breadth60_group,
        declining_name=declining_name,
        declining_group=declining_group,
        below_name=below_name,
        below_group=below_group,
        sector_stress=float(np.mean(stressed)) if stressed else 0.0,
        leader_failure=(float(np.mean(observed.leader_failures)) if observed.leader_failures else 0.0),
    )


def _reference_correlation(
    *,
    date: pd.Timestamp,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    cfg: SystemConfig,
) -> float:
    returns = (
        reference_returns.loc[:date].tail(61)
        if reference_returns is not None
        else cross_section_returns(reference_panel, date)
    )
    if len(returns.columns) < 4:
        return float("nan")
    return float(
        returns.tail(cfg.correlation_window)
        .corr()
        .where(~np.eye(len(returns.columns), dtype=bool))
        .stack()
        .median()
    )


def _reference_context_metrics(
    calculated: _BreadthMetrics,
    correlation: float,
    context: ReferenceContext | None,
) -> tuple[_BreadthMetrics, float]:
    if context is None:
        return calculated, correlation
    return (
        _BreadthMetrics(
            average_fast=calculated.average_fast,
            declining=context.declining,
            below=1.0 - context.breadth20,
            breadth60=context.breadth60,
            declining_name=context.name_declining,
            declining_group=context.group_declining,
            below_name=1.0 - context.name_breadth20,
            below_group=1.0 - context.group_breadth20,
            sector_stress=context.sector_stress,
            leader_failure=calculated.leader_failure,
        ),
        context.median_correlation,
    )


def _transition_health(
    *,
    account: AccountState,
    cfg: SystemConfig,
    breadth20: float,
    breadth60: float,
    leader_failure: float,
    correlation: float,
    vol_ratio: float,
    sector_stress: float,
) -> tuple[float, float]:
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
    damage = min(
        1.0,
        0.22 * (1.0 - breadth20)
        + 0.18 * (1.0 - breadth60)
        + 0.16 * breadth_drop
        + 0.14 * leader_failure
        + 0.10 * correlation_damage
        + 0.10 * volatility_damage
        + 0.10 * sector_stress,
    )
    health = min(
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
            "transition_damage": damage,
            "trend_health": health,
        }
    )
    _update_chronic_overlay(account, cfg=cfg, transition_damage=damage)
    return damage, health


def _update_chronic_overlay(account: AccountState, *, cfg: SystemConfig, transition_damage: float) -> None:
    chronic_observed = bool(
        cfg.chronic_overlay_enabled
        and account.opportunity in {"CHOPPY", "WEAK"}
        and transition_damage >= cfg.transition_damage_freeze
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


_REASON_BY_INDICATOR = MappingProxyType(
    {
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
)


@dataclass(slots=True)
class _VotingState:
    reasons: list[str]
    indicators: dict[str, bool]
    family_votes: dict[str, bool]
    votes: int


def _market_voting_state(
    *,
    metrics: _BreadthMetrics,
    correlation: float,
    vol_ratio: float,
    tech_speed: float,
    broad_speed: float,
    cfg: SystemConfig,
) -> _VotingState:
    snapshot = build_base_market_family_snapshot(
        average_fast_return=metrics.average_fast,
        declining_ratio=metrics.declining,
        below_ma20_ratio=metrics.below,
        sector_stress_ratio=metrics.sector_stress,
        median_correlation=correlation,
        volatility_ratio=vol_ratio,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        cfg=cfg,
    )
    indicators = snapshot.with_leadership(leader_failure=metrics.leader_failure >= 0.50)
    reasons = [_REASON_BY_INDICATOR[indicator] for indicator, active in indicators.items() if active]
    families = evidence_family_votes(indicators)
    votes = sum(indicators.values())
    return _VotingState(reasons, indicators, families, votes)


def _sector_guard_state(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    market_context: dict[str, float],
    cfg: SystemConfig,
) -> SectorGuardTransition:
    guard = update_sector_guard(
        date=date,
        calendar=pd.DatetimeIndex(tech.index),
        panel=user_panel,
        account=account,
        leadership_divergence=(market_context["tech_ret120"] - market_context["broad_ret120"]),
        cfg=cfg,
    )
    if guard.triggered:
        reset_recovery_owner_rearm(account)
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_on",
                "shock_count": guard.shock_count,
                "leadership_divergence": (market_context["tech_ret120"] - market_context["broad_ret120"]),
                "equal_weight_return": (
                    guard.observation.equal_return if guard.observation is not None else None
                ),
                "exposure_weighted_return": (
                    guard.observation.weighted_return if guard.observation is not None else None
                ),
            }
        )
    if guard.recovered:
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_off",
                "active_sessions": guard.active_sessions,
            }
        )
    return guard


@dataclass(frozen=True, slots=True)
class _HeldBookState:
    damage: list[bool]
    ret5: list[float]
    damage_ratio: float
    loss_ratio: float
    repair_ratio: float


def _held_book_state(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    cfg: SystemConfig,
) -> _HeldBookState:
    damage: list[bool] = []
    repair: list[bool] = []
    loss: list[bool] = []
    returns: list[float] = []
    for symbol, position in account.positions.items():
        frame = user_panel.get(symbol)
        if position.shares <= 0 or frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        returns.append(ret5)
        ret1 = float(frame.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
        damage.append(math.isfinite(close) and math.isfinite(ma20) and close < ma20 and ret5 <= -0.05)
        loss.append(math.isfinite(close) and close < position.avg_cost)
        repair.append(math.isfinite(ret1) and ret1 > 0)
    return _HeldBookState(
        damage=damage,
        ret5=returns,
        damage_ratio=float(np.mean(damage)) if damage else 0.0,
        loss_ratio=float(np.mean(loss)) if loss else 0.0,
        repair_ratio=float(np.mean(repair)) if repair else 0.0,
    )


def _apply_live_book_votes(
    state: _VotingState,
    *,
    held: _HeldBookState,
    guard: SectorGuardTransition,
    operating_dd: float,
    capital_dd: float,
    cfg: SystemConfig,
) -> None:
    state.indicators.update(
        live_book_damage=(guard.active or held.damage_ratio >= cfg.concentrated_break_ratio),
        capital_damage=(
            capital_dd >= cfg.capital_budget_level2_dd
            or (operating_dd >= cfg.operating_dd_caution and held.damage_ratio > 0.0)
        ),
    )
    state.reasons.extend(
        _REASON_BY_INDICATOR[indicator]
        for indicator in ("live_book_damage", "capital_damage")
        if state.indicators[indicator]
    )
    state.family_votes = evidence_family_votes(state.indicators)
    state.votes = sum(
        state.indicators[name]
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


def assess_market_and_book_evidence(
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

    present = _present_reference_symbols(date, reference_panel)
    market_context = _market_context(date, broad, tech, cfg)
    if not cfg.risk_overlay_enabled:
        return _disabled_overlay_assessment(account, cfg, market_context)
    observed = _collect_reference_observations(
        present=present,
        date=date,
        reference_panel=reference_panel,
        leaders=leaders,
        account=account,
        cfg=cfg,
    )
    metrics = _breadth_metrics(observed, cfg)
    correlation = _reference_correlation(
        date=date,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        cfg=cfg,
    )
    metrics, correlation = _reference_context_metrics(metrics, correlation, reference_context)
    recent_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(10).std(ddof=0))
    normal_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(60).std(ddof=0))
    vol_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
    operating_dd, capital_dd = portfolio_drawdowns(account, equity)
    tech_speed = min(scalar(tech.loc[date], "ret5", 0.0), scalar(tech.loc[date], "ret10", 0.0))
    broad_speed = min(scalar(broad.loc[date], "ret5", 0.0), scalar(broad.loc[date], "ret10", 0.0))
    breadth20 = 1.0 - metrics.below
    transition_damage, trend_health = _transition_health(
        account=account,
        cfg=cfg,
        breadth20=breadth20,
        breadth60=metrics.breadth60,
        leader_failure=metrics.leader_failure,
        correlation=correlation,
        vol_ratio=vol_ratio,
        sector_stress=metrics.sector_stress,
    )
    voting = _market_voting_state(
        metrics=metrics,
        correlation=correlation,
        vol_ratio=vol_ratio,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        cfg=cfg,
    )
    sector_guard = _sector_guard_state(
        date=date,
        tech=tech,
        user_panel=user_panel,
        account=account,
        market_context=market_context,
        cfg=cfg,
    )
    held = _held_book_state(date=date, user_panel=user_panel, account=account, cfg=cfg)
    _apply_live_book_votes(
        voting,
        held=held,
        guard=sector_guard,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        cfg=cfg,
    )
    return MarketBookEvidence(
        market_context=market_context,
        average_fast=metrics.average_fast,
        declining=metrics.declining,
        below=metrics.below,
        sector_stress=metrics.sector_stress,
        correlation=correlation,
        vol_ratio=vol_ratio,
        leader_failure=metrics.leader_failure,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        transition_damage=transition_damage,
        trend_health=trend_health,
        breadth20=breadth20,
        breadth60=metrics.breadth60,
        declining_name=metrics.declining_name,
        declining_group=metrics.declining_group,
        below_name=metrics.below_name,
        below_group=metrics.below_group,
        reasons=voting.reasons,
        family_votes=voting.family_votes,
        votes=voting.votes,
        sector_guard=sector_guard,
        held_damage=held.damage,
        held_ret5=held.ret5,
        held_damage_ratio=held.damage_ratio,
        held_loss_ratio=held.loss_ratio,
        held_repair_ratio=held.repair_ratio,
    )
