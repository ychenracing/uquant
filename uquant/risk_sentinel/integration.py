"""Freeze-only hand-off from Sentinel evidence to the formal risk authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from uquant.config import SystemConfig
from uquant.types import Opportunity, RiskAssessment

from .models import RISK_FAMILIES, SentinelAssessment, SentinelLevel, WarmupStatus


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


def integrate_freeze_only(
    *,
    base: RiskAssessment,
    sentinel: SentinelAssessment | None,
    cfg: SystemConfig,
    opportunity: Opportunity | str | None = None,
) -> RiskAssessment:
    """Overlay only ``freeze_new_risk`` while preserving every base risk output.

    Family flags are OR-combined, never summed.  A Sentinel trigger must be
    ready, confident, confirmed (unless severe-direct), and add a same-day
    family or earlier evidence.  The suggested gross cap remains diagnostic.
    """

    if cfg.risk_sentinel_mode == "SHADOW" or sentinel is None:
        return base
    if cfg.risk_sentinel_mode == "LIMITED_GROSS_CAP":
        raise RuntimeError("LIMITED_GROSS_CAP is not implemented in Phase 4")

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
    incremental_families = sorted(
        family
        for family in RISK_FAMILIES
        if sentinel_active[family] and not base_active[family]
    )
    # Phase 4 has no persisted, point-in-time family history for base risk or
    # Sentinel.  Never infer an earlier vote from today's membership/holdings.
    # The diagnostic remains explicit and fail-closed until such a carrier is
    # introduced in a later phase.
    earlier_families: list[str] = []
    incremental = bool(incremental_families or earlier_families)
    severe_direct = _severe_direct(sentinel, cfg)
    confirmation_days = int(sentinel.metrics.get("evidence_confirmation_days", 0.0))
    confirmation_history_trusted = bool(
        sentinel.metrics.get("confirmation_history_trusted", 0.0) == 1.0
    )
    enough_families = len(sentinel.evidence_families) >= 2
    eligible = bool(
        sentinel.coverage.status is WarmupStatus.READY
        and sentinel.confidence >= cfg.risk_sentinel_min_confidence
        and sentinel.freeze_new_risk
        and enough_families
        and incremental
        and (
            (
                confirmation_history_trusted
                and confirmation_days >= cfg.risk_sentinel_confirm_days
            )
            or severe_direct
        )
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
    sentinel_freeze = eligible and not bull_silent
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
        "sentinel_earlier_supported": False,
        "sentinel_assessment": sentinel.to_dict(),
        "sentinel_confirmation_days": confirmation_days,
        "sentinel_confirmation_history_trusted": confirmation_history_trusted,
        "sentinel_repair_days_required": cfg.risk_sentinel_repair_days,
        "sentinel_severe_direct": severe_direct,
        "sentinel_bull_silent": bull_silent,
        "sentinel_freeze_new_risk": sentinel_freeze,
        "freeze_new_risk": base.freeze_new_risk or sentinel_freeze,
    }
    return replace(
        base,
        evidence=evidence,
        freeze_new_risk=base.freeze_new_risk or sentinel_freeze,
    )
