from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization_matrix import (
    _fixture_reference_contract,
    _provenance,
    _runner_payload,
    _scenarios,
)

from uquant.config import (
    DEFAULT_CONFIG,
)
from uquant.validation import generalization_matrix as matrix_module
from uquant.validation import generalization_reference as reference_module
from uquant.validation.generalization_matrix import (
    execute_generalization_matrix,
    validate_matrix_artifact,
)
from uquant.validation.generalization_reference import (
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
)


def test_champion_exact_equality_passes_but_mutation_fails(
    matrix_data_dir: Path,
) -> None:
    """Catches a default comparison that rejects equality or tolerates a regression."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    champion = {
        f"{cell['window']}/{cell['scenario']}": copy.deepcopy(cell["metrics"])
        for cell in artifact["cells"]
        if cell["economic"]
    }

    assert validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        champion_cells=champion,
    ) == ()
    mutated = copy.deepcopy(artifact)
    first = next(cell for cell in mutated["cells"] if cell["economic"])
    first["metrics"]["final_wealth"] -= 0.01
    failures = validate_matrix_artifact(
        mutated,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        champion_cells=champion,
    )
    assert any("champion equality" in failure for failure in failures)

def test_v2_projection_uses_reconstructed_legacy_control_and_only_normalizes_validated_bindings() -> None:
    frozen = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = copy.deepcopy(frozen)
    candidate["schema_version"] = 2
    candidate["attribution_definition"] = copy.deepcopy(
        matrix_module._ATTRIBUTION_DEFINITION
    )
    for cell in candidate["cells"]:
        cell["attribution_status"] = (
            "VALID"
            if cell["metrics"] is not None
            else "ERROR"
            if cell["replay_error"]
            else "INSUFFICIENT_SAMPLE"
        )
        cell["attribution"] = {"replacement": "closed-schema placeholder"} if cell["metrics"] else None
        cell["concentration"] = {"replacement": "closed-schema placeholder"} if cell["metrics"] else None
        if cell["raw"] is not None:
            # The v1 field used reason-text classification and post-window
            # prices.  It is not reproduced by v2; the migration contract
            # admits only the compiled frozen payload and rejects injection.
            cell["raw"].pop("attribution")
            cell["raw"]["legacy_decision_digests"] = copy.deepcopy(
                cell["raw"]["decision_digests"]
            )
            cell["raw"]["decision_digests"] = ["new attribution-bearing digest"]
            cell["raw"]["decision_trace"] = [{"new": "strictly validated control evidence"}]
            cell["raw"]["daily_replay_evidence"] = [{"new": "strictly validated replay evidence"}]
            account = cell["raw"]["final_account"]
            account["schema_version"] = 5
            account["code_hash"] = "new committed source fingerprint"
            if account["fills"]:
                account["fills"][0]["event_id"] = "evt_" + "1" * 64

    expected = load_generalization_baseline().attribution_neutral_equality_sha256
    assert matrix_module._SCHEMA_VERSION == 2
    assert reference_module._attribution_neutral_equality_sha256(candidate) == expected

    injected = copy.deepcopy(candidate)
    valid_injected = next(cell for cell in injected["cells"] if cell["metrics"] is not None)
    valid_injected["raw"]["attribution"] = {"forged": "deprecated v1 evidence"}
    with pytest.raises(ValueError, match="deprecated v1 attribution"):
        reference_module._attribution_neutral_equality_sha256(injected)

    changed_frozen = copy.deepcopy(frozen)
    valid_frozen = next(cell for cell in changed_frozen["cells"] if cell["metrics"] is not None)
    valid_frozen["raw"]["attribution"]["by_reason"]["forged"] = {}
    with pytest.raises(ValueError, match="deprecated v1 attribution"):
        reference_module._attribution_neutral_equality_sha256(changed_frozen)

    for mutation in ("metric", "cash", "legacy_decision", "arbitrary_raw_field"):
        changed = copy.deepcopy(candidate)
        valid = next(cell for cell in changed["cells"] if cell["metrics"] is not None)
        if mutation == "metric":
            valid["metrics"]["final_wealth"] += 0.000001
        elif mutation == "cash":
            valid["raw"]["final_account"]["cash"] += 0.01
        elif mutation == "legacy_decision":
            valid["raw"]["legacy_decision_digests"][0] = "0" * 64
        else:
            valid["raw"]["arbitrary"] = None
        assert reference_module._attribution_neutral_equality_sha256(changed) != expected

def test_v2_projection_normalizes_only_compile_anchored_config_deletion() -> None:
    from uquant.config_governance import validate_governed_config_migration

    migration = validate_governed_config_migration(DEFAULT_CONFIG)
    raw = {"effective_config_sha256": migration.candidate_config_sha256}

    assert reference_module._project_raw_evidence_for_frozen_v1(
        raw,
        source_schema=2,
        config_migration=migration,
    )["effective_config_sha256"] == migration.champion_config_sha256

    raw["effective_config_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="migration carrier"):
        reference_module._project_raw_evidence_for_frozen_v1(
            raw,
            source_schema=2,
            config_migration=migration,
        )

def test_v2_policy_evaluator_accepts_verified_fixture_exact_equality(
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches strict v2 readback rejecting its own verified canonical artifact."""
    from uquant.data import DataStore

    scenarios = _scenarios()
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
    )
    loaded_symbols: list[str] = []
    original_load = DataStore.load

    def tracked_load(store: DataStore, symbol: str) -> Any:
        loaded_symbols.append(symbol)
        return original_load(store, symbol)

    monkeypatch.setattr(DataStore, "load", tracked_load)

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=matrix_data_dir,
    )

    assert result["passed"] is True
    assert result["exact_equality_passed"] is True
    assert result["economic_cells_valid"] == 32
    assert result["replay_error_cells"] == 0
    assert loaded_symbols
    assert len(loaded_symbols) == len(set(loaded_symbols))

def test_v2_policy_evaluator_fails_before_projection_without_verified_data(
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a no-data call hiding a self-signed current trace mutation."""

    scenarios = _scenarios()
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
    )
    changed = copy.deepcopy(artifact)
    raw = next(item["raw"] for item in changed["cells"] if item["economic"])
    raw["decision_trace"][0]["schema"] = "uquant.self-signed-control-plane.v2"
    raw["decision_digests"][0] = hashlib.sha256(
        json.dumps(
            raw["decision_trace"][0],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    with pytest.raises(ValueError, match="explicit frozen data"):
        evaluate_generalization_policy_artifact(
            changed,
            baseline=baseline,
            policy=policy,
            require_exact_equality=True,
        )

@pytest.mark.parametrize(
    ("mutation", "failure_text"),
    (
        ("decision_digest", "decision digest"),
        ("account_schema", "account schema"),
        ("account_code", "account code hash"),
        ("fill_event", "event identity"),
        ("raw_legacy_attribution", "deprecated v1 attribution"),
    ),
)
def test_v2_policy_evaluator_validates_control_plane_before_frozen_projection(
    mutation: str,
    failure_text: str,
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches v2 current-control tamper being hidden by the frozen-v1 projection."""
    scenarios = _scenarios()
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
        raising=False,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["metrics"] is not None)
    if mutation == "decision_digest":
        cell["raw"]["decision_digests"][0] = "0" * 64
    elif mutation == "account_schema":
        cell["raw"]["final_account"]["schema_version"] = 999
    elif mutation == "account_code":
        cell["raw"]["final_account"]["code_hash"] = "0" * 64
    elif mutation == "raw_legacy_attribution":
        cell["raw"]["attribution"] = {"forged": "deprecated v1 evidence"}
    else:
        cell["raw"]["final_account"]["fills"][0]["event_id"] = "evt_" + "0" * 64

    result = evaluate_generalization_policy_artifact(
        changed,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=matrix_data_dir,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any(failure_text in failure for failure in result["failures"])

def test_matrix_preserves_replay_error_continues_and_excludes_it_from_quantiles(
    matrix_data_dir: Path,
) -> None:
    """Catches one engine exception aborting the matrix or becoming a fake metric."""
    scenarios = _scenarios()
    failing = next(item for item in scenarios if item.name == "random__20__0000")
    executed: list[str] = []

    def runner(scenario: Any) -> dict[str, Any]:
        executed.append(scenario.name)
        if scenario is failing:
            raise RuntimeError("allocator failed\n  without a finite result")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )

    assert len(executed) == 32
    assert executed[-1] == "random__20__0004"
    error_cell = next(cell for cell in artifact["cells"] if cell["scenario"] == failing.name)
    assert error_cell["raw"] is None
    assert error_cell["metrics"] is None
    assert error_cell["replay_error"] == {
        "exception_type": "RuntimeError",
        "message": "allocator failed without a finite result",
    }
    assert artifact["aggregates"]["all"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["all"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["all"]["replay_error_cells"] == 1
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["by_window"]["h1_2023"]["replay_error_cells"] == 1
    valid_wealth = [
        float(cell["metrics"]["final_wealth"])
        for cell in artifact["cells"]
        if cell["metrics"] is not None
    ]
    assert artifact["aggregates"]["all"]["worst_wealth"] == min(valid_wealth)
    assert artifact["passed"] is False
    assert artifact["failures"] == [
        "cell replay failed: h1_2023/random__20__0000: RuntimeError: "
        "allocator failed without a finite result"
    ]
