from __future__ import annotations

import ast
import copy
import json
from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest

from uquant.contracts.strict_json import canonical_json_sha256

from ._analysis import (
    _TASK9_RELOCATED_FUNCTION_DEBT,
    _TASK9_RELOCATED_GLOBAL_DEBT,
    _TASK9_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS,
    architecture_snapshot,
)
from ._task9_immutable_oracle import (
    candidate_behavior_from_subprocess,
    immutable_oracle_from_archive,
)
from ._task9_inventory import (
    build_task9_inventory,
    current_reflection_contract,
)
from ._task9_relocation import (
    GENERALIZATION_OWNERS,
    POLICY_OWNERS,
    approved_relocations,
    assert_owner_ast_exact,
    build_relocation_contract,
)

ROOT = Path(__file__).resolve().parents[2]
_TASK9_START = "719288f6067686b3199d305899ddc09adf098a0d"
_TASK9_START_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"
_ORACLE_EVIDENCE_COMMIT = "edc758ed438e1a47d58ff61072f1584ca9a2e8c4"
_ORACLE_RUNNER_BLOB = "090f8cfc1fea6a4f07ac252a6e1f52e3f46e83e9"
_ORACLE_RUNNER_SHA256 = "ed2d3f1f7c4d4ad29402eb77b00b4dd60f72063de2ef478f0f67ceff58dc7b94"
_CANDIDATE_EVIDENCE_COMMIT = "66582931df52e407b8c949048ce63a1789323982"
_CANDIDATE_RUNNER_BLOB = "37f09a28ee3c96cc71b36b5156576fc0e870e720"
_CANDIDATE_RUNNER_SHA256 = (
    "39a3cb1e9e410560f9cb2ea4fbc28930cc287f3d3b5d8759753b9700668c1282"
)
_INVENTORY = ROOT / "artifacts/architecture_refactor/task9_cleanup_inventory.json"
_VALIDATION_ORACLE = (
    ROOT / "artifacts/architecture_refactor/task9_validation_contract_oracle.json"
)
_LEGACY_IMPLEMENTATIONS = (
    "uquant/validation/generalization.py",
    "uquant/validation/generalization_reference.py",
    "uquant/validation/holdout.py",
    "uquant/validation/holdout_runtime.py",
    "uquant/validation/holdout_lanes.py",
    "uquant/risk_sentinel/cli.py",
    "uquant/risk_sentinel/validation.py",
)
_FIXED_REFERENCE_COUNTS = (5, 5, 11, 10, 7, 10, 8)
_IMPORT_CONSUMER_COUNTS = (16, 3, 8, 4, 4, 4, 1)


@cache
def _immutable_inventory() -> dict[str, Any]:
    value = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


@cache
def _rebuilt_inventory() -> dict[str, Any]:
    return build_task9_inventory(ROOT)


def _assert_inventory_seal(payload: dict[str, Any]) -> None:
    claimed = payload["canonical_sha256"]
    unsigned = dict(payload)
    del unsigned["canonical_sha256"]
    assert claimed == canonical_json_sha256(unsigned)


def _assert_immutable_inventory(payload: dict[str, Any]) -> None:
    _assert_inventory_seal(payload)
    assert payload == _immutable_inventory()


@cache
def _validation_oracle() -> dict[str, Any]:
    value = json.loads(_VALIDATION_ORACLE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _assert_validation_oracle_seals(payload: dict[str, Any]) -> None:
    assert payload["baseline_commit"] == _TASK9_START
    assert payload["baseline_tree"] == _TASK9_START_TREE
    assert payload["contract"] == "uquant-task9-validation-contract-oracle-v1"
    assert payload["success_sha256"] == canonical_json_sha256(payload["success"])
    assert payload["failure_order_sha256"] == canonical_json_sha256(
        payload["failure_order"]
    )
    unsigned = dict(payload)
    del unsigned["payload_sha256"]
    assert payload["payload_sha256"] == canonical_json_sha256(unsigned)


def test_task9_cleanup_inventory_precedes_every_legacy_replacement() -> None:
    payload = _immutable_inventory()
    assert payload["baseline_commit"] == _TASK9_START
    assert payload["baseline_tree"] == _TASK9_START_TREE
    assert payload["captured_while_all_implementations_intact"] == list(
        _LEGACY_IMPLEMENTATIONS
    )
    assert tuple(entry["path"] for entry in payload["entries"]) == (
        _LEGACY_IMPLEMENTATIONS
    )


def test_task9_cleanup_inventory_is_exactly_rebuilt_from_immutable_git() -> None:
    payload = _immutable_inventory()
    _assert_inventory_seal(payload)
    assert _rebuilt_inventory() == payload


def test_task9_cleanup_inventory_has_bidirectional_reference_partitions() -> None:
    payload = _immutable_inventory()
    entries = payload["entries"]
    assert isinstance(entries, list)
    assert tuple(
        len(entry["live_references"]["immutable_fixed_path_consumers"])
        for entry in entries
    ) == _FIXED_REFERENCE_COUNTS
    assert tuple(
        len(entry["live_references"]["ast_import_consumers"])
        for entry in entries
    ) == _IMPORT_CONSUMER_COUNTS
    classifications = (
        "current_executable_consumers",
        "historical_machine_evidence_to_preserve",
        "documentation_references",
        "other_current_or_contract_consumers",
    )
    for entry in entries:
        references = entry["live_references"]
        groups = [set(references[name]) for name in classifications]
        assert set().union(*groups) == set(
            references["immutable_fixed_path_consumers"]
        )
        assert sum(len(group) for group in groups) == len(set().union(*groups))


def test_task9_frozen_public_reflection_survives_all_import_modes() -> None:
    payload = _immutable_inventory()
    current = current_reflection_contract(ROOT)
    assert {
        "modules": current["normal"],
        "import_mode_sha256": current["mode_sha256"],
    } == payload["public_runtime_contract"]


@pytest.mark.parametrize(
    ("entry_index", "field"),
    (
        (0, "ast_import_consumers"),
        (2, "immutable_fixed_path_consumers"),
        (5, "dotted_runtime_identity_consumers"),
    ),
)
def test_task9_resigned_inventory_omission_is_rejected(
    entry_index: int, field: str
) -> None:
    payload = copy.deepcopy(_immutable_inventory())
    entries = payload["entries"]
    assert isinstance(entries, list)
    values = entries[entry_index]["live_references"][field]
    assert values
    values.pop()
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    _assert_inventory_seal(payload)
    with pytest.raises(AssertionError):
        _assert_immutable_inventory(payload)


def test_task9_resigned_immutable_blob_tamper_is_rejected() -> None:
    payload = copy.deepcopy(_immutable_inventory())
    entries = payload["entries"]
    assert isinstance(entries, list)
    entries[0]["content_sha256"] = "0" * 64
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    _assert_inventory_seal(payload)
    with pytest.raises(AssertionError):
        _assert_immutable_inventory(payload)


def test_task9_validation_oracle_is_fresh_from_immutable_archive(
    tmp_path: Path,
) -> None:
    payload = _validation_oracle()
    immutable = immutable_oracle_from_archive(
        root=ROOT,
        destination=tmp_path / "snapshot",
        baseline_commit=_TASK9_START,
        baseline_tree=_TASK9_START_TREE,
        evidence_commit=_ORACLE_EVIDENCE_COMMIT,
        runner_blob=_ORACLE_RUNNER_BLOB,
        runner_sha256=_ORACLE_RUNNER_SHA256,
        source_identities=payload["source_identity"]["legacy_files"],
    )
    _assert_validation_oracle_seals(immutable)
    assert immutable == payload


def test_task9_relocated_candidate_behavior_matches_frozen_oracle_exactly() -> None:
    frozen = _validation_oracle()
    candidate = candidate_behavior_from_subprocess(
        root=ROOT,
        evidence_commit=_CANDIDATE_EVIDENCE_COMMIT,
        runner_blob=_CANDIDATE_RUNNER_BLOB,
        runner_sha256=_CANDIDATE_RUNNER_SHA256,
    )
    assert candidate == {
        "success": frozen["success"],
        "failure_order": frozen["failure_order"],
    }


def test_task9_candidate_behavior_runner_tamper_is_rejected() -> None:
    with pytest.raises(AssertionError):
        candidate_behavior_from_subprocess(
            root=ROOT,
            evidence_commit=_CANDIDATE_EVIDENCE_COMMIT,
            runner_blob=_CANDIDATE_RUNNER_BLOB,
            runner_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "mutation",
    ("failure_message", "failure_omission", "source_identity"),
)
def test_task9_resigned_validation_oracle_tamper_is_rejected(
    mutation: str,
) -> None:
    payload = copy.deepcopy(_validation_oracle())
    if mutation == "failure_message":
        payload["failure_order"][0]["message"] = "candidate-authored replacement"
    elif mutation == "failure_omission":
        payload["failure_order"].pop()
        payload["coverage"]["failure_labels"].pop()
    else:
        payload["source_identity"]["legacy_files"][0]["sha256"] = "0" * 64
    payload["success_sha256"] = canonical_json_sha256(payload["success"])
    payload["failure_order_sha256"] = canonical_json_sha256(
        payload["failure_order"]
    )
    del payload["payload_sha256"]
    payload["payload_sha256"] = canonical_json_sha256(payload)
    _assert_validation_oracle_seals(payload)
    with pytest.raises(AssertionError):
        assert payload == _validation_oracle()


@cache
def _relocation_contract() -> dict[str, Any]:
    return build_relocation_contract(ROOT, _immutable_inventory())


def test_task9_checkpoint2_relocation_is_closed_and_source_bound() -> None:
    assert approved_relocations(_immutable_inventory()) == {
        "uquant/validation/generalization.py": tuple(sorted(GENERALIZATION_OWNERS)),
        "uquant/validation/generalization_reference.py": tuple(sorted(POLICY_OWNERS)),
    }
    payload = _relocation_contract()
    claimed = payload["canonical_sha256"]
    unsigned = dict(payload)
    del unsigned["canonical_sha256"]
    assert claimed == canonical_json_sha256(unsigned)
    assert not (ROOT / "uquant/validation/generalization.py").exists()
    assert len(
        (ROOT / "uquant/validation/generalization_reference.py")
        .read_text(encoding="utf-8")
        .splitlines()
    ) < 180


def test_task9_checkpoint2_resigned_relocation_tamper_is_rejected() -> None:
    payload = copy.deepcopy(_relocation_contract())
    payload["entries"][0]["owners"][0]["path"] = "uquant/validation/unapproved.py"
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    unsigned = dict(payload)
    claimed = unsigned.pop("canonical_sha256")
    assert claimed == canonical_json_sha256(unsigned)
    with pytest.raises(AssertionError):
        assert payload == _relocation_contract()


def test_task9_checkpoint2_owner_slices_are_immutable_ast_exact() -> None:
    assert_owner_ast_exact(ROOT)


@pytest.mark.parametrize("mutation", ("threshold", "comparison", "order"))
def test_task9_checkpoint2_ast_gate_rejects_rule_mutations(mutation: str) -> None:
    path = (
        "uquant/validation/generalization/gates.py"
        if mutation == "threshold"
        else "uquant/validation/generalization_policy/evaluator.py"
    )
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    if mutation == "threshold":
        value = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == 0.10
        )
        value.value = 0.11
    elif mutation == "comparison":
        comparison = next(node for node in ast.walk(tree) if isinstance(node, ast.Compare))
        comparison.ops[0] = ast.Gt() if isinstance(comparison.ops[0], ast.Lt) else ast.Lt()
    else:
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_generalization_policy_artifact"
        )
        function.body[2], function.body[3] = function.body[3], function.body[2]
    with pytest.raises(AssertionError):
        assert_owner_ast_exact(ROOT, candidate_sources={path: ast.unparse(tree)})


def _task9_legacy_module(module: str) -> str:
    if module == "uquant.validation.generalization" or module.startswith(
        "uquant.validation.generalization."
    ):
        return "uquant.validation.generalization"
    if module == "uquant.validation.generalization_reference" or module.startswith(
        "uquant.validation.generalization_policy."
    ):
        return "uquant.validation.generalization_reference"
    return module


def test_task9_checkpoint2_relocated_debt_is_exact_bidirectional_and_non_growing() -> None:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    relocated = graph["task9_relocated_private_imports"]
    assert {str(row["id"]) for row in relocated} == _TASK9_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if ".generalization." in str(row["importer"])
        or ".generalization_policy." in str(row["importer"])
        or ".generalization." in str(row["imported_from"])
        or ".generalization_policy." in str(row["imported_from"])
    }
    immutable_symbols = {
        str(entry["module"]): set(entry["defined_top_level_symbols"])
        for entry in _immutable_inventory()["entries"][:2]
    }
    for row in relocated:
        importer = _task9_legacy_module(str(row["importer"]))
        imported_from = _task9_legacy_module(str(row["imported_from"]))
        name = str(row["name"])
        if importer == imported_from:
            assert name in immutable_symbols[importer]
        else:
            assert (
                importer,
                imported_from,
                name,
            ) == (
                "uquant.validation.generalization_reference",
                "uquant.validation.generalization_matrix",
                "_head_and_source",
            )

    functions = snapshot["functions"]
    candidate_function_debt = {
        str(row["id"])
        for row in functions
        if str(row["id"]).startswith(
            (
                "uquant.validation.generalization.",
                "uquant.validation.generalization_policy.",
            )
        )
        and (
            int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
            or int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
        )
    }
    assert set(_TASK9_RELOCATED_FUNCTION_DEBT) == candidate_function_debt
    baseline_debt = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(
            encoding="utf-8"
        )
    )["architecture_debt"]["initial"]
    legacy_function_debt = {
        str(row["id"])
        for category in ("long_functions", "branchy_functions")
        for row in baseline_debt[category]
    }
    assert {
        legacy for legacy, _ in _TASK9_RELOCATED_FUNCTION_DEBT.values()
    } <= legacy_function_debt

    candidate_globals = {
        str(row["id"])
        for row in snapshot["module_globals"]
        if str(row["id"]).startswith(
            (
                "uquant.validation.generalization.",
                "uquant.validation.generalization_policy.",
            )
        )
        and (bool(row["mutable_initializer"]) or bool(row["mutation_sites"]))
    }
    assert set(_TASK9_RELOCATED_GLOBAL_DEBT) == candidate_globals
    legacy_global_debt = {
        str(row["id"]) for row in baseline_debt["mutable_module_globals"]
    }
    assert set(_TASK9_RELOCATED_GLOBAL_DEBT.values()) <= legacy_global_debt


def test_task9_checkpoint2_unknown_debt_is_not_hidden_by_relocation() -> None:
    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    path = "uquant/validation/generalization/baseline.py"
    source_texts[path] += (
        "\nfrom .models import _TASK9_UNREVIEWED_PRIVATE\n"
        "_TASK9_UNREVIEWED_MUTABLE = []\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    graph = mutation["import_graph"]
    assert (
        "uquant.validation.generalization.baseline:"
        "uquant.validation.generalization.models:_TASK9_UNREVIEWED_PRIVATE"
    ) in {str(row["id"]) for row in graph["cross_module_private_imports"]}
    assert "uquant.validation.generalization.baseline:_TASK9_UNREVIEWED_MUTABLE" in {
        str(row["id"])
        for row in mutation["module_globals"]
        if bool(row["mutable_initializer"]) or bool(row["mutation_sites"])
    }
