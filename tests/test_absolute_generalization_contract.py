from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.contracts.universe import default_ai_universe
from uquant.provenance.fingerprints import (
    git_source_surface_fingerprint,
    source_surface_fingerprint,
)
from uquant.provenance.surfaces import load_source_surface_registry

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "benchmarks/absolute_generalization_acceptance_contract.json"
OWNERSHIP_PATH = ROOT / "benchmarks/strategic_ownership_acceptance_contract.json"
BASELINE_COMMIT = "d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5"
HISTORICAL_BASELINE_SOURCE = (
    "cacef64c25053a84e1aad073feec252d8cb9d2decb19576460642a3b6ec6573f"
)
OWNERSHIP_CONTRACT_SHA256 = (
    "72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08"
)
CURRENT_CANDIDATE_SOURCE = (
    "d1ef7977ae482e46a920381e6af58791199ec8e1a02586dbe8df451e7d4696c9"
)
BASELINE_SOURCE_AT_COMMIT = CURRENT_CANDIDATE_SOURCE
AI_UNIVERSE_SHA256 = (
    "03f42c5066fb8e1c7b2f8e1b7dd38d508d8053f548ebb5596317ce587d7cffd0"
)

CANONICAL_UNIVERSE = (
    "sh600487",
    "sh601869",
    "sh603688",
    "sh603986",
    "sh688008",
    "sh688012",
    "sh688019",
    "sh688037",
    "sh688041",
    "sh688072",
    "sh688082",
    "sh688110",
    "sh688120",
    "sh688146",
    "sh688200",
    "sh688233",
    "sh688256",
    "sh688268",
    "sh688300",
    "sh688347",
    "sh688361",
    "sh688498",
    "sh688766",
    "sz000636",
    "sz002281",
    "sz002371",
    "sz002409",
    "sz300054",
    "sz300223",
    "sz300308",
    "sz300394",
    "sz300502",
    "sz300604",
    "sz300666",
)

EXPECTED_SHARDS = {
    "loo-a": (
        "sh600487",
        "sh688019",
        "sh688120",
        "sh688300",
        "sz002281",
        "sz300394",
    ),
    "loo-b": (
        "sh601869",
        "sh688037",
        "sh688146",
        "sh688347",
        "sz002371",
        "sz300502",
    ),
    "loo-c": (
        "sh603688",
        "sh688041",
        "sh688200",
        "sh688361",
        "sz002409",
        "sz300604",
    ),
    "loo-d": (
        "sh603986",
        "sh688072",
        "sh688233",
        "sh688498",
        "sz300054",
        "sz300666",
    ),
    "loo-e": (
        "sh688008",
        "sh688082",
        "sh688256",
        "sh688766",
        "sz300223",
    ),
    "loo-f": (
        "sh688012",
        "sh688110",
        "sh688268",
        "sz000636",
        "sz300308",
    ),
}


def _contract_module() -> object:
    return importlib.import_module("uquant.validation.absolute_generalization.contract")


def _raw_contract() -> dict[str, object]:
    value = json.loads(CONTRACT_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


def _write_contract(tmp_path: Path, raw: str | bytes) -> Path:
    path = tmp_path / "contract.json"
    path.write_bytes(raw.encode("utf-8") if isinstance(raw, str) else raw)
    return path


def test_contract_is_strict_canonical_json_with_exact_schema_and_compiled_seal() -> None:
    module = _contract_module()
    raw_bytes = CONTRACT_PATH.read_bytes()
    raw = _raw_contract()

    assert raw_bytes == canonical_json_bytes(raw) + b"\n"
    assert set(raw) == {
        "baseline_can_relax_absolute_limits",
        "candidate",
        "canonical_sha256",
        "canonical_universe",
        "components",
        "contract_id",
        "critical_removals",
        "frozen_baseline",
        "inputs",
        "percentile_method",
        "required_witnesses",
        "schema_version",
        "shards",
        "thresholds",
        "window",
    }
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    expected_seal = hashlib.sha256(canonical_json_bytes(unsealed)).hexdigest()
    assert raw["canonical_sha256"] == expected_seal
    assert expected_seal == module.ABSOLUTE_GENERALIZATION_CONTRACT_SHA256


def test_contract_freezes_exact_absolute_policy_and_historical_baseline() -> None:
    module = _contract_module()
    contract = module.load_absolute_generalization_contract(CONTRACT_PATH)
    raw = _raw_contract()

    assert contract.baseline_can_relax_absolute_limits is False
    assert raw["baseline_can_relax_absolute_limits"] is False
    assert raw["contract_id"] == "absolute-generalization-acceptance"
    assert raw["schema_version"] == 1
    assert raw["window"] == {"end": "2026-08-05", "start": "2023-01-03"}
    assert raw["percentile_method"] == "linear_interpolation_at_(n-1)*probability"
    assert raw["components"] == [
        "champion_non_regression",
        "absolute_strategic_robustness",
        "failed_grant_recovery",
        "witness_resilience",
        "repeated_crowning",
        "bounded_healthy_cash_vacancy",
        "complete_literal_metrics",
    ]
    assert raw["critical_removals"] == ["sz300308", "sz300502", "sz300394"]
    assert raw["required_witnesses"] == [
        "sh603688",
        "sh688008",
        "sh688082",
        "sz002409",
        "sz300666",
    ]
    assert raw["frozen_baseline"] == {
        "champion_final_wealth": 24.509661802900865,
        "champion_maximum_drawdown": 0.3,
        "champion_minimum_final_wealth": 23.28417871275582,
        "production_source_sha256": HISTORICAL_BASELINE_SOURCE,
        "strategic_ownership_contract_sha256": OWNERSHIP_CONTRACT_SHA256,
    }
    assert raw["thresholds"] == {
        "maximum_failed_grant_retry_healthy_sessions": 20,
        "maximum_p90_drawdown": 0.3,
        "maximum_p90_healthy_zero_total_target_streak": 60,
        "maximum_terminal_zero_strategic_target_scc_sessions": 60,
        "maximum_worst_healthy_zero_total_target_streak": 120,
        "minimum_p10_final_wealth": 1.0,
        "minimum_positive_return_fraction": 0.9,
        "minimum_repeated_crowning_actual_epochs": 2,
        "minimum_repeated_crowning_distinct_owners": 2,
        "minimum_witness_fraction": 1.0,
        "positive_return_final_wealth_exclusive_minimum": 1.0,
        "repair_bounds": [
            {
                "maximum_healthy_sessions": 20,
                "persisted_damage_level": 1,
                "target_budget_level": 0,
            },
            {
                "maximum_healthy_sessions": 40,
                "persisted_damage_level": 2,
                "target_budget_level": 1,
            },
            {
                "maximum_healthy_sessions": 60,
                "persisted_damage_level": 3,
                "target_budget_level": 2,
            },
            {
                "maximum_healthy_sessions": 60,
                "persisted_damage_level": 4,
                "target_budget_level": 3,
            },
        ],
    }
    with pytest.raises(FrozenInstanceError):
        contract.baseline_can_relax_absolute_limits = True


def test_contract_binds_candidate_and_frozen_inputs_to_independent_authorities() -> None:
    module = _contract_module()
    contract = module.load_absolute_generalization_contract(CONTRACT_PATH)
    raw = _raw_contract()
    registry = load_source_surface_registry(ROOT)

    assert raw["candidate"] == {
        "baseline_commit": BASELINE_COMMIT,
        "baseline_source_sha256": BASELINE_SOURCE_AT_COMMIT,
        "production_source_sha256": CURRENT_CANDIDATE_SOURCE,
        "source_surface_id": "economic_decision_v1",
        "source_surface_registry_sha256": registry.canonical_sha256,
    }
    assert raw["inputs"] == {
        "ai_universe_sha256": AI_UNIVERSE_SHA256,
        "effective_config_sha256": "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5",
        "frozen_data": {
            "checksums_sha256": "ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29",
            "files_verified": 36,
            "manifest_sha256": "343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d",
            "snapshot_id": "20260809T094222Z-causal-tech-index-rebase",
        },
        "uv_lock_sha256": "4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61",
    }
    assert config_fingerprint(DEFAULT_CONFIG) == raw["inputs"]["effective_config_sha256"]
    assert hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest() == raw["inputs"][
        "uv_lock_sha256"
    ]
    assert default_ai_universe().sha256 == AI_UNIVERSE_SHA256
    assert contract.canonical_universe == default_ai_universe().symbols == CANONICAL_UNIVERSE
    assert git_source_surface_fingerprint(
        ROOT, BASELINE_COMMIT, "economic_decision_v1"
    ) == BASELINE_SOURCE_AT_COMMIT
    assert source_surface_fingerprint(ROOT, "economic_decision_v1") == CURRENT_CANDIDATE_SOURCE
    ownership = json.loads(OWNERSHIP_PATH.read_bytes())
    assert hashlib.sha256(canonical_json_bytes(ownership)).hexdigest() == (
        OWNERSHIP_CONTRACT_SHA256
    )
    assert tuple(ownership["canonical_universe"]) == CANONICAL_UNIVERSE


def test_loader_binds_baseline_and_evolving_candidate_sources_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _contract_module()
    raw = _raw_contract()
    baseline_source = "1" * 64
    candidate_source = "2" * 64
    candidate = raw["candidate"]
    assert isinstance(candidate, dict)
    candidate["baseline_source_sha256"] = baseline_source
    candidate["production_source_sha256"] = candidate_source
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    seal = hashlib.sha256(canonical_json_bytes(unsealed)).hexdigest()
    raw["canonical_sha256"] = seal
    monkeypatch.setattr(module, "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256", seal)
    monkeypatch.setattr(module, "_BASELINE_SOURCE", baseline_source, raising=False)
    monkeypatch.setattr(module, "_CANDIDATE_SOURCE", candidate_source)
    monkeypatch.setattr(
        module,
        "git_source_surface_fingerprint",
        lambda *_args: baseline_source,
    )
    monkeypatch.setattr(
        module,
        "source_surface_fingerprint",
        lambda *_args: candidate_source,
    )

    contract = module.load_absolute_generalization_contract(
        _write_contract(tmp_path, canonical_json_bytes(raw) + b"\n")
    )

    assert contract.candidate.baseline_source_sha256 == baseline_source
    assert contract.candidate.production_source_sha256 == candidate_source


@pytest.mark.parametrize(
    ("document", "message"),
    (
        ('{"schema_version":1,"schema_version":1}', "duplicate JSON key"),
        ('{"value":NaN}', "nonstandard JSON constant"),
        ('{"value":Infinity}', "nonstandard JSON constant"),
        ('{"value":-Infinity}', "nonstandard JSON constant"),
        ('{"value":1e999}', "only finite numbers"),
    ),
)
def test_contract_rejects_duplicate_keys_and_nonfinite_json(
    tmp_path: Path, document: str, message: str
) -> None:
    module = _contract_module()

    with pytest.raises(ValueError, match=message):
        module.load_absolute_generalization_contract(_write_contract(tmp_path, document))


@pytest.mark.parametrize("symlink_kind", ("leaf", "ancestor"))
def test_contract_physical_reader_rejects_leaf_and_ancestor_symlinks(
    tmp_path: Path, symlink_kind: str
) -> None:
    module = _contract_module()
    reader = getattr(module, "_read_physical_regular_file", None)
    assert callable(reader), "contract module must expose its private physical reader"
    if symlink_kind == "leaf":
        unsafe = tmp_path / "contract.json"
        unsafe.symlink_to(CONTRACT_PATH)
    else:
        physical = tmp_path / "physical"
        physical.mkdir()
        (physical / "contract.json").write_bytes(CONTRACT_PATH.read_bytes())
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(physical, target_is_directory=True)
        unsafe = linked_parent / "contract.json"

    with pytest.raises(ValueError, match="missing or unsafe"):
        reader(unsafe, label="absolute generalization contract")


def test_ownership_authority_reader_rejects_symlinks(tmp_path: Path) -> None:
    module = _contract_module()
    reader = getattr(module, "_read_ownership_contract", None)
    assert callable(reader), "contract module must expose its private ownership reader"
    unsafe = tmp_path / "ownership.json"
    unsafe.symlink_to(OWNERSHIP_PATH)

    with pytest.raises(ValueError, match="missing or unsafe"):
        reader(unsafe)


def test_contract_rejects_tampering_even_when_candidate_reseals_it(tmp_path: Path) -> None:
    module = _contract_module()
    raw = _raw_contract()
    thresholds = raw["thresholds"]
    assert isinstance(thresholds, dict)
    thresholds["minimum_positive_return_fraction"] = 0.1
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    raw["canonical_sha256"] = hashlib.sha256(canonical_json_bytes(unsealed)).hexdigest()

    with pytest.raises(ValueError, match="compiled contract identity"):
        module.load_absolute_generalization_contract(
            _write_contract(tmp_path, canonical_json_bytes(raw) + b"\n")
        )


def test_contract_public_surface_has_no_writer_or_auto_acceptance_path() -> None:
    package = importlib.import_module("uquant.validation.absolute_generalization")
    contract_module = _contract_module()

    assert set(contract_module.__all__) == {
        "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
        "AbsoluteGeneralizationContract",
        "load_absolute_generalization_contract",
    }
    assert set(package.__all__) == {
        "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
        "AbsoluteGeneralizationContract",
        "AbsoluteGeneralizationScenario",
        "build_leave_one_out_scenarios",
        "load_absolute_generalization_contract",
    }


def test_contract_sources_and_resource_are_registered_only_on_validation_surfaces() -> None:
    registry = load_source_surface_registry(ROOT)
    expected_sources = {
        "uquant/validation/absolute_generalization/__init__.py",
        "uquant/validation/absolute_generalization/contract.py",
        "uquant/validation/absolute_generalization/scenarios.py",
    }
    contract_resource = "benchmarks/absolute_generalization_acceptance_contract.json"

    validation = registry.surface("validation_runner_v1")
    full_package = registry.surface("full_package_v1")
    economic = registry.surface("economic_decision_v1")
    assert expected_sources <= set(validation.source_paths)
    assert expected_sources <= set(full_package.source_paths)
    assert contract_resource in validation.resource_paths
    assert contract_resource not in full_package.resource_paths
    assert expected_sources.isdisjoint(economic.source_paths)
    assert contract_resource not in economic.resource_paths
