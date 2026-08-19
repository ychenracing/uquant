from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from uquant import risk as risk_module
from uquant.config import DEFAULT_CONFIG
from uquant.risk_sentinel.integration import integrate_freeze_only, sentinel_cap_for_level
from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)
from uquant.risk_sentinel.service import apply_causal_hysteresis
from uquant.types import Risk, RiskAssessment

LIMITED_CFG = DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP")


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


def _sentinel(date: str, level: SentinelLevel) -> SentinelAssessment:
    return SentinelAssessment(
        date=date,
        level=level,
        confidence=0.90,
        suggested_gross_cap=0.25,
        freeze_new_risk=True,
        evidence_families=("breadth_structure", "market_velocity"),
        reasons=("risk",),
        first_evidence_date=date,
        coverage=_coverage(),
        metrics={
            "broad_fast_return": -0.04,
            "tech_fast_return": -0.05,
            "synchronized_subindustry_damage": 0.60,
        },
    )


def _base(cap: float = 1.0) -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=cap,
        votes=0,
        evidence={"family_votes": {}},
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=False,
        reduction_level=0,
        severity="NORMAL",
    )


def _effective(level: SentinelLevel):
    history = (
        _sentinel("2026-08-18", level),
        _sentinel("2026-08-19", level),
    )
    return apply_causal_hysteresis(
        history,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
        severe_direct=level is SentinelLevel.CRITICAL,
    )


def test_formal_cap_mapping_is_locked_and_ignores_observation_suggestion() -> None:
    assert sentinel_cap_for_level(SentinelLevel.NORMAL) is None
    assert sentinel_cap_for_level(SentinelLevel.CAUTION) is None
    assert sentinel_cap_for_level(SentinelLevel.NOT_READY) is None
    assert sentinel_cap_for_level(SentinelLevel.DEFENSIVE) == 0.70
    assert sentinel_cap_for_level(SentinelLevel.CRITICAL) == 0.50


def test_formal_mapping_uses_only_validated_locked_config() -> None:
    assert sentinel_cap_for_level(SentinelLevel.DEFENSIVE, LIMITED_CFG) == 0.70
    assert sentinel_cap_for_level(SentinelLevel.CRITICAL, LIMITED_CFG) == 0.50


def test_limited_cap_is_minimum_of_base_and_sentinel() -> None:
    sentinel = _sentinel("2026-08-19", SentinelLevel.DEFENSIVE)

    bound = integrate_freeze_only(
        base=_base(1.0),
        sentinel=sentinel,
        cfg=LIMITED_CFG,
        hysteresis=_effective(SentinelLevel.DEFENSIVE),
    )
    already_stricter = integrate_freeze_only(
        base=_base(0.40),
        sentinel=sentinel,
        cfg=LIMITED_CFG,
        hysteresis=_effective(SentinelLevel.DEFENSIVE),
    )

    assert bound.target_gross_cap == 0.70
    assert bound.evidence["sentinel_cap_binding"] is True
    assert already_stricter.target_gross_cap == 0.40
    assert already_stricter.evidence["sentinel_cap_binding"] is False


def test_limited_cap_preserves_base_state_and_reduction_owners() -> None:
    base = replace(
        _base(1.0),
        state=Risk.CAUTION,
        shock_state="WATCH",
        reduction_level=1,
        severity="CAUTION",
    )

    integrated = integrate_freeze_only(
        base=base,
        sentinel=_sentinel("2026-08-19", SentinelLevel.CRITICAL),
        cfg=LIMITED_CFG,
        hysteresis=_effective(SentinelLevel.CRITICAL),
    )

    assert integrated.target_gross_cap == 0.50
    assert integrated.state is base.state
    assert integrated.shock_state == base.shock_state
    assert integrated.reduction_level == base.reduction_level
    assert integrated.severity == base.severity
    assert integrated.evidence["sentinel_effective_level"] == "CRITICAL"
    assert integrated.evidence["sentinel_cap"] == 0.50


def test_shadow_and_freeze_only_modes_never_apply_a_cap() -> None:
    sentinel = _sentinel("2026-08-19", SentinelLevel.CRITICAL)
    effective = _effective(SentinelLevel.CRITICAL)

    for mode in ("SHADOW", "FREEZE_ONLY"):
        base = _base(0.90)
        integrated = integrate_freeze_only(
            base=base,
            sentinel=sentinel,
            cfg=DEFAULT_CONFIG.override(risk_sentinel_mode=mode),
            hysteresis=effective,
        )
        assert integrated.target_gross_cap == 0.90


def test_not_ready_never_reports_a_safe_full_cap() -> None:
    sentinel = replace(
        _sentinel("2026-08-19", SentinelLevel.CRITICAL),
        level=SentinelLevel.NOT_READY,
        suggested_gross_cap=None,
        coverage=replace(_coverage(), status=WarmupStatus.NOT_READY),
    )
    result = integrate_freeze_only(
        base=_base(0.85),
        sentinel=sentinel,
        cfg=LIMITED_CFG,
        hysteresis=_effective(SentinelLevel.NORMAL),
    )

    assert result.target_gross_cap == 0.85
    assert result.evidence["sentinel_cap"] is None
    assert result.evidence["sentinel_cap_binding"] is False


def test_assess_risk_is_the_only_production_cap_mapping_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base(1.0)
    sentinel = _sentinel("2026-08-19", SentinelLevel.DEFENSIVE)
    history = (
        _sentinel("2026-08-18", SentinelLevel.DEFENSIVE),
        sentinel,
    )

    monkeypatch.setattr(risk_module, "_assess_base_risk", lambda **_: base)
    result = risk_module.assess_risk(
        date=pd.Timestamp("2026-08-19"),
        broad=None,  # type: ignore[arg-type]
        tech=None,  # type: ignore[arg-type]
        reference_panel={},
        reference_returns=None,
        user_panel={},
        leaders={},
        account=None,  # type: ignore[arg-type]
        equity=1.0,
        cfg=LIMITED_CFG,
        sentinel_assessment=sentinel,
        sentinel_history=history,
    )

    assert result.target_gross_cap == 0.70
    assert result.evidence["sentinel_cap_binding"] is True
