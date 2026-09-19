"""Regression coverage for symbol-scoped production entry-check evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest
from _absolute_generalization_metrics_fixture import complete_replay, payload, scenario
from test_absolute_generalization_metrics import _identities

from uquant.validation.absolute_generalization import (
    derive_cell_metrics,
    load_absolute_generalization_contract,
    validate_cell_artifact,
)
from uquant.validation.absolute_generalization.artifacts import reject_self_assertion_claims


@pytest.mark.parametrize(
    "check",
    (
        {"as_of": "2023-01-03", "passed": True},
        {"as_of": "2023-01-03", "passed": False, "value": 0.6, "minimum": 0.7},
    ),
)
def test_strict_round_trip_accepts_entry_checks_in_cell_and_shard(
    check: dict[str, object],
) -> None:
    """Keep both production entry-check DTO forms at their exact symbol path."""
    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["core_allocation"] = {
        "symbols": {"sh600487": {"entry": {"checks": {"confidence": check}}}}
    }
    replay = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )
    contract = load_absolute_generalization_contract()
    artifact = derive_cell_metrics(replay, scenario(), _identities())
    raw = artifact.to_dict()
    assert validate_cell_artifact(raw, contract) == artifact
    reject_self_assertion_claims({"cells": [raw]}, label="manifest")


@pytest.mark.parametrize(
    "check",
    (
        {"as_of": "2023-01-03", "passed": 1},
        {"as_of": "2023-01-03", "passed": True, "capability_pass": True},
        {"as_of": "2023-01-03", "passed": True, "value": 1.0, "minimum": True},
    ),
)
def test_entry_check_path_does_not_trust_malformed_dtos(check: dict[str, object]) -> None:
    path = (
        "replay_evidence", "observations", 0, "decision_payload", "value",
        "risk_summary", "core_allocation", "symbols", "sh600487", "entry",
        "checks", "confidence",
    )
    with pytest.raises(ValueError, match="self-asserted pass at"):
        reject_self_assertion_claims(check, path=path)


@pytest.mark.parametrize(
    "tail",
    (
        ("core_allocation", "symbols", "entry", "checks", "untrusted", "confidence"),
        ("core_allocation", "symbols", "sh600487", "checks", "entry", "confidence"),
    ),
)
def test_entry_check_path_rejects_shifted_symbol_layers(tail: tuple[str, ...]) -> None:
    path = (
        "replay_evidence", "observations", 0, "decision_payload", "value",
        "risk_summary", *tail,
    )
    with pytest.raises(ValueError, match="self-asserted pass"):
        reject_self_assertion_claims({"as_of": "2023-01-03", "passed": True}, path=path)


def test_entry_check_path_rejects_nested_fake_replay_root() -> None:
    path = (
        "untrusted", "replay_evidence", "observations", 0, "decision_payload",
        "value", "risk_summary", "core_allocation", "symbols", "sh600487",
        "entry", "checks", "confidence",
    )
    with pytest.raises(ValueError, match="self-asserted pass"):
        reject_self_assertion_claims({"as_of": "2023-01-03", "passed": True}, path=path)


