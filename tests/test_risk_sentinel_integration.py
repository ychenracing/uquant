from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from uquant import risk as risk_module
from uquant.config import DEFAULT_CONFIG
from uquant.risk_sentinel.integration import integrate_freeze_only
from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)
from uquant.types import Risk, RiskAssessment

FREEZE_CFG = DEFAULT_CONFIG.override(risk_sentinel_mode="FREEZE_ONLY")


def _coverage(status: WarmupStatus = WarmupStatus.READY) -> CoverageHealth:
    component = 1.0 if status is WarmupStatus.READY else 0.0
    return CoverageHealth(
        status=status,
        confidence=component,
        component_observation=component,
        subindustry_coverage=component,
        held_industry_mapping=component,
        reference_warmup=component,
        missing_indices=(),
        new_symbols=(),
        stale_symbols=(),
    )


def _sentinel(
    *,
    families: tuple[str, ...] = ("breadth_structure", "market_velocity"),
    confidence: float = 0.90,
    confirmation_days: int = 2,
    level: SentinelLevel = SentinelLevel.DEFENSIVE,
    coverage: CoverageHealth | None = None,
) -> SentinelAssessment:
    return SentinelAssessment(
        date="2026-08-19",
        level=level,
        confidence=confidence,
        suggested_gross_cap=0.50,
        freeze_new_risk=True,
        evidence_families=families,
        reasons=tuple(f"{family} active" for family in families),
        first_evidence_date="2026-08-18",
        coverage=coverage or _coverage(),
        metrics={
            "evidence_confirmation_days": float(confirmation_days),
            "broad_fast_return": -0.04,
            "tech_fast_return": -0.05,
            "synchronized_subindustry_damage": 0.60,
        },
    )


def _base(*, active_families: tuple[str, ...] = ()) -> RiskAssessment:
    active = set(active_families)
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=len(active),
        evidence={
            "family_votes": {
                "market_velocity": "market_velocity" in active,
                "breadth_structure": "breadth_structure" in active,
                "covariance_stress": "covariance_stress" in active,
                "leadership_damage": "leadership_damage" in active,
                "live_book_damage": "live_book_damage" in active,
                "capital_damage": "capital_damage" in active,
            },
            "freeze_new_risk": False,
        },
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=False,
        reduction_level=0,
        severity="NORMAL",
    )


def test_shadow_mode_preserves_the_complete_base_assessment() -> None:
    base = _base()

    integrated = integrate_freeze_only(
        base=base,
        sentinel=_sentinel(),
        cfg=DEFAULT_CONFIG.override(risk_sentinel_mode="SHADOW"),
    )

    assert integrated is base


@pytest.mark.parametrize(
    "sentinel",
    (
        _sentinel(confidence=0.79),
        _sentinel(confirmation_days=1),
        _sentinel(coverage=_coverage(WarmupStatus.NOT_READY)),
    ),
)
def test_freeze_requires_ready_confident_confirmed_evidence(
    sentinel: SentinelAssessment,
) -> None:
    base = _base()

    integrated = integrate_freeze_only(
        base=base,
        sentinel=sentinel,
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.target_gross_cap == base.target_gross_cap
    assert integrated.evidence["sentinel_freeze_new_risk"] is False


def test_duplicate_families_do_not_create_incremental_authority() -> None:
    base = _base(active_families=("breadth_structure", "market_velocity"))
    sentinel = replace(_sentinel(), first_evidence_date="2026-08-19")

    integrated = integrate_freeze_only(
        base=base,
        sentinel=sentinel,
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_incremental"] is False
    assert integrated.evidence["combined_family_active"]["breadth_structure"] is True
    assert integrated.evidence["combined_family_active"]["market_velocity"] is True
    assert integrated.evidence["combined_family_vote_count"] == 2
    assert integrated.evidence["sentinel_earlier_families"] == []
    assert integrated.evidence["sentinel_earlier_supported"] is False


def test_incremental_confirmed_evidence_only_sets_freeze() -> None:
    base = _base(active_families=("market_velocity",))

    integrated = integrate_freeze_only(
        base=base,
        sentinel=_sentinel(),
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is True
    assert integrated.target_gross_cap == base.target_gross_cap
    assert integrated.state is base.state
    assert integrated.reduction_level == base.reduction_level
    assert integrated.shock_state == base.shock_state
    assert integrated.severity == base.severity
    assert integrated.evidence["sentinel_incremental"] is True
    assert integrated.evidence["sentinel_incremental_families"] == [
        "breadth_structure"
    ]
    assert integrated.evidence["base_family_active"]["market_velocity"] is True
    assert integrated.evidence["sentinel_family_active"]["market_velocity"] is True
    assert integrated.evidence["first_base_date"] == {}
    assert integrated.evidence["first_sentinel_date"] == "2026-08-18"


def test_critical_velocity_and_breadth_can_trigger_without_confirmation() -> None:
    sentinel = replace(
        _sentinel(confirmation_days=1, level=SentinelLevel.CRITICAL),
        evidence_families=("breadth_structure", "market_velocity", "capital_damage"),
    )

    integrated = integrate_freeze_only(
        base=_base(active_families=("capital_damage",)),
        sentinel=sentinel,
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is True
    assert integrated.evidence["sentinel_severe_direct"] is True


def test_limited_gross_cap_mode_is_not_a_phase4_execution_path() -> None:
    with pytest.raises(RuntimeError, match="not implemented in Phase 4"):
        integrate_freeze_only(
            base=_base(),
            sentinel=_sentinel(),
            cfg=DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP"),
        )


def test_assess_risk_is_the_only_public_freeze_mapping_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base(active_families=("market_velocity",))
    observed: dict[str, object] = {}

    def fake_base(**kwargs: object) -> RiskAssessment:
        observed.update(kwargs)
        return base

    monkeypatch.setattr(risk_module, "_assess_base_risk", fake_base)
    sentinel = _sentinel()
    integrated = risk_module.assess_risk(
        date=pd.Timestamp("2026-08-19"),
        broad=None,  # type: ignore[arg-type]
        tech=None,  # type: ignore[arg-type]
        reference_panel={},
        reference_returns=None,
        user_panel={},
        leaders={},
        account=None,  # type: ignore[arg-type]
        equity=1.0,
        cfg=FREEZE_CFG,
        sentinel_assessment=sentinel,
    )

    assert observed["cfg"] is FREEZE_CFG
    assert "sentinel_assessment" not in observed
    assert integrated.freeze_new_risk is True
    assert integrated.target_gross_cap == base.target_gross_cap
