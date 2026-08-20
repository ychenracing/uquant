"""Freeze-only hand-off from Sentinel evidence to the formal risk authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from uquant.config import SystemConfig
from uquant.types import Opportunity, RiskAssessment

from .models import (
    RISK_FAMILIES,
    RiskEvidenceTimeline,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)


def _family_flags(values: Mapping[str, object] | None) -> dict[str, bool]:
    source = values or {}
    return {family: bool(source.get(family, False)) for family in sorted(RISK_FAMILIES)}


def sentinel_freeze_authorized(risk: RiskAssessment) -> bool:
    """Return whether the formal flag attributes this freeze to Sentinel.

    Evidence is diagnostic only: it can attribute a flag that already exists,
    but it can never manufacture behavioral authority on its own.
    """

    return bool(
        risk.freeze_new_risk
        and risk.evidence.get("sentinel_freeze_new_risk", False)
        and not risk.evidence.get("base_freeze_new_risk", False)
    )


def _severe_direct(assessment: SentinelAssessment, cfg: SystemConfig) -> bool:
    families = set(assessment.evidence_families)
    broad = assessment.metrics.get("broad_fast_return", 0.0)
    tech = assessment.metrics.get("tech_fast_return", 0.0)
    synchronized = assessment.metrics.get("synchronized_subindustry_damage", 0.0)
    severe_velocity = (broad <= -0.025 and tech <= -0.025) or min(broad, tech) <= -0.05
    return bool(
        cfg.risk_sentinel_severe_direct_enabled
        and assessment.level is SentinelLevel.CRITICAL
        and {"market_velocity", "breadth_structure"}.issubset(families)
        and severe_velocity
        and synchronized >= 0.40
    )


@dataclass(frozen=True, slots=True)
class _CausalConfirmation:
    confirmation_days: int = 0
    confirmation_history_trusted: bool = False
    incremental_families: tuple[str, ...] = ()
    earlier_families: tuple[str, ...] = ()
    comparison: str = "not_comparable"
    current_families: tuple[str, ...] = ()
    timeline_aligned: bool = False


def _causal_confirmation(
    *,
    timeline: RiskEvidenceTimeline | None,
    sentinel: SentinelAssessment,
    cfg: SystemConfig,
) -> tuple[bool, _CausalConfirmation]:
    """Validate Stage 6's account-free market timeline for freeze authority.

    The timeline carrier is deliberately rechecked at this boundary.  It must
    end on the live assessment date, contain aligned Sentinel/base market rows,
    and prove either a current incremental family or a strictly earlier family
    observed by both systems.  Account-derived families never enter this path.
    """

    if timeline is None or not timeline.sentinel_rows or not timeline.base_rows:
        return False, _CausalConfirmation()

    current = timeline.sentinel_rows[-1]
    current_base = timeline.base_rows[-1]
    derived_sentinel_first: dict[str, str] = {}
    for sentinel_row in timeline.sentinel_rows:
        if sentinel_row.coverage_status is not WarmupStatus.READY:
            continue
        for family in sentinel_row.active_families:
            derived_sentinel_first.setdefault(family, sentinel_row.date)
    derived_base_first: dict[str, str] = {}
    for base_row in timeline.base_rows:
        if not base_row.data_ready:
            continue
        for family in base_row.active_families:
            derived_base_first.setdefault(family, base_row.date)
    aligned = bool(
        timeline.as_of == sentinel.date
        and current.date == sentinel.date
        and current_base.date == sentinel.date
        and bool(timeline.sessions)
        and timeline.sessions[-1] == sentinel.date
        and len(timeline.sessions) == len(timeline.sentinel_rows)
        and len(timeline.sessions) == len(timeline.base_rows)
        and tuple(row.date for row in timeline.sentinel_rows) == timeline.sessions
        and tuple(row.date for row in timeline.base_rows) == timeline.sessions
        and dict(timeline.sentinel_first_family_dates) == derived_sentinel_first
        and dict(timeline.base_first_family_dates) == derived_base_first
    )
    current_families = set(current.active_families)
    current_base_families = set(current_base.active_families)
    derived_incremental = current_families - current_base_families
    incremental_families = sorted(
        derived_incremental.intersection(timeline.incremental_families)
    )
    aligned = aligned and set(timeline.incremental_families) == derived_incremental

    sentinel_first = dict(timeline.sentinel_first_family_dates)
    base_first = dict(timeline.base_first_family_dates)
    strictly_earlier = {
        family
        for family in current_families
        if family in sentinel_first
        and family in base_first
        and sentinel_first[family] < base_first[family]
    }
    earlier_families = sorted(
        strictly_earlier.intersection(timeline.earlier_families)
        - set(incremental_families)
    )
    comparison = (
        "incremental_same_day"
        if incremental_families
        else "earlier_confirmed"
        if earlier_families
        else "not_comparable"
    )
    diagnostics = _CausalConfirmation(
        confirmation_days=timeline.confirmation_days,
        confirmation_history_trusted=timeline.confirmation_history_trusted,
        incremental_families=tuple(incremental_families),
        earlier_families=tuple(earlier_families),
        comparison=comparison,
        current_families=tuple(sorted(current_families)),
        timeline_aligned=aligned,
    )
    authorized = bool(
        cfg.risk_sentinel_causal_confirmation_enabled
        and aligned
        and current.coverage_status is WarmupStatus.READY
        and current.confidence >= cfg.risk_sentinel_min_confidence
        and current.freeze_candidate
        and len(current_families) >= 2
        and timeline.confirmation_history_trusted
        and timeline.confirmation_days >= cfg.risk_sentinel_confirm_days
        and (incremental_families or earlier_families)
    )
    return authorized, diagnostics


def integrate_freeze_only(
    *,
    base: RiskAssessment,
    sentinel: SentinelAssessment | None,
    cfg: SystemConfig,
    opportunity: Opportunity | str | None = None,
    causal_timeline: RiskEvidenceTimeline | None = None,
) -> RiskAssessment:
    """Overlay only ``freeze_new_risk`` while preserving every base risk output.

    Family flags are OR-combined, never summed.  A Sentinel trigger must be
    ready, confident, confirmed (unless severe-direct), and add a same-day
    family or earlier evidence.  The suggested gross cap remains diagnostic.
    """

    if cfg.risk_sentinel_mode == "SHADOW" or sentinel is None:
        return base
    if str(cfg.risk_sentinel_mode) == "LIMITED_GROSS_CAP":
        raise RuntimeError(
            "LIMITED_GROSS_CAP was rejected by the economic gate; "
            "use FREEZE_ONLY or SHADOW."
        )

    base_active = _family_flags(
        base.evidence.get("family_votes")
        if isinstance(base.evidence.get("family_votes"), Mapping)
        else None
    )
    sentinel_active = {
        family: family in sentinel.evidence_families
        for family in sorted(RISK_FAMILIES)
    }
    combined = {
        family: base_active[family] or sentinel_active[family]
        for family in sorted(RISK_FAMILIES)
    }
    first_sentinel = sentinel.first_evidence_date
    same_day_incremental_families = sorted(
        family
        for family in RISK_FAMILIES
        if sentinel_active[family] and not base_active[family]
    )
    severe_direct = _severe_direct(sentinel, cfg)
    causal_authorized, causal = _causal_confirmation(
        timeline=causal_timeline,
        sentinel=sentinel,
        cfg=cfg,
    )
    incremental_families = list(causal.incremental_families)
    earlier_families = list(causal.earlier_families)
    incremental = bool(incremental_families or earlier_families)
    severe_direct_eligible = bool(
        sentinel.coverage.status is WarmupStatus.READY
        and sentinel.confidence >= cfg.risk_sentinel_min_confidence
        and sentinel.freeze_new_risk
        and len(sentinel.evidence_families) >= 2
        and bool(same_day_incremental_families)
        and severe_direct
    )
    opportunity_value = (
        opportunity.value if isinstance(opportunity, Opportunity) else opportunity
    )
    bull_silent = bool(
        sentinel.level is SentinelLevel.CAUTION
        and opportunity_value == Opportunity.STRONG_TREND.value
        and not any(
            sentinel_active[family]
            for family in (
                "market_velocity",
                "breadth_structure",
                "covariance_stress",
            )
        )
    )
    causal_eligible = bool(
        not base.freeze_new_risk
        and sentinel.coverage.status is WarmupStatus.READY
        and sentinel.confidence >= cfg.risk_sentinel_min_confidence
        and sentinel.freeze_new_risk
        and causal_authorized
    )
    sentinel_freeze = (causal_eligible or severe_direct_eligible) and not bull_silent
    evidence: dict[str, Any] = {
        **base.evidence,
        "base_target_gross_cap": base.target_gross_cap,
        "base_freeze_new_risk": base.freeze_new_risk,
        "base_family_active": base_active,
        "sentinel_family_active": sentinel_active,
        "combined_family_active": combined,
        "combined_family_vote_count": sum(combined.values()),
        "sentinel_incremental": incremental,
        "sentinel_incremental_families": incremental_families,
        "sentinel_earlier_families": earlier_families,
        "first_base_date": {},
        "first_sentinel_date": first_sentinel,
        "sentinel_earlier_supported": bool(causal.earlier_families),
        "sentinel_assessment": sentinel.to_dict(),
        "sentinel_confirmation_days": causal.confirmation_days,
        "sentinel_confirmation_history_trusted": (
            causal.confirmation_history_trusted
        ),
        "sentinel_repair_days_required": cfg.risk_sentinel_repair_days,
        "sentinel_severe_direct": severe_direct,
        "sentinel_bull_silent": bull_silent,
        "sentinel_causal_timeline_aligned": causal.timeline_aligned,
        "sentinel_causal_comparison": causal.comparison,
        "sentinel_causal_current_families": list(causal.current_families),
        "sentinel_causal_confirmation_authorized": causal_authorized,
        "sentinel_freeze_new_risk": sentinel_freeze,
    }
    return replace(
        base,
        evidence=evidence,
        freeze_new_risk=base.freeze_new_risk or sentinel_freeze,
    )
