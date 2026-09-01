from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from _absolute_generalization_metrics_fixture import (
    complete_replay,
    payload,
    replay_error,
    scenario,
)
from test_absolute_generalization_metrics import _identities

import uquant.validation.absolute_generalization._acceptance_evidence as acceptance_evidence
from uquant.validation.absolute_generalization import (
    derive_cell_metrics,
    load_absolute_generalization_contract,
    validate_cell_artifact,
)
from uquant.validation.absolute_generalization.artifacts import (
    reject_self_assertion_claims,
)


def test_strict_round_trip_rejects_extra_self_asserted_or_tampered_fields() -> None:
    contract = load_absolute_generalization_contract()
    artifact = derive_cell_metrics(complete_replay(), scenario(), _identities())
    raw = artifact.to_dict()

    assert validate_cell_artifact(raw, contract) == artifact

    with pytest.raises(ValueError, match="self-asserted pass"):
        validate_cell_artifact({**raw, "passed": True}, contract)

    changed = dict(raw)
    assert isinstance(changed["metrics"], dict)
    changed_metrics = dict(changed["metrics"])
    changed_metrics["final_wealth"] = 99.0
    changed["metrics"] = changed_metrics
    with pytest.raises(ValueError, match="seal"):
        validate_cell_artifact(changed, contract)

    resealed = dict(changed)
    from uquant.contracts.strict_json import canonical_json_sha256

    resealed["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in resealed.items() if key != "canonical_sha256"}
    )
    with pytest.raises(ValueError, match="metric identity"):
        validate_cell_artifact(resealed, contract)


def test_strict_round_trip_accepts_production_predicate_passed_fact() -> None:
    """A predicate observation named passed is evidence, not an acceptance claim."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    repair = decision["risk_summary"]["flat_book_capital_repair"]
    repair["predicate_results"] = [
        {
            "authoritative_state": {"positive_position_symbols": []},
            "code": "ALL_CASH",
            "economic_authority": False,
            "orphan_residue": False,
            "passed": True,
        }
    ]
    replay = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )
    contract = load_absolute_generalization_contract()
    artifact = derive_cell_metrics(replay, scenario(), _identities())

    assert validate_cell_artifact(artifact.to_dict(), contract) == artifact


@pytest.mark.parametrize(
    ("kind", "owner"),
    (
        ("historical_crowning", "flat_book_capital_repair"),
        ("cross_industry_crowning", "strategic_cash_rearm"),
        ("failed_grant_recovery", "flat_book_capital_repair"),
        ("repair_bounds", "flat_book_capital_repair"),
    ),
)
def test_recovery_manifest_accepts_owned_production_predicate_paths(
    kind: str,
    owner: str,
) -> None:
    predicate = {
        "authoritative_state": {"positive_position_symbols": []},
        "code": "ALL_CASH",
        "economic_authority": True,
        "orphan_residue": False,
        "passed": True,
    }
    account = {owner: {"predicate_results": [predicate]}}
    if kind in {"historical_crowning", "cross_industry_crowning"}:
        raw: dict[str, object] = {kind: {"final_account": account}}
    elif kind == "failed_grant_recovery":
        raw = {kind: {"transitions": [{"runtime_state": {"account_payload": account}}]}}
    else:
        raw = {kind: [{"observations": [{"runtime_state": {"account_payload": account}}]}]}
    reject_self_assertion_claims(raw, label="manifest")


def test_crowning_account_decode_normalizes_epoch_only_cohort_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = {
        "strategic_epochs": [{"epoch_id": "epoch-a", "grant_id": "grant-a", "owner_symbol": "owner"}],
        "order_ledger": [
            {
                "epoch_id": "epoch-a",
                "grant_id": "",
                "symbol": "cohort",
                "origin_subsystem": "STRATEGIC",
            }
        ],
    }

    def decode(value: object, *, require_hashes: bool) -> SimpleNamespace:
        assert require_hashes is False
        assert isinstance(value, dict)
        assert value["order_ledger"][0]["grant_id"] == "grant-a"  # type: ignore[index]
        return SimpleNamespace(fills=(), strategic_epochs=(), order_ledger=())

    monkeypatch.setattr(acceptance_evidence, "account_from_dict", decode)

    assert acceptance_evidence._crowning_account_indexes(raw) == ({}, {}, {})
    assert raw["order_ledger"][0]["grant_id"] == ""  # type: ignore[index]


def test_initial_crowning_requires_no_rearm_authorization_session() -> None:
    chain = SimpleNamespace(
        grant=SimpleNamespace(authorization_id=""),
        raw={"authorization_session": ""},
    )

    assert acceptance_evidence._crowning_authorization_session(chain) == ""
    chain.raw["authorization_session"] = "2026-01-05"
    with pytest.raises(ValueError, match="authorization"):
        acceptance_evidence._crowning_authorization_session(chain)


def test_strict_round_trip_rejects_predicate_shaped_pass_at_untrusted_path() -> None:
    """The production DTO shape is trusted only at its owned ledger path."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["untrusted_acceptance_claim"] = {
        "authoritative_state": {},
        "code": "CAPABILITY_PASS",
        "economic_authority": False,
        "orphan_residue": False,
        "passed": True,
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

    with pytest.raises(ValueError, match="self-asserted pass"):
        validate_cell_artifact(artifact.to_dict(), contract)


def test_strict_round_trip_rejects_nested_fake_replay_predicate_path() -> None:
    """A nested replay-shaped suffix cannot manufacture an owned predicate path."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["untrusted"] = {
        "replay_evidence": {
            "final_account_payload": {
                "value": {
                    "flat_book_capital_repair": {
                        "predicate_results": [
                            {
                                "authoritative_state": {},
                                "code": "CAPABILITY_PASS",
                                "economic_authority": False,
                                "orphan_residue": False,
                                "passed": True,
                            }
                        ]
                    }
                }
            }
        }
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

    with pytest.raises(ValueError, match="self-asserted pass"):
        validate_cell_artifact(artifact.to_dict(), contract)


def test_replay_error_artifact_is_explicit_non_applicable_and_metric_free() -> None:
    contract = load_absolute_generalization_contract()
    artifact = derive_cell_metrics(replay_error(), scenario(), _identities())
    raw = artifact.to_dict()

    assert artifact.status == "REPLAY_ERROR"
    assert artifact.replay_error == "DataContractError: fixture missing"
    assert artifact.metrics is None
    assert raw["metrics"] is None
    assert raw["event_facts"]
    assert isinstance(raw["event_facts"], dict)
    assert all(
        set(fact) == {"applicable", "observed", "healthy_sessions", "reason"}
        and fact
        == {
            "applicable": False,
            "observed": False,
            "healthy_sessions": 0,
            "reason": "REPLAY_ERROR",
        }
        for fact in raw["event_facts"].values()
    )
    assert validate_cell_artifact(raw, contract) == artifact

    fabricated = dict(raw)
    fabricated["metrics"] = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()[
        "metrics"
    ]
    with pytest.raises(ValueError, match=r"replay-error.*metric-free"):
        validate_cell_artifact(fabricated, contract)

    self_asserted = dict(raw)
    self_asserted["runner_success"] = True
    with pytest.raises(ValueError, match="self-asserted pass"):
        validate_cell_artifact(self_asserted, contract)


def test_replay_error_rejects_resealed_applicability_claims() -> None:
    """A replay error cannot be relabeled as reconciled evidence by resealing it."""

    from uquant.contracts.strict_json import canonical_json_sha256

    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(replay_error(), scenario(), _identities()).to_dict()
    raw["accounting_reconciled"] = True
    raw["target_order_fill_identity_reconciled"] = True
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match=r"replay-error.*non-applicable"):
        validate_cell_artifact(raw, contract)


def test_complete_artifact_rejects_resealed_replay_error_event_fact() -> None:
    """Complete evidence cannot silently reuse a replay-error event sentinel."""

    from uquant.contracts.strict_json import canonical_json_sha256

    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()
    assert isinstance(raw["event_facts"], dict)
    event_facts = dict(raw["event_facts"])
    event_facts["terminal_zero_strategic_target_state"] = {
        "applicable": False,
        "observed": False,
        "healthy_sessions": 0,
        "reason": "REPLAY_ERROR",
    }
    raw["event_facts"] = event_facts
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match=r"complete.*event fact"):
        validate_cell_artifact(raw, contract)


def test_artifact_rejects_identity_tamper_even_when_outer_seal_is_recomputed() -> None:
    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()
    assert isinstance(raw["identities"], dict)
    identities = dict(raw["identities"])
    identities["universe_sha256"] = "f" * 64
    raw["identities"] = identities
    from uquant.contracts.strict_json import canonical_json_sha256

    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match="universe identity"):
        validate_cell_artifact(raw, contract)


def test_artifact_rejects_resealed_valid_metric_tamper() -> None:
    """The raw replay, not an author-recomputable seal, owns derived metrics."""

    from uquant.contracts.strict_json import canonical_json_sha256

    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()
    assert isinstance(raw["metrics"], dict)
    metrics = dict(raw["metrics"])
    metrics["max_drawdown"] = 0.5
    raw["metrics"] = metrics
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match="derived metrics"):
        validate_cell_artifact(raw, contract)


def test_artifact_rejects_resealed_replay_derived_role_identity_tamper() -> None:
    """Role identities must be recomputed from replay membership evidence."""

    from uquant.contracts.strict_json import canonical_json_sha256

    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()
    assert isinstance(raw["identities"], dict)
    identities = dict(raw["identities"])
    identities["tradable_role_identity"] = "f" * 64
    raw["identities"] = identities
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match="tradable role identity"):
        validate_cell_artifact(raw, contract)


def test_artifact_rejects_resealed_noncanonical_replay_number_type() -> None:
    """Strict replay decoding cannot normalize an integer into sealed float evidence."""

    from uquant.contracts.strict_json import canonical_json_sha256

    contract = load_absolute_generalization_contract()
    raw = derive_cell_metrics(complete_replay(), scenario(), _identities()).to_dict()
    assert isinstance(raw["replay_evidence"], dict)
    replay_evidence = dict(raw["replay_evidence"])
    replay_evidence["initial_cash"] = 1_000
    raw["replay_evidence"] = replay_evidence
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )

    with pytest.raises(ValueError, match=r"replay evidence initial cash.*malformed"):
        validate_cell_artifact(raw, contract)
