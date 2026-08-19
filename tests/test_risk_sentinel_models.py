from __future__ import annotations

import math

import pytest

from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)


def _coverage() -> CoverageHealth:
    return CoverageHealth(
        status=WarmupStatus.READY,
        confidence=1.0,
        component_observation=1.0,
        subindustry_coverage=1.0,
        held_industry_mapping=1.0,
        reference_warmup=1.0,
        missing_indices=(),
        new_symbols=(),
        stale_symbols=(),
    )


def test_assessment_canonicalizes_reason_and_family_order() -> None:
    assessment = SentinelAssessment(
        date="2026-08-19",
        level=SentinelLevel.CAUTION,
        confidence=0.8,
        suggested_gross_cap=0.75,
        freeze_new_risk=True,
        evidence_families=("live_book_damage", "breadth_structure"),
        reasons=("weak live book", "broad damage"),
        first_evidence_date="2026-08-18",
        coverage=_coverage(),
        metrics={"z_metric": 2.0, "a_metric": 1.0},
    )

    assert assessment.evidence_families == (
        "breadth_structure",
        "live_book_damage",
    )
    assert assessment.reasons == ("broad damage", "weak live book")
    assert list(assessment.to_dict()["metrics"]) == ["a_metric", "z_metric"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("confidence", math.nan),
        ("suggested_gross_cap", -0.01),
        ("suggested_gross_cap", 1.01),
        ("suggested_gross_cap", math.inf),
    ],
)
def test_assessment_rejects_nonfinite_or_out_of_range_values(
    field: str,
    value: float,
) -> None:
    values = {
        "date": "2026-08-19",
        "level": SentinelLevel.CAUTION,
        "confidence": 0.8,
        "suggested_gross_cap": 0.75,
        "freeze_new_risk": True,
        "evidence_families": ("breadth_structure",),
        "reasons": ("broad damage",),
        "first_evidence_date": "2026-08-18",
        "coverage": _coverage(),
        "metrics": {"equal_fast_return": -0.03},
    }
    values[field] = value

    with pytest.raises(ValueError):
        SentinelAssessment(**values)  # type: ignore[arg-type]


def test_assessment_rejects_nonfinite_metrics() -> None:
    with pytest.raises(ValueError, match="metric"):
        SentinelAssessment(
            date="2026-08-19",
            level=SentinelLevel.CAUTION,
            confidence=0.8,
            suggested_gross_cap=0.75,
            freeze_new_risk=True,
            evidence_families=("breadth_structure",),
            reasons=("broad damage",),
            first_evidence_date="2026-08-18",
            coverage=_coverage(),
            metrics={"bad": math.nan},
        )


def test_not_ready_cannot_claim_normal_safety() -> None:
    with pytest.raises(ValueError, match="NOT_READY"):
        SentinelAssessment(
            date="2026-08-19",
            level=SentinelLevel.NOT_READY,
            confidence=0.2,
            suggested_gross_cap=1.0,
            freeze_new_risk=False,
            evidence_families=(),
            reasons=("coverage unavailable",),
            first_evidence_date=None,
            coverage=_coverage(),
            metrics={},
        )


def test_coverage_rejects_weighted_confidence_drift() -> None:
    with pytest.raises(ValueError, match="formula"):
        CoverageHealth(
            status=WarmupStatus.READY,
            confidence=0.99,
            component_observation=1.0,
            subindustry_coverage=1.0,
            held_industry_mapping=1.0,
            reference_warmup=1.0,
            missing_indices=(),
            new_symbols=(),
            stale_symbols=(),
        )
