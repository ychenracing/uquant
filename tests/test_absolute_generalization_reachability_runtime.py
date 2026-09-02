"""Hostile and legal runtime boundaries for observed reachability evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
from _absolute_generalization_reachability_fixture import (
    failed_recovery_trace,
    failed_successor_chain,
)
from test_absolute_generalization_reachability import (
    _cash_account,
    _filled_chain,
    _historical_order,
    _observed_trace,
    _probe_chain,
    _reachability_state,
    _replace_account_payload,
)
from test_strategic_cash_rearm import _qualification

import uquant.validation.absolute_generalization.replay as replay_module
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import strict_json_loads
from uquant.models.strategic_grant import StrategicQualificationObservation
from uquant.validation.absolute_generalization import (
    AbsoluteGeneralizationScenario,
    analyze_terminal_scc,
    is_positive_strategic_outlet,
    load_absolute_generalization_contract,
    project_observed_reachability_state,
)
from uquant.validation.absolute_generalization import (
    _recovery_runtime_fixtures as fixture_module,
)
from uquant.validation.absolute_generalization import recovery_runtime as runtime_module
from uquant.validation.absolute_generalization._acceptance_evidence import (
    validate_failed_grant_evidence,
    validate_terminal_evidence,
)
from uquant.validation.absolute_generalization._physical_identity import (
    physical_fill_identity,
    physical_fill_identity_map,
    physical_fill_identity_sha256,
)
from uquant.validation.absolute_generalization._reachability_codec import (
    decision_runtime_inputs_from_raw,
    reachability_state_from_raw,
    reachability_state_to_raw,
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


def test_reachability_runtime_state_strict_json_round_trip_preserves_task6() -> None:
    trace = _observed_trace(4, outlet_at_end=True)
    raw = [
        {
            "session": row["session"],
            "phase": row["phase"],
            "edge_kind": row["edge_kind"],
            "state": reachability_state_to_raw(row["state"]),
        }
        for row in trace
    ]
    rebuilt = [
        {
            "session": row["session"],
            "phase": row["phase"],
            "edge_kind": row["edge_kind"],
            "state": reachability_state_from_raw(row["state"]),
        }
        for row in raw
    ]

    assert analyze_terminal_scc(rebuilt) == analyze_terminal_scc(trace)
    leader = next(iter(raw[0]["state"]["leaders"].values()))
    assert "confidence" in leader
    assert "components" in leader

    missing = deepcopy(raw[0]["state"])
    next(iter(missing["leaders"].values())).pop("components")
    with pytest.raises(ValueError, match="leader"):
        reachability_state_from_raw(missing)

    boolean = deepcopy(raw[0]["state"])
    next(iter(boolean["snapshots"].values()))["ret20"] = True
    with pytest.raises(ValueError, match="snapshot"):
        reachability_state_from_raw(boolean)

    nonfinite = deepcopy(raw[0]["state"])
    next(iter(nonfinite["leaders"].values()))["components"]["ret20"] = float(
        "nan"
    )
    with pytest.raises(ValueError, match="leader"):
        reachability_state_from_raw(nonfinite)


def test_decision_runtime_codec_omits_explicit_unavailable_diagnostics() -> None:
    state = _reachability_state()
    leader = asdict(next(iter(state["leaders"].values())))
    leader["components"].update(
        {
            "missing": float("nan"),
            "negative_unavailable": float("-inf"),
            "positive_unavailable": float("inf"),
        }
    )
    snapshots = deepcopy(state["snapshots"])
    next(iter(snapshots.values())).update(
        {
            "missing": float("nan"),
            "negative_unavailable": float("-inf"),
            "positive_unavailable": float("inf"),
        }
    )
    qualification = _qualification()
    payload = replay_module._payload(
        {
            "effective_config_sha256": config_fingerprint(DEFAULT_CONFIG),
            "risk_assessment": state["risk"],
            "strategic_universe_roles": state["universe"],
            "strategic_qualification": qualification,
            "strategic_successor_qualification": qualification,
            "leader_scores": [leader],
            "qualification_snapshots": snapshots,
        },
        project_nonfinite_diagnostics=True,
    )

    decoded = decision_runtime_inputs_from_raw(payload)
    observed = next(iter(decoded["leaders"].values()))
    snapshot = next(iter(decoded["snapshots"].values()))

    assert observed.components["secular_score"] == 0.95
    assert not {
        "missing",
        "negative_unavailable",
        "positive_unavailable",
    }.intersection(observed.components)
    assert snapshot["ret20"] == 0.2
    assert not {
        "missing",
        "negative_unavailable",
        "positive_unavailable",
    }.intersection(snapshot)


def test_recovery_fixture_data_covers_the_production_reference_universe() -> None:
    contract = load_absolute_generalization_contract()

    assert set(fixture_module._fixture_symbols(contract)) == {
        *contract.canonical_universe,
        *fixture_module.INDEX_SYMBOLS,
    }


def test_incremental_replay_account_is_materialized_for_reachability() -> None:
    _target, grant, epoch, order, fill = _filled_chain()
    account = _cash_account(budget_level=1)
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.active_strategic_epoch_id = epoch.epoch_id
    account.order_ledger = [order]
    account.fills = [fill]
    account.next_order_sequence = 2
    snapshot = replay_module._account_snapshot(
        account,
        order_tracker=replay_module._EntityTracker(),
        epoch_tracker=replay_module._EntityTracker(),
        appended_orders=(order,),
        appended_epochs=(epoch,),
    )

    payload = runtime_module._ReplayAccountMaterializer().materialize(
        snapshot,
        new_fills=(replay_module._payload(fill),),
    )
    raw = strict_json_loads(payload.canonical_json)

    assert [item["order_id"] for item in raw["order_ledger"]] == [order.order_id]
    assert [item["epoch_id"] for item in raw["strategic_epochs"]] == [epoch.epoch_id]
    assert raw["fills"] == [strict_json_loads(replay_module._payload(fill).canonical_json)]


def test_inactive_empty_qualification_identity_is_a_legal_runtime_state() -> None:
    trace = _observed_trace(2)
    for index, row in enumerate(trace):
        state = row["state"]
        assert isinstance(state, dict)
        state["qualification_ready"] = False
        state["qualification_route"] = ""
        state["qualification_quorum"] = ""

        def clear(raw: dict[str, object]) -> None:
            raw["strategic_qualification"] = asdict(
                StrategicQualificationObservation()
            )

        _replace_account_payload(trace, index=index, mutate=clear)

    assert analyze_terminal_scc(trace).passed is True


def test_recovery_loads_only_the_preregistered_historical_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract = load_absolute_generalization_contract()
    calls: list[str] = []
    expected = object()

    def replay(scenario: AbsoluteGeneralizationScenario, **_kwargs: object) -> object:
        calls.append(scenario.removed_symbol)
        return expected

    monkeypatch.setattr(runtime_module, "run_absolute_generalization_replay", replay)

    observed = runtime_module._historical_recovery_replay(
        root=tmp_path,
        data_dir=tmp_path,
        cache_dir=tmp_path,
        contract=contract,
    )

    assert observed is expected
    assert calls == ["sz300502"]


def test_failed_recovery_fixture_rotates_authorization_and_candidate() -> None:
    contract = load_absolute_generalization_contract()
    replay = fixture_module.run_failed_grant_fixture(contract)
    transitions = runtime_module.replay_reachability_transitions(replay)

    payload = runtime_module._failed_recovery_payload(transitions, contract)

    first = payload["first_grant"]
    second = payload["second_grant"]
    assert first["authorization_id"]
    assert second["authorization_id"]
    assert first["authorization_id"] != second["authorization_id"]
    assert first["candidate_symbol"] != second["candidate_symbol"]


def test_initial_crowning_preserves_empty_authorization_session() -> None:
    _target, grant, _epoch, _order, _fill = _filled_chain()
    grant.authorization_id = ""

    assert runtime_module._observed_crowning_authorization_session(
        {"strategic_grant": asdict(grant)}, grant
    ) == ""


def test_task6_projects_state_claims_from_runtime_objects() -> None:
    original = _observed_trace(1)[0]["state"]
    projected = project_observed_reachability_state(
        account_payload=original["account_payload"],
        cfg=original["cfg"],
        risk=original["risk"],
        universe=original["universe"],
        snapshots=original["snapshots"],
        leaders=original["leaders"],
        outlet_evidence=original["outlet_evidence"],
    )
    assert projected == original


def test_simulated_fill_uses_lossless_native_physical_identity() -> None:
    _target, _grant, _epoch, _order, fill = _filled_chain()
    fill.fill_id = ""
    identity = physical_fill_identity(fill)

    assert identity == (
        "SIMULATED",
        fill.order_id,
        fill.signal_date,
        fill.fill_date,
        fill.symbol,
        fill.side,
        fill.shares,
        fill.price.hex(),
        fill.gross_value.hex(),
        fill.event_id,
        fill.grant_id,
        fill.epoch_id,
    )
    fill.event_id = ""
    with pytest.raises(ValueError, match="simulated fill identity"):
        physical_fill_identity(fill)


def test_simulated_fill_identity_rejects_numeric_aliases_and_duplicates() -> None:
    _target, _grant, _epoch, _order, fill = _filled_chain()
    fill.fill_id = ""
    duplicate = deepcopy(fill)
    with pytest.raises(ValueError, match="duplicate physical fill identity"):
        physical_fill_identity_map((fill, duplicate))

    boolean_shares = deepcopy(fill)
    boolean_shares.shares = True
    with pytest.raises(ValueError, match="shares"):
        physical_fill_identity(boolean_shares)

    zero_shares = deepcopy(fill)
    zero_shares.shares = 0
    with pytest.raises(ValueError, match="shares"):
        physical_fill_identity(zero_shares)

    boolean_price = deepcopy(fill)
    boolean_price.price = True
    with pytest.raises(ValueError, match="price"):
        physical_fill_identity(boolean_price)


def _raw_transitions(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "session": row["session"],
            "phase": row["phase"],
            "edge_kind": row["edge_kind"],
            "runtime_state": reachability_state_to_raw(row["state"]),
        }
        for row in rows
    ]


def test_task7_rebuilds_failed_recovery_and_terminal_from_full_runtime_state() -> None:
    first, first_epoch, target, second, second_epoch, order, fill = (
        failed_successor_chain(retry_sessions=2)
    )
    fill.fill_id = ""
    chain = (target, second, second_epoch, order, fill)
    transitions = _raw_transitions(failed_recovery_trace(2, chain))
    raw = {
        "first_grant": asdict(first),
        "first_epoch": asdict(first_epoch),
        "second_grant": asdict(second),
        "second_epoch": asdict(second_epoch),
        "target": asdict(target),
        "order": asdict(order),
        "fill": asdict(fill),
        "fill_identity_sha256": physical_fill_identity_sha256(fill),
        "transitions": transitions,
    }

    validate_failed_grant_evidence(raw)
    validate_terminal_evidence({"transitions": transitions})

    hostile = deepcopy(raw)
    hostile["fill_identity_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="physical fill"):
        validate_failed_grant_evidence(hostile)

    hostile = deepcopy(raw)
    hostile["transitions"][-1]["runtime_state"]["capital_budget_level"] = 4
    with pytest.raises(ValueError, match="claims"):
        validate_failed_grant_evidence(hostile)


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
