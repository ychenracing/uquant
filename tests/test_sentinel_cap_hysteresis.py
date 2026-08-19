from __future__ import annotations

from dataclasses import replace

import pandas as pd

from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)
from uquant.risk_sentinel.service import (
    apply_causal_hysteresis,
    evaluate_recent_sentinel_levels,
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


def _assessment(date: str, level: SentinelLevel) -> SentinelAssessment:
    active = level in {SentinelLevel.DEFENSIVE, SentinelLevel.CRITICAL}
    return SentinelAssessment(
        date=date,
        level=level,
        confidence=0.90,
        suggested_gross_cap=0.50 if active else None,
        freeze_new_risk=level is not SentinelLevel.NORMAL,
        evidence_families=("breadth_structure", "market_velocity") if active else (),
        reasons=("risk",) if active else ("clear",),
        first_evidence_date=date if active else None,
        coverage=_coverage(),
        metrics={
            "broad_fast_return": -0.04 if active else 0.0,
            "tech_fast_return": -0.05 if active else 0.0,
            "synchronized_subindustry_damage": 0.60 if active else 0.0,
        },
    )


def test_two_defensive_sessions_confirm_without_persisted_state() -> None:
    history = (
        _assessment("2026-08-17", SentinelLevel.NORMAL),
        _assessment("2026-08-18", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-19", SentinelLevel.DEFENSIVE),
    )

    first = apply_causal_hysteresis(
        history,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )
    restarted = apply_causal_hysteresis(
        tuple(reversed(history)),
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )

    assert first == restarted
    assert first.effective_level is SentinelLevel.DEFENSIVE
    assert first.confirmation_days == 2
    assert first.first_evidence_date == "2026-08-18"


def test_one_defensive_session_does_not_confirm() -> None:
    result = apply_causal_hysteresis(
        (_assessment("2026-08-19", SentinelLevel.DEFENSIVE),),
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )

    assert result.effective_level is SentinelLevel.NORMAL
    assert result.confirmation_days == 1


def test_low_confidence_or_degraded_risk_cannot_confirm_or_repair() -> None:
    low_confidence = replace(
        _assessment("2026-08-18", SentinelLevel.DEFENSIVE),
        confidence=0.79,
    )
    degraded = replace(
        _assessment("2026-08-19", SentinelLevel.DEFENSIVE),
        coverage=replace(_coverage(), status=WarmupStatus.DEGRADED),
    )

    result = apply_causal_hysteresis(
        (low_confidence, degraded),
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
        min_confidence=0.80,
    )

    assert result.effective_level is SentinelLevel.NORMAL
    assert result.confirmation_days == 0


def test_critical_direct_trigger_is_explicit_and_single_session() -> None:
    history = (_assessment("2026-08-19", SentinelLevel.CRITICAL),)

    ordinary = apply_causal_hysteresis(
        history,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
        severe_direct=False,
    )
    direct = apply_causal_hysteresis(
        history,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
        severe_direct=True,
    )

    assert ordinary.effective_level is SentinelLevel.NORMAL
    assert direct.effective_level is SentinelLevel.CRITICAL


def test_severe_direct_activation_requires_three_low_sessions_to_release() -> None:
    direct = _assessment("2026-08-17", SentinelLevel.CRITICAL)
    low = (
        _assessment("2026-08-18", SentinelLevel.NORMAL),
        _assessment("2026-08-19", SentinelLevel.CAUTION),
        _assessment("2026-08-20", SentinelLevel.NORMAL),
    )

    one_day = apply_causal_hysteresis(
        (direct, low[0]),
        as_of="2026-08-18",
        confirm_days=2,
        repair_days=3,
        severe_direct=True,
    )
    two_days = apply_causal_hysteresis(
        (direct, *low[:2]),
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
        severe_direct=True,
    )
    repaired = apply_causal_hysteresis(
        (direct, *low),
        as_of="2026-08-20",
        confirm_days=2,
        repair_days=3,
        severe_direct=True,
    )

    assert one_day.effective_level is SentinelLevel.CRITICAL
    assert one_day.repair_days == 1
    assert two_days.effective_level is SentinelLevel.CRITICAL
    assert two_days.repair_days == 2
    assert repaired.effective_level is SentinelLevel.NORMAL
    assert repaired.repair_confirmed is True


def test_active_cap_requires_three_low_risk_sessions_to_release() -> None:
    active = (
        _assessment("2026-08-14", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-15", SentinelLevel.DEFENSIVE),
    )
    low = (
        _assessment("2026-08-18", SentinelLevel.CAUTION),
        _assessment("2026-08-19", SentinelLevel.NORMAL),
        _assessment("2026-08-20", SentinelLevel.NORMAL),
    )

    one_day = apply_causal_hysteresis(
        active + low[:1],
        as_of="2026-08-18",
        confirm_days=2,
        repair_days=3,
    )
    two_days = apply_causal_hysteresis(
        active + low[:2],
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )
    repaired = apply_causal_hysteresis(
        active + low,
        as_of="2026-08-20",
        confirm_days=2,
        repair_days=3,
    )

    assert one_day.effective_level is SentinelLevel.DEFENSIVE
    assert two_days.effective_level is SentinelLevel.DEFENSIVE
    assert repaired.effective_level is SentinelLevel.NORMAL
    assert repaired.repair_confirmed is True
    assert repaired.repair_days == 3


def test_new_confirmed_episode_clears_prior_repair_attribution() -> None:
    history = (
        _assessment("2026-08-11", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-12", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-13", SentinelLevel.NORMAL),
        _assessment("2026-08-14", SentinelLevel.CAUTION),
        _assessment("2026-08-15", SentinelLevel.NORMAL),
        _assessment("2026-08-18", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-19", SentinelLevel.DEFENSIVE),
    )

    result = apply_causal_hysteresis(
        history,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )

    assert result.effective_level is SentinelLevel.DEFENSIVE
    assert result.repair_days == 0
    assert result.repair_confirmed is False


def test_future_assessments_do_not_change_as_of_result() -> None:
    current = (
        _assessment("2026-08-18", SentinelLevel.DEFENSIVE),
        _assessment("2026-08-19", SentinelLevel.DEFENSIVE),
    )
    future = replace(current[-1], date="2026-08-20", level=SentinelLevel.NORMAL)

    without_future = apply_causal_hysteresis(
        current,
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )
    with_future = apply_causal_hysteresis(
        (*current, future),
        as_of="2026-08-19",
        confirm_days=2,
        repair_days=3,
    )

    assert with_future == without_future


def test_recent_level_evaluation_is_market_only_causal_and_restartable() -> None:
    index = pd.bdate_range(end="2026-08-20", periods=30)
    close = pd.Series(range(100, 130), index=index, dtype=float)
    frame = pd.DataFrame({"close": close})
    future_changed = frame.copy()
    future_changed.loc[pd.Timestamp("2026-08-20"), "close"] = 1.0
    sessions = tuple(str(item.date()) for item in index[-3:-1])
    panel = {symbol: frame for symbol in ("a", "b", "c", "d")}

    first = evaluate_recent_sentinel_levels(
        sessions=sessions,
        broad_frame=frame,
        tech_frame=frame,
        reference_panel=panel,
        point_in_time_industries=lambda _: {
            "a": "optical",
            "b": "optical",
            "c": "storage",
            "d": "storage",
        },
    )
    restarted = evaluate_recent_sentinel_levels(
        sessions=tuple(reversed(sessions)),
        broad_frame=future_changed,
        tech_frame=future_changed,
        reference_panel={symbol: future_changed for symbol in panel},
        point_in_time_industries=lambda _: {
            "d": "storage",
            "c": "storage",
            "b": "optical",
            "a": "optical",
        },
    )

    assert tuple(item.date for item in first) == tuple(sorted(sessions))
    assert tuple(item.to_dict() for item in restarted) == tuple(
        item.to_dict() for item in first
    )
    assert all(item.metrics["capital_drawdown"] == 0.0 for item in first)
    assert all("live_book_damage" not in item.evidence_families for item in first)
