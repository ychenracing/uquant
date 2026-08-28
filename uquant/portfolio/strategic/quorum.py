"""Independent evidence-family quorum for strategic owner qualification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ...config import SystemConfig
from ...models.strategic_universe import (
    ReferenceAvailability,
    StrategicUniverseRoles,
)
from ...types import LeaderScore, Risk, RiskAssessment


class StrategicQuorumRoute(str, Enum):
    """Supported strategic admission strength."""

    FULL_COHORT = "FULL_COHORT"
    STRONG_PAIR = "STRONG_PAIR"
    ABSOLUTE_SINGLE = "ABSOLUTE_SINGLE"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class StrategicQuorumResult:
    """Auditable outcome across owner, industry, market, and robustness families."""

    route: StrategicQuorumRoute
    qualified: bool
    owner_absolute_quality: bool
    industry_confirmation: bool
    market_confirmation: bool
    robustness_confirmation: bool
    available_industry_references: tuple[str, ...]
    unavailable_references: tuple[str, ...]
    reasons: tuple[str, ...]
    required_confirm_days: int
    restricted_initial_weight: float | None


def _finite(snapshot: dict[str, float], field: str, default: float = -math.inf) -> float:
    value = snapshot.get(field, default)
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) else default


def _liquid(snapshot: dict[str, float]) -> bool:
    return _finite(snapshot, "liquidity_confirmation", 1.0) >= 0.5


def _common_absolute_quality(
    *,
    symbol: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    cfg: SystemConfig,
    minimum_score: float,
    minimum_secular_score: float,
) -> bool:
    snapshot = snapshots.get(symbol)
    leader = leaders.get(symbol)
    if snapshot is None or leader is None:
        return False
    return bool(
        _finite(snapshot, "leader_score") >= minimum_score
        and _finite(snapshot, "leader_confidence") >= cfg.leader_min_confidence
        and _finite(snapshot, "secular_score") >= minimum_secular_score
        and _finite(snapshot, "secular_confidence") >= cfg.strategic_secular_min_confidence
        and _finite(snapshot, "momentum60") >= cfg.strategic_current_factor_floor
        and _finite(snapshot, "momentum120") >= cfg.strategic_current_factor_floor
        and _finite(snapshot, "relative_strength") >= cfg.strategic_current_factor_floor
        and _finite(snapshot, "trend_persistence") >= 2.0 / 3.0
        and _finite(snapshot, "ret120") >= cfg.strategic_long_cycle_min_ret120
        and _liquid(snapshot)
        and leader.confidence >= cfg.leader_min_confidence
        and leader.industry != "unknown"
    )


def _market_confirmation(risk: RiskAssessment, cfg: SystemConfig) -> tuple[bool, bool]:
    keys = (
        "breadth20",
        "broad_ret20",
        "tech_ret20",
        "broad_ret120",
        "tech_ret120",
        "risk_anchor_group_count",
    )
    values: dict[str, float] = {}
    for key in keys:
        raw = risk.evidence.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return False, False
        value = float(raw)
        if not math.isfinite(value):
            return False, False
        values[key] = value
    complete = True
    confirmed = bool(
        risk.state is Risk.NORMAL
        and not risk.freeze_new_risk
        and values["risk_anchor_group_count"] >= cfg.strategic_cohort_min_size
        and values["breadth20"] >= cfg.high_confidence_entry_breadth
        and values["broad_ret20"] >= cfg.strategic_transition_impulse_min_market_ret20
        and values["tech_ret20"] >= cfg.strategic_transition_impulse_min_market_ret20
        and max(values["broad_ret120"], values["tech_ret120"])
        > cfg.recovery_transition_weak_leg_ret120
        and max(values["broad_ret120"], values["tech_ret120"])
        <= cfg.strategic_long_cycle_max_tech_ret120
    )
    return complete, confirmed


def _industry_confirmation(
    *,
    owner_symbol: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    universe: StrategicUniverseRoles,
    cfg: SystemConfig,
) -> tuple[tuple[str, ...], bool]:
    owner_industry = universe.industry_of(owner_symbol)
    if owner_industry == "unknown" and owner_symbol in leaders:
        owner_industry = leaders[owner_symbol].industry
    available = tuple(
        symbol
        for symbol in universe.qualification_reference_symbols
        if symbol != owner_symbol
        and universe.availability(symbol) is ReferenceAvailability.AVAILABLE
        and universe.industry_of(symbol) == owner_industry
        and symbol in snapshots
    )
    confirming = tuple(
        symbol
        for symbol in available
        if _finite(snapshots[symbol], "ret20") >= cfg.strategic_long_cycle_min_ret20
        and _finite(snapshots[symbol], "trend_persistence") >= 0.5
        and _liquid(snapshots[symbol])
    )
    return available, bool(confirming)


def evaluate_strategic_quorum(
    *,
    owner_symbol: str,
    candidate_symbols: tuple[str, ...],
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    cfg: SystemConfig,
    synchronized_full_cohort: bool = False,
) -> StrategicQuorumResult:
    """Evaluate fixed economic gates without turning unavailable data negative."""

    ordered_candidates = tuple(dict.fromkeys(candidate_symbols))
    candidate_count = len(ordered_candidates)
    if owner_symbol not in ordered_candidates:
        ordered_candidates = (owner_symbol, *ordered_candidates)
        candidate_count = len(ordered_candidates)
    if candidate_count >= 3:
        route = StrategicQuorumRoute.FULL_COHORT
        minimum_score = cfg.leader_mature_score
        minimum_secular = cfg.strategic_secular_min_score
        required_days = cfg.strategic_cohort_confirm_days
    elif candidate_count == 2:
        route = StrategicQuorumRoute.STRONG_PAIR
        minimum_score = cfg.strategic_two_name_min_score
        minimum_secular = cfg.strategic_secular_min_score
        required_days = cfg.strategic_two_name_confirm_days
    elif candidate_count == 1:
        route = StrategicQuorumRoute.ABSOLUTE_SINGLE
        minimum_score = cfg.strategic_one_name_min_score
        minimum_secular = cfg.strategic_one_name_min_secular_score
        required_days = cfg.strategic_one_name_confirm_days
    else:
        route = StrategicQuorumRoute.NONE
        minimum_score = cfg.strategic_one_name_min_score
        minimum_secular = cfg.strategic_one_name_min_secular_score
        required_days = cfg.strategic_one_name_confirm_days
    owner_quality = _common_absolute_quality(
        symbol=owner_symbol,
        snapshots=snapshots,
        leaders=leaders,
        cfg=cfg,
        minimum_score=minimum_score,
        minimum_secular_score=minimum_secular,
    )
    candidate_quality = owner_quality and all(
        _common_absolute_quality(
            symbol=symbol,
            snapshots=snapshots,
            leaders=leaders,
            cfg=cfg,
            minimum_score=minimum_score,
            minimum_secular_score=minimum_secular,
        )
        for symbol in ordered_candidates
    )
    available_industry, industry_confirmed = _industry_confirmation(
        owner_symbol=owner_symbol,
        snapshots=snapshots,
        leaders=leaders,
        universe=universe,
        cfg=cfg,
    )
    market_complete, market_confirmed = _market_confirmation(risk, cfg)
    robustness_confirmed = bool(available_industry and market_complete)
    full_compatibility = bool(
        route is StrategicQuorumRoute.FULL_COHORT
        and synchronized_full_cohort
    )
    qualified = bool(
        route is not StrategicQuorumRoute.NONE
        and (
            full_compatibility
            or (
                candidate_quality
                and industry_confirmed
                and robustness_confirmed
                and market_confirmed
            )
        )
    )
    reasons: list[str] = []
    if (not owner_quality or not candidate_quality) and not full_compatibility:
        reasons.append("OWNER_ABSOLUTE_QUALITY")
    if not available_industry and not full_compatibility:
        reasons.append("INDUSTRY_REFERENCE_COVERAGE")
    elif not industry_confirmed and not full_compatibility:
        reasons.append("INDUSTRY_CONFIRMATION")
    if not market_complete and not full_compatibility:
        reasons.append("MARKET_REFERENCE_COVERAGE")
    elif not market_confirmed and not full_compatibility:
        reasons.append("MARKET_CONFIRMATION")
    if not robustness_confirmed:
        reasons.append("ROBUSTNESS_CONFIRMATION")
    unavailable = tuple(
        symbol
        for symbol in universe.qualification_reference_symbols
        if universe.availability(symbol) is ReferenceAvailability.UNAVAILABLE
    )
    restricted = (
        min(cfg.core_admission_weight, cfg.max_symbol_weight, risk.target_gross_cap)
        if route in {
            StrategicQuorumRoute.STRONG_PAIR,
            StrategicQuorumRoute.ABSOLUTE_SINGLE,
        }
        else None
    )
    return StrategicQuorumResult(
        route=route if qualified else StrategicQuorumRoute.NONE,
        qualified=qualified,
        owner_absolute_quality=owner_quality,
        industry_confirmation=industry_confirmed,
        market_confirmation=market_confirmed,
        robustness_confirmation=robustness_confirmed,
        available_industry_references=available_industry,
        unavailable_references=unavailable,
        reasons=tuple(reasons),
        required_confirm_days=required_days,
        restricted_initial_weight=restricted,
    )


__all__ = (
    "StrategicQuorumResult",
    "StrategicQuorumRoute",
    "evaluate_strategic_quorum",
)
