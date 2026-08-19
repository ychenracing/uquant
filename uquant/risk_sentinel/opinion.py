"""Standardized, observation-only Sentinel opinion."""

from __future__ import annotations

from .evidence import MarketEvidence
from .models import CoverageHealth, SentinelAssessment, SentinelLevel, WarmupStatus


def build_risk_opinion(
    *,
    evidence: MarketEvidence,
    coverage: CoverageHealth,
) -> SentinelAssessment:
    """Map independent family votes to a non-executing risk opinion."""

    if coverage.status is WarmupStatus.NOT_READY:
        return SentinelAssessment(
            date=evidence.date,
            level=SentinelLevel.NOT_READY,
            confidence=coverage.confidence,
            suggested_gross_cap=None,
            freeze_new_risk=True,
            evidence_families=evidence.families,
            reasons=("coverage is not ready; manual review required",),
            first_evidence_date=evidence.first_evidence_date,
            coverage=coverage,
            metrics=evidence.metrics,
        )
    families = evidence.families
    if coverage.status is WarmupStatus.DEGRADED and not families:
        return SentinelAssessment(
            date=evidence.date,
            level=SentinelLevel.CAUTION,
            confidence=coverage.confidence,
            suggested_gross_cap=None,
            freeze_new_risk=True,
            evidence_families=(),
            reasons=("coverage is degraded; manual review required",),
            first_evidence_date=None,
            coverage=coverage,
            metrics=evidence.metrics,
        )
    severity = len(families) + int("capital_damage" in families)
    if severity >= 4 or (
        "capital_damage" in families and len(families) >= 2
    ):
        level = SentinelLevel.CRITICAL
        cap = 0.25
    elif severity >= 2:
        level = SentinelLevel.DEFENSIVE
        cap = 0.50
    elif severity == 1:
        level = SentinelLevel.CAUTION
        cap = 0.75
    else:
        level = SentinelLevel.NORMAL
        cap = None
    reasons = (
        tuple(evidence.family_reasons[family] for family in families)
        if families
        else ("no independent risk family triggered",)
    )
    return SentinelAssessment(
        date=evidence.date,
        level=level,
        confidence=coverage.confidence,
        suggested_gross_cap=cap,
        freeze_new_risk=level is not SentinelLevel.NORMAL,
        evidence_families=families,
        reasons=reasons,
        first_evidence_date=evidence.first_evidence_date,
        coverage=coverage,
        metrics=evidence.metrics,
    )
