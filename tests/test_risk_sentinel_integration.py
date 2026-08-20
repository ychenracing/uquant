from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from uquant import risk as risk_module
from uquant.config import DEFAULT_CONFIG
from uquant.risk_sentinel.integration import integrate_freeze_only
from uquant.risk_sentinel.models import (
    BaseMarketRiskRow,
    CoverageHealth,
    RiskEvidenceTimeline,
    SentinelAssessment,
    SentinelLevel,
    SentinelMarketRow,
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
            "confirmation_history_trusted": 1.0,
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


def _causal_timeline() -> RiskEvidenceTimeline:
    market_pairs = (
        ("breadth_structure", True),
        ("covariance_stress", False),
        ("market_velocity", True),
    )
    base_pairs = (
        ("breadth_structure", False),
        ("covariance_stress", False),
        ("market_velocity", True),
    )
    sentinel_rows = tuple(
        SentinelMarketRow(
            date=date,
            coverage_status=WarmupStatus.READY,
            confidence=0.90,
            level=SentinelLevel.DEFENSIVE,
            freeze_candidate=True,
            family_active=market_pairs,
            reasons=("breadth structure deteriorated", "market velocity deteriorated"),
            weakest_subindustries=("design",),
        )
        for date in ("2026-08-18", "2026-08-19")
    )
    base_rows = tuple(
        BaseMarketRiskRow(date=date, family_active=base_pairs, data_ready=True)
        for date in ("2026-08-18", "2026-08-19")
    )
    return RiskEvidenceTimeline(
        as_of="2026-08-19",
        sessions=("2026-08-18", "2026-08-19"),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        sentinel_first_family_dates=(
            ("breadth_structure", "2026-08-18"),
            ("market_velocity", "2026-08-18"),
        ),
        base_first_family_dates=(("market_velocity", "2026-08-18"),),
        incremental_families=("breadth_structure",),
        earlier_families=("breadth_structure",),
        confirmation_days=2,
        repair_days=0,
        effective_level=SentinelLevel.DEFENSIVE,
        confirmed_since="2026-08-18",
        confirmation_history_trusted=True,
        trust_reasons=(),
    )


def _integrate_causal(
    *,
    base: RiskAssessment,
    cfg,
    timeline: RiskEvidenceTimeline | None = None,
    sentinel: SentinelAssessment | None = None,
) -> RiskAssessment:
    return integrate_freeze_only(
        base=base,
        sentinel=sentinel or _sentinel(),
        cfg=cfg,
        causal_timeline=timeline or _causal_timeline(),
    )


def _timeline_with_current(**changes: object) -> RiskEvidenceTimeline:
    timeline = _causal_timeline()
    current = replace(timeline.sentinel_rows[-1], **changes)
    return replace(
        timeline,
        sentinel_rows=(*timeline.sentinel_rows[:-1], current),
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


def test_untrusted_confirmation_history_cannot_authorize_freeze() -> None:
    sentinel = _sentinel()
    sentinel = replace(
        sentinel,
        metrics={**sentinel.metrics, "confirmation_history_trusted": 0.0},
    )

    integrated = integrate_freeze_only(
        base=_base(active_families=("market_velocity",)),
        sentinel=sentinel,
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_confirmation_history_trusted"] is False


def test_causal_history_diagnostics_have_no_phase6_authority() -> None:
    sentinel = replace(
        _sentinel(),
        metrics={
            **_sentinel().metrics,
            "confirmation_history_trusted": 0.0,
            "causal_confirmation_history_trusted": 1.0,
            "causal_confirmation_days": 20.0,
        },
    )

    integrated = integrate_freeze_only(
        base=_base(active_families=("market_velocity",)),
        sentinel=sentinel,
        cfg=FREEZE_CFG,
    )

    assert FREEZE_CFG.risk_sentinel_causal_confirmation_enabled is False
    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_freeze_new_risk"] is False


def test_causal_history_authority_requires_the_locked_enable_switch() -> None:
    base = _base(active_families=("market_velocity",))

    disabled = _integrate_causal(base=base, cfg=FREEZE_CFG)
    enabled = _integrate_causal(
        base=base,
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
    )

    assert disabled.freeze_new_risk is False
    assert enabled.freeze_new_risk is True
    assert enabled.target_gross_cap == base.target_gross_cap
    assert enabled.state is base.state
    assert enabled.reduction_level == base.reduction_level
    assert enabled.shock_state == base.shock_state
    assert enabled.severity == base.severity
    assert enabled.evidence["sentinel_incremental_families"] == [
        "breadth_structure"
    ]
    assert enabled.evidence["sentinel_earlier_families"] == []
    assert enabled.evidence["sentinel_causal_comparison"] == "incremental_same_day"
    assert enabled.evidence["sentinel_confirmation_days"] == 2
    assert enabled.evidence["sentinel_confirmation_history_trusted"] is True


@pytest.mark.parametrize(
    "timeline",
    (
        _timeline_with_current(coverage_status=WarmupStatus.NOT_READY),
        _timeline_with_current(confidence=0.79),
        _timeline_with_current(
            family_active=(
                ("breadth_structure", False),
                ("covariance_stress", False),
                ("market_velocity", True),
            )
        ),
        _timeline_with_current(freeze_candidate=False),
        replace(_causal_timeline(), confirmation_history_trusted=False),
        replace(_causal_timeline(), confirmation_days=1),
        replace(
            _causal_timeline(),
            incremental_families=(),
            earlier_families=(),
        ),
        replace(_causal_timeline(), as_of="2026-08-18"),
    ),
    ids=(
        "coverage-not-ready",
        "confidence-below-lock",
        "one-comparable-family",
        "current-freeze-not-requested",
        "untrusted-history",
        "one-confirmation-day",
        "no-comparable-advantage",
        "misaligned-as-of",
    ),
)
def test_causal_authority_fails_closed_when_any_locked_condition_is_missing(
    timeline: RiskEvidenceTimeline,
) -> None:
    integrated = _integrate_causal(
        base=_base(active_families=("market_velocity",)),
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
        timeline=timeline,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_freeze_new_risk"] is False


def test_causal_authority_accepts_only_strictly_comparable_earlier_family() -> None:
    timeline = _causal_timeline()
    current_base = replace(
        timeline.base_rows[-1],
        family_active=(
            ("breadth_structure", True),
            ("covariance_stress", False),
            ("market_velocity", True),
        ),
    )
    timeline = replace(
        timeline,
        base_rows=(*timeline.base_rows[:-1], current_base),
        base_first_family_dates=(
            ("breadth_structure", "2026-08-19"),
            ("market_velocity", "2026-08-18"),
        ),
        incremental_families=(),
        earlier_families=("breadth_structure",),
    )

    integrated = _integrate_causal(
        base=_base(active_families=("breadth_structure", "market_velocity")),
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
        timeline=timeline,
    )

    assert integrated.freeze_new_risk is True
    assert integrated.evidence["sentinel_incremental_families"] == []
    assert integrated.evidence["sentinel_earlier_families"] == [
        "breadth_structure"
    ]
    assert integrated.evidence["sentinel_causal_comparison"] == "earlier_confirmed"


def test_current_account_damage_cannot_substitute_for_two_market_families() -> None:
    timeline = _timeline_with_current(
        family_active=(
            ("breadth_structure", False),
            ("covariance_stress", False),
            ("market_velocity", True),
        )
    )
    sentinel = _sentinel(
        families=("capital_damage", "live_book_damage", "market_velocity")
    )

    integrated = _integrate_causal(
        base=_base(),
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
        timeline=timeline,
        sentinel=sentinel,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_causal_current_families"] == [
        "market_velocity"
    ]


@pytest.mark.parametrize(
    "sentinel",
    (
        _sentinel(confidence=0.79),
        _sentinel(coverage=_coverage(WarmupStatus.NOT_READY)),
    ),
    ids=("live-confidence-below-lock", "live-coverage-not-ready"),
)
def test_current_sentinel_health_must_also_be_ready_and_confident(
    sentinel: SentinelAssessment,
) -> None:
    integrated = _integrate_causal(
        base=_base(),
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
        sentinel=sentinel,
    )

    assert integrated.freeze_new_risk is False
    assert integrated.evidence["sentinel_freeze_new_risk"] is False


def test_base_freeze_is_never_attributed_to_causal_sentinel_authority() -> None:
    base = replace(_base(), freeze_new_risk=True)

    integrated = _integrate_causal(
        base=base,
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
    )

    assert integrated.freeze_new_risk is True
    assert integrated.evidence["base_freeze_new_risk"] is True
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


def test_ineligible_overlay_preserves_base_freeze_evidence_semantics() -> None:
    """Diagnostics must not turn a CAUTION state flag into a strategy freeze."""

    base = replace(
        _base(active_families=("breadth_structure", "market_velocity")),
        state=Risk.CAUTION,
        freeze_new_risk=True,
        reduction_level=1,
    )

    integrated = integrate_freeze_only(
        base=base,
        sentinel=_sentinel(),
        cfg=FREEZE_CFG,
    )

    assert integrated.freeze_new_risk is True
    assert integrated.evidence["base_freeze_new_risk"] is True
    assert integrated.evidence["sentinel_freeze_new_risk"] is False
    assert integrated.evidence["freeze_new_risk"] is False


def test_incremental_causally_confirmed_evidence_only_sets_freeze() -> None:
    base = _base(active_families=("market_velocity",))

    integrated = _integrate_causal(
        base=base,
        cfg=FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True),
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


def test_limited_gross_cap_mode_is_explicitly_rejected() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "LIMITED_GROSS_CAP was rejected by the economic gate; "
            "use FREEZE_ONLY or SHADOW"
        ),
    ):
        DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP")  # type: ignore[arg-type]


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
    cfg = FREEZE_CFG.override(risk_sentinel_causal_confirmation_enabled=True)
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
        cfg=cfg,
        sentinel_assessment=sentinel,
        sentinel_causal_timeline=_causal_timeline(),
    )

    assert observed["cfg"] is cfg
    assert "sentinel_assessment" not in observed
    assert integrated.freeze_new_risk is True
    assert integrated.target_gross_cap == base.target_gross_cap
