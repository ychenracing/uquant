"""Pure service boundary for Independent Risk Sentinel evaluation."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .coverage import assess_coverage
from .evidence import build_market_evidence
from .models import SentinelAssessment, SentinelLevel, WarmupStatus
from .opinion import build_risk_opinion


@dataclass(frozen=True, slots=True)
class SentinelHysteresis:
    """State-free Sentinel confirmation reconstructed from causal observations."""

    effective_level: SentinelLevel
    observed_level: SentinelLevel
    confirmation_days: int
    repair_days: int
    repair_confirmed: bool
    first_evidence_date: str | None


def _positive_days(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def severe_direct_observation(assessment: SentinelAssessment) -> bool:
    """Return whether one trusted CRITICAL row meets the narrow direct trigger."""

    families = set(assessment.evidence_families)
    broad = assessment.metrics.get("broad_fast_return", 0.0)
    tech = assessment.metrics.get("tech_fast_return", 0.0)
    synchronized = assessment.metrics.get("synchronized_subindustry_damage", 0.0)
    severe_velocity = (broad <= -0.025 and tech <= -0.025) or min(broad, tech) <= -0.05
    return bool(
        assessment.level is SentinelLevel.CRITICAL
        and {"market_velocity", "breadth_structure"}.issubset(families)
        and severe_velocity
        and synchronized >= 0.40
    )


def apply_causal_hysteresis(
    assessments: tuple[SentinelAssessment, ...],
    *,
    as_of: str,
    confirm_days: int,
    repair_days: int,
    severe_direct: bool = False,
    min_confidence: float = 0.0,
) -> SentinelHysteresis:
    """Rebuild confirmation and repair using only observations visible at ``as_of``.

    The function owns no durable or process-local state. Input order is ignored,
    duplicate session observations fail closed, and future observations are
    excluded before the sequence is evaluated.
    """

    confirmation_required = _positive_days(confirm_days, label="confirm_days")
    repair_required = _positive_days(repair_days, label="repair_days")
    try:
        boundary = date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sentinel hysteresis as_of must be an ISO date") from exc
    if not isinstance(severe_direct, bool):
        raise ValueError("Sentinel severe_direct must be boolean")
    if (
        isinstance(min_confidence, bool)
        or not isinstance(min_confidence, (int, float))
        or not math.isfinite(float(min_confidence))
        or not 0.0 <= float(min_confidence) <= 1.0
    ):
        raise ValueError("Sentinel min_confidence must be in [0, 1]")
    visible = tuple(
        sorted(
            (item for item in assessments if date.fromisoformat(item.date) <= boundary),
            key=lambda item: item.date,
        )
    )
    dates = tuple(item.date for item in visible)
    if len(dates) != len(set(dates)):
        raise ValueError("Sentinel hysteresis observations must have unique dates")

    effective = SentinelLevel.NORMAL
    observed = SentinelLevel.NOT_READY
    active_streak = 0
    critical_streak = 0
    active_start_date: str | None = None
    critical_start_date: str | None = None
    repair_streak = 0
    repair_confirmed = False
    first_evidence_date: str | None = None
    for item in visible:
        observed = item.level
        trusted = bool(
            item.coverage.status is WarmupStatus.READY
            and item.confidence >= float(min_confidence)
        )
        if item.level is SentinelLevel.NOT_READY or not trusted:
            active_streak = 0
            critical_streak = 0
            active_start_date = None
            critical_start_date = None
            repair_streak = 0
            continue
        if item.level in {SentinelLevel.DEFENSIVE, SentinelLevel.CRITICAL}:
            repair_streak = 0
            if active_streak == 0:
                active_start_date = item.first_evidence_date or item.date
            active_streak += 1
            if item.level is SentinelLevel.CRITICAL:
                if critical_streak == 0:
                    critical_start_date = item.first_evidence_date or item.date
                critical_streak += 1
            else:
                critical_streak = 0
                critical_start_date = None
            direct_observation = bool(
                severe_direct and severe_direct_observation(item)
            )
            if direct_observation or critical_streak >= confirmation_required:
                if effective is SentinelLevel.NORMAL:
                    repair_confirmed = False
                if effective is not SentinelLevel.CRITICAL:
                    first_evidence_date = critical_start_date
                effective = SentinelLevel.CRITICAL
            elif effective is SentinelLevel.NORMAL and active_streak >= confirmation_required:
                effective = SentinelLevel.DEFENSIVE
                first_evidence_date = active_start_date
                repair_confirmed = False
            continue

        active_streak = 0
        critical_streak = 0
        active_start_date = None
        critical_start_date = None
        if effective in {SentinelLevel.DEFENSIVE, SentinelLevel.CRITICAL}:
            repair_streak += 1
            if repair_streak >= repair_required:
                effective = SentinelLevel.NORMAL
                first_evidence_date = None
                repair_confirmed = True
        else:
            repair_streak = 0

    return SentinelHysteresis(
        effective_level=effective,
        observed_level=observed,
        confirmation_days=max(active_streak, critical_streak),
        repair_days=repair_streak,
        repair_confirmed=repair_confirmed,
        first_evidence_date=first_evidence_date,
    )


def evaluate_sentinel(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    leader_symbols: tuple[str, ...] = (),
    capital_drawdown: float | None = None,
) -> SentinelAssessment:
    """Evaluate causal inputs without modifying them or any durable state."""

    expected_symbols = tuple(sorted(point_in_time_industries))
    coverage = assess_coverage(
        as_of=as_of,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        expected_symbols=expected_symbols,
        reference_panel=reference_panel,
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
    )
    evidence = build_market_evidence(
        as_of=as_of,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        reference_panel=reference_panel,
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
        leader_symbols=leader_symbols,
        capital_drawdown=capital_drawdown,
    )
    return build_risk_opinion(evidence=evidence, coverage=coverage)


def evaluate_recent_sentinel_levels(
    *,
    sessions: tuple[str, ...],
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Callable[[str], Mapping[str, str]],
) -> tuple[SentinelAssessment, ...]:
    """Recompute market-only level history from each session's visible prefix.

    Holdings, leader ownership and account drawdown are deliberately absent:
    their historical values are not durable Sentinel inputs. This prevents
    today's account state from being backfilled into earlier confirmations.
    """

    ordered = tuple(sorted(sessions))
    if len(ordered) != len(set(ordered)):
        raise ValueError("Sentinel history sessions must be unique")
    assessments: list[SentinelAssessment] = []
    for session in ordered:
        try:
            date.fromisoformat(session)
        except (TypeError, ValueError) as exc:
            raise ValueError("Sentinel history session must be an ISO date") from exc
        industries = dict(point_in_time_industries(session))
        panel = {
            symbol: reference_panel[symbol]
            for symbol in sorted(industries)
            if symbol in reference_panel
        }
        assessments.append(
            evaluate_sentinel(
                as_of=session,
                broad_frame=broad_frame,
                tech_frame=tech_frame,
                reference_panel=panel,
                point_in_time_industries=industries,
                held_symbols=(),
                leader_symbols=(),
                capital_drawdown=None,
            )
        )
    return tuple(assessments)
