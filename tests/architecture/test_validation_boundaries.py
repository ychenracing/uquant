from __future__ import annotations

import ast
import copy
import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest

from uquant.contracts.strict_json import canonical_json_sha256

from ._analysis import (
    _VALIDATION_RELOCATED_FUNCTION_DEBT,
    _VALIDATION_RELOCATED_GLOBAL_DEBT,
    _VALIDATION_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS,
    architecture_snapshot,
)
from ._validation_inventory import (
    build_validation_inventory,
    current_reflection_contract,
)
from ._validation_reference_oracle import (
    candidate_behavior_from_subprocess,
    immutable_oracle_from_archive,
)
from ._validation_relocation import (
    GENERALIZATION_OWNERS,
    HOLDOUT_LANES_FACADE,
    HOLDOUT_OWNERS,
    HOLDOUT_RUNTIME_FACADE,
    POLICY_OWNERS,
    approved_relocations,
    assert_owner_ast_exact,
    build_relocation_contract,
    has_immutable_local_relocation_lineage,
)
from ._validation_transport import (
    validation_historical_debt_projection,
    validation_private_relocation_projection,
)

ROOT = Path(__file__).resolve().parents[2]
_VALIDATION_REFERENCE_COMMIT = "719288f6067686b3199d305899ddc09adf098a0d"
_VALIDATION_REFERENCE_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"
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
_HOLDOUT_OWNERS = (
    "uquant/validation/holdout/__init__.py",
    "uquant/validation/holdout/contract.py",
    "uquant/validation/holdout/source_identity.py",
    "uquant/validation/holdout/manifest.py",
    "uquant/validation/holdout/lanes.py",
    "uquant/validation/holdout/snapshots.py",
    "uquant/validation/holdout/replay.py",
    "uquant/validation/holdout/checkpoints.py",
    "uquant/validation/holdout/artifact_transaction.py",
    "uquant/validation/holdout/service.py",
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
    return build_validation_inventory(ROOT)


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
    assert payload["baseline_commit"] == _VALIDATION_REFERENCE_COMMIT
    assert payload["baseline_tree"] == _VALIDATION_REFERENCE_TREE
    assert payload["contract"] == "uquant-task9-validation-contract-oracle-v1"
    assert payload["success_sha256"] == canonical_json_sha256(payload["success"])
    assert payload["failure_order_sha256"] == canonical_json_sha256(
        payload["failure_order"]
    )
    unsigned = dict(payload)
    del unsigned["payload_sha256"]
    assert payload["payload_sha256"] == canonical_json_sha256(unsigned)


def test_validation_cleanup_inventory_precedes_every_legacy_replacement() -> None:
    payload = _immutable_inventory()
    assert payload["baseline_commit"] == _VALIDATION_REFERENCE_COMMIT
    assert payload["baseline_tree"] == _VALIDATION_REFERENCE_TREE
    assert payload["captured_while_all_implementations_intact"] == list(
        _LEGACY_IMPLEMENTATIONS
    )
    assert tuple(entry["path"] for entry in payload["entries"]) == (
        _LEGACY_IMPLEMENTATIONS
    )


def test_validation_cleanup_inventory_is_exactly_rebuilt_from_immutable_git() -> None:
    payload = _immutable_inventory()
    _assert_inventory_seal(payload)
    assert _rebuilt_inventory() == payload


def test_validation_cleanup_inventory_has_bidirectional_reference_partitions() -> None:
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


def _without_docstring_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_docstring_identity(item)
            for key, item in value.items()
            if key != "raw_docstring_sha256"
        }
    if isinstance(value, list):
        return [_without_docstring_identity(item) for item in value]
    return value


def test_validation_public_runtime_contract_matches_current_api_in_all_import_modes() -> None:
    current = current_reflection_contract(ROOT)
    expected = _without_docstring_identity(current["normal"])
    assert set(current["modes"]) == {
        "normal",
        "optimized",
        "double_optimized",
        "windows_no_fcntl",
    }
    for modules in current["modes"].values():
        assert _without_docstring_identity(modules) == expected


@pytest.mark.parametrize(
    ("entry_index", "field"),
    (
        (0, "ast_import_consumers"),
        (2, "immutable_fixed_path_consumers"),
        (5, "dotted_runtime_identity_consumers"),
    ),
)
def test_validation_resigned_inventory_omission_is_rejected(
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


def test_validation_resigned_immutable_blob_tamper_is_rejected() -> None:
    payload = copy.deepcopy(_immutable_inventory())
    entries = payload["entries"]
    assert isinstance(entries, list)
    entries[0]["content_sha256"] = "0" * 64
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    _assert_inventory_seal(payload)
    with pytest.raises(AssertionError):
        _assert_immutable_inventory(payload)


def test_validation_oracle_is_fresh_from_immutable_archive(
    tmp_path: Path,
) -> None:
    payload = _validation_oracle()
    immutable = immutable_oracle_from_archive(
        root=ROOT,
        destination=tmp_path / "snapshot",
        baseline_commit=_VALIDATION_REFERENCE_COMMIT,
        baseline_tree=_VALIDATION_REFERENCE_TREE,
        evidence_commit=_ORACLE_EVIDENCE_COMMIT,
        runner_blob=_ORACLE_RUNNER_BLOB,
        runner_sha256=_ORACLE_RUNNER_SHA256,
        source_identities=payload["source_identity"]["legacy_files"],
    )
    _assert_validation_oracle_seals(immutable)
    assert immutable == payload


def test_validation_candidate_behavior_matches_frozen_oracle_and_current_cli_identity() -> None:
    frozen = _validation_oracle()
    candidate = candidate_behavior_from_subprocess(
        root=ROOT,
        evidence_commit=_CANDIDATE_EVIDENCE_COMMIT,
        runner_blob=_CANDIDATE_RUNNER_BLOB,
        runner_sha256=_CANDIDATE_RUNNER_SHA256,
    )
    expected = {
        "success": copy.deepcopy(frozen["success"]),
        "failure_order": copy.deepcopy(frozen["failure_order"]),
    }
    current_help = candidate["success"]["sentinel"]["cli_help"]
    assert current_help["sha256"] == (
        "3ad102d2092e0d33194a62e80786687be7825e3ff34f1509a01bfd7d12529714"
    )
    assert current_help["text"] == json.loads(
        (ROOT / "benchmarks/public_api_contract.json").read_text(
            encoding="utf-8"
        )
    )["contract"]["cli_help"]["uquant-sentinel"]
    expected["success"]["sentinel"]["cli_help"] = current_help
    current_failure_note = candidate["failure_order"][27]["notes"][0]
    assert current_failure_note.startswith("stderr: usage: uquant-sentinel ")
    assert current_failure_note.endswith(
        "uquant-sentinel: error: --validate-contracts does not accept assessment arguments\n"
    )
    expected["failure_order"][27]["notes"][0] = current_failure_note
    assert candidate == expected


def test_validation_candidate_behavior_runner_tamper_is_rejected() -> None:
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
def test_validation_resigned_validation_oracle_tamper_is_rejected(
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


def test_validation_policy_relocation_is_closed_and_source_bound() -> None:
    assert approved_relocations(_immutable_inventory()) == {
        "uquant/validation/generalization.py": tuple(sorted(GENERALIZATION_OWNERS)),
        "uquant/validation/generalization_reference.py": tuple(sorted(POLICY_OWNERS)),
        "uquant/validation/holdout.py": tuple(
            sorted(
                (
                    HOLDOUT_OWNERS[0],
                    HOLDOUT_OWNERS[1],
                    HOLDOUT_OWNERS[2],
                    HOLDOUT_OWNERS[3],
                    HOLDOUT_OWNERS[9],
                )
            )
        ),
        HOLDOUT_RUNTIME_FACADE: tuple(
            sorted(
                (
                    HOLDOUT_RUNTIME_FACADE,
                    HOLDOUT_OWNERS[5],
                    HOLDOUT_OWNERS[6],
                    HOLDOUT_OWNERS[7],
                    HOLDOUT_OWNERS[8],
                    HOLDOUT_OWNERS[9],
                )
            )
        ),
        HOLDOUT_LANES_FACADE: tuple(
            sorted((HOLDOUT_LANES_FACADE, HOLDOUT_OWNERS[4]))
        ),
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


def test_validation_policy_resigned_relocation_tamper_is_rejected() -> None:
    payload = copy.deepcopy(_relocation_contract())
    payload["entries"][0]["owners"][0]["path"] = "uquant/validation/unapproved.py"
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    unsigned = dict(payload)
    claimed = unsigned.pop("canonical_sha256")
    assert claimed == canonical_json_sha256(unsigned)
    with pytest.raises(AssertionError):
        assert payload == _relocation_contract()


def test_validation_policy_owner_slices_are_immutable_ast_exact() -> None:
    assert_owner_ast_exact(ROOT)


@pytest.mark.parametrize("mutation", ("threshold", "comparison", "order"))
def test_validation_policy_ast_gate_rejects_rule_mutations(mutation: str) -> None:
    path = (
        "uquant/validation/generalization/gates.py"
        if mutation == "threshold"
        else "uquant/validation/generalization_policy/evaluation_stages.py"
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
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_policy_stages"
        )
        comparison = next(node for node in ast.walk(function) if isinstance(node, ast.Compare))
        comparison.ops[0] = ast.Eq() if isinstance(comparison.ops[0], ast.NotEq) else ast.NotEq()
    else:
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_policy_stages"
        )
        function.body[5], function.body[6] = function.body[6], function.body[5]
    with pytest.raises(AssertionError):
        assert_owner_ast_exact(ROOT, candidate_sources={path: ast.unparse(tree)})


def _validation_legacy_module(module: str) -> str:
    if module == "uquant.validation.generalization" or module.startswith(
        "uquant.validation.generalization."
    ):
        return "uquant.validation.generalization"
    if module == "uquant.validation.generalization_reference" or module.startswith(
        "uquant.validation.generalization_policy."
    ):
        return "uquant.validation.generalization_reference"
    if module in {
        "uquant.validation.holdout.contract",
        "uquant.validation.holdout.manifest",
        "uquant.validation.holdout.service",
        "uquant.validation.holdout.source_identity",
    }:
        return "uquant.validation.holdout"
    if module in {
        "uquant.validation.holdout.artifact_transaction",
        "uquant.validation.holdout.checkpoints",
        "uquant.validation.holdout.replay",
        "uquant.validation.holdout.snapshots",
    }:
        return "uquant.validation.holdout_runtime"
    if module == "uquant.validation.holdout.lanes":
        return "uquant.validation.holdout_lanes"
    return module


def test_validation_policy_relocated_debt_is_exact_bidirectional_and_non_growing() -> None:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    relocated = graph["task9_relocated_private_imports"]
    assert validation_private_relocation_projection(
        root=ROOT,
        observed={str(row["id"]) for row in relocated},
        expected=set(_VALIDATION_RELOCATED_PRIVATE_IMPORTS),
    ) == _VALIDATION_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if ".generalization." in str(row["importer"])
        or ".generalization_policy." in str(row["importer"])
        or ".generalization." in str(row["imported_from"])
        or ".generalization_policy." in str(row["imported_from"])
    }
    assert not (
        {str(row["id"]) for row in relocated}
        & {str(row["id"]) for row in graph["cross_module_private_imports"]}
    )
    immutable_entries = {
        str(entry["module"]): entry for entry in _immutable_inventory()["entries"]
    }
    immutable_symbols = {
        module: set(entry["defined_top_level_symbols"])
        for module, entry in immutable_entries.items()
    }
    for row in relocated:
        importer = _validation_legacy_module(str(row["importer"]))
        imported_from = _validation_legacy_module(str(row["imported_from"]))
        name = str(row["name"])
        if str(row["importer"]).startswith("uquant.validation.holdout"):
            importer_path = importer.replace(".", "/") + ".py"
            consumers = immutable_entries[imported_from]["live_references"][
                "cross_module_private_import_consumers"
            ]
            if not any(
                consumer["path"] == importer_path and name in consumer["symbols"]
                for consumer in consumers
            ):
                assert has_immutable_local_relocation_lineage(
                    ROOT,
                    importer_owner=str(row["importer"]),
                    imported_from_owner=str(row["imported_from"]),
                    name=name,
                )
        elif importer == imported_from:
            assert name in immutable_symbols[importer]
        else:
            if (
                importer,
                imported_from,
                name,
            ) != (
                "uquant.validation.generalization_reference",
                "uquant.validation.generalization_matrix",
                "_head_and_source",
            ):
                importer_path = importer.replace(".", "/") + ".py"
                consumers = immutable_entries[imported_from]["live_references"][
                    "cross_module_private_import_consumers"
                ]
                assert any(
                    consumer["path"] == importer_path and name in consumer["symbols"]
                    for consumer in consumers
                )

    functions = snapshot["functions"]
    candidate_function_debt = {
        str(row["id"])
        for row in functions
        if str(row["id"]).startswith(
            (
                "uquant.validation.generalization.",
                "uquant.validation.generalization_policy.",
                "uquant.validation.holdout.",
            )
        )
        and (
            int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
            or int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
        )
    }
    candidate_globals = {
        str(row["id"])
        for row in snapshot["module_globals"]
        if str(row["id"]).startswith(
            (
                "uquant.validation.generalization.",
                "uquant.validation.generalization_policy.",
                "uquant.validation.holdout.",
            )
        )
        and (bool(row["mutable_initializer"]) or bool(row["mutation_sites"]))
    }
    projected_functions, projected_globals = validation_historical_debt_projection(
        root=ROOT,
        current_functions=candidate_function_debt,
        historical_functions=set(_VALIDATION_RELOCATED_FUNCTION_DEBT),
        current_globals=candidate_globals,
        historical_globals=set(_VALIDATION_RELOCATED_GLOBAL_DEBT),
    )
    assert set(_VALIDATION_RELOCATED_FUNCTION_DEBT) == projected_functions
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
        legacy for legacy, _ in _VALIDATION_RELOCATED_FUNCTION_DEBT.values()
    } <= legacy_function_debt

    assert set(_VALIDATION_RELOCATED_GLOBAL_DEBT) == projected_globals
    legacy_global_debt = {
        str(row["id"]) for row in baseline_debt["mutable_module_globals"]
    }
    assert set(_VALIDATION_RELOCATED_GLOBAL_DEBT.values()) <= legacy_global_debt


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "uquant/validation/generalization_policy/schema.py",
            "schema_failures = _schema_failures",
            "schema_failures = _replay_error",
        ),
        (
            "uquant/validation/generalization_policy/evaluation_stages.py",
            "schema.schema_failures(",
            "schema.replay_error(",
        ),
    ),
)
def test_validation_architecture_public_policy_transport_rejects_unknown_identity_or_callee(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    snapshot = architecture_snapshot()
    relocated = snapshot["import_graph"]["task9_relocated_private_imports"]
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert original in source
    with pytest.raises(AssertionError):
        validation_private_relocation_projection(
            root=ROOT,
            observed={str(row["id"]) for row in relocated},
            expected=set(_VALIDATION_RELOCATED_PRIVATE_IMPORTS),
            overrides={relative: source.replace(original, mutation, 1)},
        )


def test_validation_policy_unknown_debt_is_not_hidden_by_relocation() -> None:
    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    path = "uquant/validation/generalization/baseline.py"
    source_texts[path] += (
        "\nfrom .models import _UNREVIEWED_VALIDATION_PRIVATE\n"
        "_UNREVIEWED_VALIDATION_MUTABLE = []\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    graph = mutation["import_graph"]
    assert (
        "uquant.validation.generalization.baseline:"
        "uquant.validation.generalization.models:_UNREVIEWED_VALIDATION_PRIVATE"
    ) in {str(row["id"]) for row in graph["cross_module_private_imports"]}
    assert "uquant.validation.generalization.baseline:_UNREVIEWED_VALIDATION_MUTABLE" in {
        str(row["id"])
        for row in mutation["module_globals"]
        if bool(row["mutable_initializer"]) or bool(row["mutation_sites"])
    }


def test_validation_holdout_local_holdout_lineage_fails_closed_without_definition_or_reference() -> None:
    legacy_path = "uquant/validation/holdout_runtime.py"
    immutable_source = subprocess.run(
        ["git", "show", f"{_VALIDATION_REFERENCE_TREE}:{legacy_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    arguments = {
        "importer_owner": "uquant.validation.holdout.service",
        "imported_from_owner": "uquant.validation.holdout.artifact_transaction",
        "name": "_artifact_bundle_lock",
    }
    assert has_immutable_local_relocation_lineage(ROOT, **arguments)
    missing_definition = immutable_source.replace(
        "def _artifact_bundle_lock(", "def artifact_bundle_lock(", 1
    )
    assert not has_immutable_local_relocation_lineage(
        ROOT,
        **arguments,
        legacy_sources={legacy_path: missing_definition},
    )
    missing_reference = immutable_source.replace(
        "with _artifact_bundle_lock(root, carriers):",
        "with artifact_bundle_lock(root, carriers):",
        1,
    )
    assert not has_immutable_local_relocation_lineage(
        ROOT,
        **arguments,
        legacy_sources={legacy_path: missing_reference},
    )


def test_validation_holdout_unknown_holdout_debt_is_not_hidden_by_relocation() -> None:
    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    path = "uquant/validation/holdout/service.py"
    source_texts[path] += (
        "\nfrom .contract import _UNREVIEWED_HOLDOUT_PRIVATE\n"
        "_UNREVIEWED_HOLDOUT_MUTABLE = []\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    graph = mutation["import_graph"]
    private_import = (
        "uquant.validation.holdout.service:"
        "uquant.validation.holdout.contract:_UNREVIEWED_HOLDOUT_PRIVATE"
    )
    assert private_import in {
        str(row["id"]) for row in graph["cross_module_private_imports"]
    }
    assert private_import not in {
        str(row["id"]) for row in graph["task9_relocated_private_imports"]
    }
    assert "uquant.validation.holdout.service:_UNREVIEWED_HOLDOUT_MUTABLE" in {
        str(row["id"])
        for row in mutation["module_globals"]
        if bool(row["mutable_initializer"]) or bool(row["mutation_sites"])
    }


def test_validation_holdout_has_real_holdout_owners_and_thin_facades() -> None:
    assert all((ROOT / path).is_file() for path in _HOLDOUT_OWNERS)
    assert not (ROOT / "uquant/validation/holdout.py").exists()
    assert len(
        (ROOT / "uquant/validation/holdout_runtime.py")
        .read_text(encoding="utf-8")
        .splitlines()
    ) < 180
    assert len(
        (ROOT / "uquant/validation/holdout_lanes.py")
        .read_text(encoding="utf-8")
        .splitlines()
    ) < 100


@pytest.mark.parametrize(
    ("owner", "mutation"),
    (
        ("uquant/validation/holdout/contract.py", "constant"),
        ("uquant/validation/holdout/lanes.py", "comparison"),
        ("uquant/validation/holdout/service.py", "service_order"),
        ("uquant/validation/holdout/snapshots.py", "checkpoint_handoff"),
    ),
)
def test_validation_holdout_ast_gate_rejects_holdout_rule_and_order_mutations(
    owner: str,
    mutation: str,
) -> None:
    tree = ast.parse((ROOT / owner).read_text(encoding="utf-8"))
    if mutation == "constant":
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "HOLDOUT_START"
        )
        assert isinstance(assignment.value, ast.Constant)
        assignment.value.value = "2026-08-07"
    elif mutation == "comparison":
        comparison = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.LtE)
        )
        comparison.ops[0] = ast.Lt()
    else:
        function_name = (
            "_generate_future_holdout_replay_locked"
            if mutation == "service_order"
            else "_validate_snapshot_predecessor"
        )
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        if mutation == "service_order":
            function.body[1], function.body[2] = function.body[2], function.body[1]
        else:
            index = next(
                index
                for index, node in enumerate(function.body)
                if isinstance(node, ast.If)
                and any(
                    isinstance(child, ast.Name)
                    and child.id == "prior_checkpoint"
                    for child in ast.walk(node)
                )
            )
            function.body[index - 1], function.body[index] = (
                function.body[index],
                function.body[index - 1],
            )
    with pytest.raises(AssertionError):
        assert_owner_ast_exact(ROOT, candidate_sources={owner: ast.unparse(tree)})


def test_validation_sentinel_fingerprint_has_one_cli_independent_owner() -> None:
    from uquant.risk_sentinel import cli, provenance, validation

    root = ROOT
    assert cli.sentinel_source_fingerprint(root) == provenance.legacy_sentinel_source_fingerprint(
        root
    )
    assert "legacy_sentinel_source_fingerprint" not in vars(cli)
    assert validation.sentinel_source_fingerprint is provenance.legacy_sentinel_source_fingerprint
    assert "legacy_sentinel_source_fingerprint" not in vars(validation)
    assert "uquant.risk_sentinel.cli" not in {
        module.__name__
        for module in validation.__dict__.values()
        if hasattr(module, "__name__")
    }
    provenance_tree = ast.parse(
        (ROOT / "uquant/risk_sentinel/provenance.py").read_text(encoding="utf-8")
    )
    assert not any(
        isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "cli"
        for node in ast.walk(provenance_tree)
    )


def test_validation_sentinel_current_sentinel_identity_calls_reviewed_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uquant.risk_sentinel import provenance

    calls: list[tuple[Path, str]] = []

    def _reviewed_surface(root: str | Path, surface_id: str) -> str:
        calls.append((Path(root), surface_id))
        return "current-sentinel-identity"

    monkeypatch.setattr(provenance, "source_surface_fingerprint", _reviewed_surface)

    assert provenance.current_sentinel_surface_fingerprint(ROOT) == "current-sentinel-identity"
    assert calls == [(ROOT, "sentinel_v1")]


def test_validation_sentinel_current_sentinel_identity_includes_provenance_member(
    tmp_path: Path,
) -> None:
    from uquant.provenance.surfaces import load_source_surface_registry
    from uquant.risk_sentinel.provenance import current_sentinel_surface_fingerprint

    registry = load_source_surface_registry(ROOT)
    registry_path = ROOT / "benchmarks/source_surface_registry.json"
    target_registry = tmp_path / "benchmarks/source_surface_registry.json"
    target_registry.parent.mkdir(parents=True)
    shutil.copyfile(registry_path, target_registry)
    for relative in registry.surface("sentinel_v1").paths:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    before = current_sentinel_surface_fingerprint(tmp_path)
    provenance_path = tmp_path / "uquant/risk_sentinel/provenance.py"
    provenance_path.write_bytes(provenance_path.read_bytes() + b"\n# mutation\n")

    assert current_sentinel_surface_fingerprint(tmp_path) != before


def test_validation_sentinel_facades_use_native_strict_mypy_without_broad_disables() -> None:
    for relative in ("uquant/cli.py", "uquant/validation/holdout_runtime.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "# mypy: disable-error-code" not in source
