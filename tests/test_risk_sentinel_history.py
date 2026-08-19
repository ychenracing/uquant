from __future__ import annotations

import pytest

from uquant.risk_sentinel.history import fold_sentinel_market_state
from uquant.risk_sentinel.models import SentinelLevel, SentinelMarketRow, WarmupStatus


def _row(
    date: str,
    *,
    level: SentinelLevel = SentinelLevel.NORMAL,
    status: WarmupStatus = WarmupStatus.READY,
    severe_direct: bool = False,
) -> SentinelMarketRow:
    active = level in {SentinelLevel.DEFENSIVE, SentinelLevel.CRITICAL}
    return SentinelMarketRow(
        date=date,
        coverage_status=status,
        confidence=1.0,
        level=level,
        freeze_candidate=active,
        family_active=(("market_velocity", active),),
        reasons=("market velocity",) if active else ("normal",),
        weakest_subindustries=(),
        severe_direct=severe_direct,
    )


def test_fold_requires_two_confirmations_and_three_repairs() -> None:
    seed = (
        _row("2026-01-05"),
        _row("2026-01-06"),
        _row("2026-01-07"),
    )
    first = _row("2026-01-08", level=SentinelLevel.DEFENSIVE)
    second = _row("2026-01-09", level=SentinelLevel.DEFENSIVE)
    repair = (
        _row("2026-01-12"),
        _row("2026-01-13"),
        _row("2026-01-14"),
    )

    unconfirmed = fold_sentinel_market_state((*seed, first), confirm_days=2, repair_days=3)
    confirmed = fold_sentinel_market_state((*seed, first, second), confirm_days=2, repair_days=3)
    repairing = fold_sentinel_market_state(
        (*seed, first, second, *repair[:2]),
        confirm_days=2,
        repair_days=3,
    )
    repaired = fold_sentinel_market_state(
        (*seed, first, second, *repair),
        confirm_days=2,
        repair_days=3,
    )

    assert unconfirmed.confirmation_history_trusted is True
    assert unconfirmed.effective_level is SentinelLevel.NORMAL
    assert unconfirmed.confirmation_days == 1
    assert confirmed.effective_level is SentinelLevel.DEFENSIVE
    assert confirmed.confirmed_since == "2026-01-08"
    assert confirmed.confirmation_days == 2
    assert repairing.effective_level is SentinelLevel.DEFENSIVE
    assert repairing.repair_days == 2
    assert repaired.effective_level is SentinelLevel.NORMAL
    assert repaired.repair_days == 3


def test_not_ready_or_degraded_breaks_trust_and_cannot_bridge_confirmation() -> None:
    rows = (
        _row("2026-01-05"),
        _row("2026-01-06"),
        _row("2026-01-07"),
        _row("2026-01-08", level=SentinelLevel.DEFENSIVE),
        _row("2026-01-09", status=WarmupStatus.NOT_READY),
        _row("2026-01-12", level=SentinelLevel.DEFENSIVE),
        _row("2026-01-13", level=SentinelLevel.DEFENSIVE),
    )

    state = fold_sentinel_market_state(rows, confirm_days=2, repair_days=3)

    assert state.confirmation_history_trusted is False
    assert state.effective_level is SentinelLevel.NORMAL
    assert state.confirmation_days == 0
    assert "NOT_READY" in state.trust_reasons

    degraded = fold_sentinel_market_state(
        (*rows[:4], _row("2026-01-09", status=WarmupStatus.DEGRADED)),
        confirm_days=2,
        repair_days=3,
    )
    assert degraded.confirmation_history_trusted is False
    assert "DEGRADED" in degraded.trust_reasons


def test_severe_direct_can_confirm_immediately_only_after_history_is_trusted() -> None:
    seed = (
        _row("2026-01-05"),
        _row("2026-01-06"),
        _row("2026-01-07"),
    )
    severe = _row(
        "2026-01-08",
        level=SentinelLevel.CRITICAL,
        severe_direct=True,
    )

    trusted = fold_sentinel_market_state((*seed, severe), confirm_days=2, repair_days=3)
    untrusted = fold_sentinel_market_state((severe,), confirm_days=2, repair_days=3)

    assert trusted.effective_level is SentinelLevel.CRITICAL
    assert trusted.confirmed_since == "2026-01-08"
    assert untrusted.effective_level is SentinelLevel.NORMAL
    assert untrusted.confirmation_history_trusted is False


@pytest.mark.parametrize(
    ("first_level", "second_level"),
    (
        (SentinelLevel.DEFENSIVE, SentinelLevel.CRITICAL),
        (SentinelLevel.CRITICAL, SentinelLevel.DEFENSIVE),
    ),
)
def test_confirmation_streak_survives_freeze_candidate_level_changes(
    first_level: SentinelLevel,
    second_level: SentinelLevel,
) -> None:
    rows = (
        _row("2026-01-05"),
        _row("2026-01-06"),
        _row("2026-01-07"),
        _row("2026-01-08", level=first_level),
        _row("2026-01-09", level=second_level),
    )

    state = fold_sentinel_market_state(rows, confirm_days=2, repair_days=3)

    assert state.effective_level is second_level
    assert state.confirmed_since == "2026-01-08"
    assert state.confirmation_days == 2
