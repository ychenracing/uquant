"""Hostile and legal runtime boundaries for observed reachability evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest
from test_absolute_generalization_reachability import (
    _cash_account,
    _filled_chain,
    _historical_order,
    _observed_trace,
    _probe_chain,
    _reachability_state,
    _replace_account_payload,
)

from uquant.validation.absolute_generalization import (
    analyze_terminal_scc,
    is_positive_strategic_outlet,
)


def test_broker_fill_does_not_require_a_local_acknowledgement() -> None:
    target, grant, epoch, order, fill = _filled_chain()
    grant.acknowledged_order_ids = []

    assert is_positive_strategic_outlet(
        target=target,
        grant=grant,
        epoch=epoch,
        orders=(order,),
        fills=(fill,),
    )


def test_completed_grant_preserves_legal_same_epoch_order_history() -> None:
    target, grant, epoch, order, fill = _filled_chain()
    grant.status = "COMPLETED"
    historical = deepcopy(order)
    historical.order_id = "O000000002"
    historical.status = "CANCELLED"
    historical.requested_shares = 0
    historical.filled_shares = 0
    historical.remaining_shares = 0
    historical.last_event = "CANCELLED"
    historical.cancel_reason = "completed quantity retained as history"
    grant.submitted_order_ids.append(historical.order_id)
    grant.acknowledged_order_ids.append(historical.order_id)
    account = _cash_account(budget_level=1)
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.active_strategic_epoch_id = epoch.epoch_id
    account.order_ledger = [order, historical]
    account.next_order_sequence = 3
    account.fills = [fill]
    trace = _observed_trace(2)
    trace[-1]["state"] = _reachability_state(account=account)
    state = trace[-1]["state"]
    assert isinstance(state, dict)
    state["outlet_evidence"] = {
        "target": target,
        "grant": grant,
        "epoch": epoch,
        "orders": (order, historical),
        "fills": (fill,),
    }

    assert analyze_terminal_scc(trace).passed is True


def test_real_limit_blocker_without_fill_is_a_legal_observation() -> None:
    account = _cash_account(budget_level=1)
    blocked = _historical_order()
    blocked.status = "OPEN"
    blocked.requested_shares = 100
    blocked.remaining_shares = 100
    blocked.last_event = "LIMIT_BLOCKED"
    blocked.cancel_reason = ""
    account.order_ledger = [blocked]
    account.next_order_sequence = 2
    trace = _observed_trace(2)
    trace[-1]["state"] = _reachability_state(account=account)

    result = analyze_terminal_scc(trace)

    assert result.passed is True
    assert result.bounded is True


def test_transition_digest_is_deterministic_under_mapping_key_permutation() -> None:
    trace = _observed_trace(4)
    permuted = deepcopy(trace)
    for index, row in enumerate(permuted):
        state = row["state"]
        assert isinstance(state, dict)
        permuted[index] = dict(reversed(tuple(row.items())))
        permuted[index]["state"] = dict(reversed(tuple(state.items())))

    first = analyze_terminal_scc(trace)
    second = analyze_terminal_scc(permuted)

    assert first.state_transition_digest == second.state_transition_digest
    assert first == analyze_terminal_scc(trace)


def test_transition_count_is_finitely_bounded_before_runtime_decoding() -> None:
    one = _observed_trace(1)[0]
    with pytest.raises(ValueError, match="transition count is unbounded"):
        analyze_terminal_scc([one] * 20_001)


def test_nonfinite_snapshot_and_bool_as_int_fail_closed() -> None:
    nonfinite = _observed_trace(2)
    state = nonfinite[0]["state"]
    assert isinstance(state, dict)
    snapshots = state["snapshots"]
    assert isinstance(snapshots, dict)
    values = next(iter(snapshots.values()))
    assert isinstance(values, dict)
    values["leader_score"] = float("nan")
    with pytest.raises(ValueError, match="snapshot evidence"):
        analyze_terminal_scc(nonfinite)

    boolean_level = _observed_trace(2)
    state = boolean_level[0]["state"]
    assert isinstance(state, dict)
    state["capital_budget_level"] = True
    with pytest.raises(ValueError, match="capital budget level"):
        analyze_terminal_scc(boolean_level)


def test_unknown_state_and_ack_outside_submitted_set_fail_closed() -> None:
    unknown = _observed_trace(2)
    state = unknown[0]["state"]
    assert isinstance(state, dict)
    state["risk_state"] = "UNKNOWN"
    with pytest.raises(ValueError, match="UNKNOWN"):
        analyze_terminal_scc(unknown)

    acknowledgement = _observed_trace(2)
    _target, grant, _epoch = _probe_chain()
    grant.status = "EXPIRED"
    grant.expiry_reason = "unfilled_probe_timeout"
    grant.epoch_id = ""
    grant.submitted_order_ids = []
    grant.acknowledged_order_ids = ["O000000001"]

    def mutate(raw: dict[str, object]) -> None:
        raw["strategic_grant"] = asdict(grant)

    _replace_account_payload(acknowledgement, index=0, mutate=mutate)
    with pytest.raises(ValueError, match="account payload is invalid"):
        analyze_terminal_scc(acknowledgement)
