"""Focused failed-grant recovery acceptance over observed production facts."""

from __future__ import annotations

from dataclasses import replace

import pytest
from _absolute_generalization_reachability_fixture import (
    failed_recovery_trace,
    failed_successor_chain,
)
from test_absolute_generalization_reachability import (
    _filled_chain,
    _rearm_evidence,
    _replace_account_payload,
)

from uquant.validation.absolute_generalization import analyze_failed_grant_recovery


@pytest.mark.parametrize(("retry_sessions", "passed"), ((20, True), (21, False)))
def test_failed_grant_successor_uses_exact_twenty_session_boundary(
    retry_sessions: int,
    passed: bool,
) -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=retry_sessions)
    )
    chain = (target, second, second_epoch, order, fill)
    result = analyze_failed_grant_recovery(
        first_grant=first,
        first_epoch=first_epoch,
        transitions=failed_recovery_trace(retry_sessions, chain),
    )

    assert result.observed is True
    assert result.healthy_retry_sessions == retry_sessions
    assert result.passed is passed
    assert result.first_candidate == "sz300308"
    assert result.second_candidate == "sz300502"
    assert result.previous_grant_reconciled is True
    assert result.authorization_rotated is True
    assert result.outlet_reconciled is True


def test_failed_grant_successor_rejects_a_broken_previous_identity() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain()
    )
    second.previous_grant_id = "grant_" + "f" * 64

    with pytest.raises(ValueError, match="previous grant"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


def test_failed_grant_successor_rejects_broken_previous_epoch_identity() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    second_epoch.previous_epoch_id = "epoch_" + "f" * 64
    with pytest.raises(ValueError, match="previous epoch"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


@pytest.mark.parametrize("authorization", ("", "same"))
def test_failed_grant_successor_requires_a_new_authorization(
    authorization: str,
) -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    second.authorization_id = "" if authorization == "" else first.authorization_id
    with pytest.raises(ValueError, match="authorization"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


def test_failed_grant_recovery_requires_a_distinct_candidate() -> None:
    first, first_epoch, *_rest = failed_successor_chain(retry_sessions=1)
    target, second, second_epoch, order, fill = _filled_chain(
        candidate=first.candidate_symbol,
        previous_grant_id=first.grant_id,
        previous_epoch_id=first_epoch.epoch_id,
        authorization_id=str(
            _rearm_evidence("sz300308", "2026-01-09")["authorization_id"]
        ),
        created_session="2026-01-09",
        fill_session="2026-01-10",
    )

    with pytest.raises(ValueError, match="successor identity"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


def test_failed_grant_recovery_rejects_nonrecoverable_terminal_reason() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    first.expiry_reason = "contract_corruption"
    first_epoch.close_reason = "contract_corruption"

    with pytest.raises(ValueError, match="recoverable"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


def test_failed_grant_recovery_rejects_a_previously_activated_epoch() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    first.filled_shares = 1
    first_epoch.first_fill_session = "2026-01-06"
    first_epoch.active_session = "2026-01-06"

    with pytest.raises(ValueError, match="terminally unfilled"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )


def test_failed_grant_recovery_rejects_a_contradictory_observed_predecessor() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    trace = failed_recovery_trace(
        1, (target, second, second_epoch, order, fill)
    )

    def record_realized_predecessor(raw: dict[str, object]) -> None:
        epochs = raw["strategic_epochs"]
        assert isinstance(epochs, list)
        predecessor = epochs[0]
        assert isinstance(predecessor, dict)
        assert predecessor["epoch_id"] == first_epoch.epoch_id
        predecessor.update(
            {
                "realized_status": "CLOSED",
                "first_fill_session": "2026-01-06",
                "active_session": "2026-01-06",
                "closed_session": "2026-01-07",
                "close_reason": "realized strategic lifecycle",
            }
        )

    for index in (-3, -2, -1):
        _replace_account_payload(
            trace,
            index=index,
            mutate=record_realized_predecessor,
        )

    with pytest.raises(ValueError, match="observed predecessor"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=trace,
        )


def test_failed_grant_recovery_rejects_a_terminal_epoch_reversal() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    trace = failed_recovery_trace(
        1, (target, second, second_epoch, order, fill)
    )

    def record_realized_predecessor(raw: dict[str, object]) -> None:
        epochs = raw["strategic_epochs"]
        assert isinstance(epochs, list)
        predecessor = epochs[0]
        assert isinstance(predecessor, dict)
        assert predecessor["epoch_id"] == first_epoch.epoch_id
        predecessor.update(
            {
                "realized_status": "CLOSED",
                "first_fill_session": "2026-01-06",
                "active_session": "2026-01-06",
                "closed_session": "2026-01-07",
                "close_reason": "realized strategic lifecycle",
            }
        )

    _replace_account_payload(
        trace,
        index=-3,
        mutate=record_realized_predecessor,
    )

    with pytest.raises(ValueError, match="observed predecessor"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=trace,
        )


def test_failed_grant_recovery_rejects_an_ordinary_target() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=1)
    )
    target = replace(target, origin_subsystem="LEADER")

    with pytest.raises(ValueError, match="successor outlet"):
        analyze_failed_grant_recovery(
            first_grant=first,
            first_epoch=first_epoch,
            transitions=failed_recovery_trace(
                1, (target, second, second_epoch, order, fill)
            ),
        )
