from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import subprocess
from collections import Counter
from typing import Any, cast

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_sha256
from uquant.portfolio import PortfolioAllocator

from . import _task8_portfolio_trace as trace_module
from ._analysis import (
    _TASK8_RELOCATED_FUNCTION_DEBT,
    _TASK8_RELOCATED_PRIVATE_IMPORTS,
    _TASK8_RELOCATED_TYPE_IGNORES,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._task8_immutable_trace import assert_trace_seals, immutable_trace_from_archive
from ._task8_inventory import build_task8_inventory, current_reflection_contract

_TASK8_START = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_TASK8_START_TREE = "d3824f7c5d89521b8284b5de08cc1e82e3ab7ebd"
_TRACE_LOGIC_COMMIT = "3aadf021dce9ed77c2359065146e38209866789c"
_TRACE_LOGIC_BLOB = "cce9498d851d4007c57b2ba5eaa2e6f3216c444e"
_TRACE_RUNNER_SHA256 = "00672c67b31374c50e1e56e236a45609374637b86f9900d47dc550abe5b1f1c3"
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task8_cleanup_inventory.json"
_DAILY_TRACE = ROOT / "benchmarks" / "task8_daily_portfolio_trace.json"
_TRACE_RUNNER = ROOT / "tests" / "architecture" / "_task8_portfolio_trace.py"
_IMPLEMENTATION_IDENTITIES = {
    "uquant/portfolio.py": (
        "264a1a463b60929d6cefcb234eddbb2644cfdd93d1b3e1484a81a0dcde26a2d1",
        128958,
    ),
    "uquant/portfolio_leaders.py": (
        "64be28bc8fb707372d987eb529f60c9c2792b3c4bf60e3da724fa6bcd92fed77",
        57412,
    ),
    "uquant/portfolio_strategic.py": (
        "df416789de3f0ac9d3e88676ca5a00af00203d9578a153ef11011517c45dc1e8",
        60050,
    ),
    "uquant/portfolio_recovery.py": (
        "bb77595cf31fe435d9b42c9abe48918ae891f3dfec56f66cead7365d7d0a62d8",
        14156,
    ),
}
_FIXED_REFERENCE_COUNTS = {
    "uquant/portfolio.py": 17,
    "uquant/portfolio_leaders.py": 11,
    "uquant/portfolio_strategic.py": 12,
    "uquant/portfolio_recovery.py": 6,
}
_IMPORT_CONSUMER_COUNTS = {
    "uquant/portfolio.py": 11,
    "uquant/portfolio_leaders.py": 2,
    "uquant/portfolio_strategic.py": 1,
    "uquant/portfolio_recovery.py": 1,
}
_CHECKPOINT1_OWNER_METHODS = {
    "uquant/portfolio/allocator.py": ("_confirmed_recovery_gross", "allocate"),
    "uquant/portfolio/risk_reduction.py": (
        "_risk_attribution_mechanism",
        "_risk_retention_score",
        "_risk_retention_vector",
        "_risk_lifecycle_rank",
        "_subset_retention_vector",
        "_sparse_risk_reduce",
        "_risk_reduction_metadata",
        "_turnover_aware_sector_cap",
    ),
    "uquant/portfolio/freeze.py": (
        "_commit_frozen_exit_state",
        "_frozen_existing_targets",
    ),
    "uquant/portfolio/pipeline.py": ("_allocate_strategy",),
}
_CHECKPOINT1_PACKAGE_PATHS = (
    "uquant/portfolio/__init__.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/context.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/pipeline.py",
    "uquant/portfolio/risk_reduction.py",
)


def _git_source(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{_TASK8_START}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _assert_inventory_seals(payload: dict[str, Any]) -> None:
    assert payload["baseline_commit"] == _TASK8_START
    assert payload["baseline_tree"] == _TASK8_START_TREE
    assert payload["contract"] == "uquant-task8-pre-replacement-portfolio-inventory-v1"
    assert payload["canonical_sha256"] == canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "canonical_sha256"}
    )
    assert tuple(entry["path"] for entry in payload["entries"]) == tuple(
        _IMPLEMENTATION_IDENTITIES
    )


def _function_nodes(source: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def _immutable_allocator_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_git_source("uquant/portfolio.py"))
    allocator = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PortfolioAllocator"
    )
    return {node.name: node for node in allocator.body if isinstance(node, ast.FunctionDef)}


def _normalized_method(node: ast.FunctionDef) -> str:
    normalized = copy.deepcopy(node)
    normalized.decorator_list = []
    if normalized.args.args and normalized.args.args[0].arg == "self":
        normalized.args.args[0].annotation = None
    if (
        normalized.body
        and isinstance(normalized.body[0], ast.Expr)
        and isinstance(normalized.body[0].value, ast.Constant)
        and isinstance(normalized.body[0].value.value, str)
    ):
        normalized.body[0].value.value = inspect.cleandoc(normalized.body[0].value.value)
    return ast.dump(normalized, include_attributes=False)


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def immutable_task8_inventory() -> dict[str, Any]:
    return cast(dict[str, Any], build_task8_inventory(ROOT))


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def immutable_task8_trace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    return cast(
        dict[str, object],
        immutable_trace_from_archive(
            root=ROOT,
            destination=tmp_path_factory.mktemp("task8-immutable-trace") / "snapshot",
            baseline_commit=_TASK8_START,
            implementation_identities=_IMPLEMENTATION_IDENTITIES,
            runner=_TRACE_RUNNER,
            runner_sha256=_TRACE_RUNNER_SHA256,
            logic_commit=_TRACE_LOGIC_COMMIT,
            logic_blob=_TRACE_LOGIC_BLOB,
        ),
    )


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def candidate_task8_traces() -> tuple[list[dict[str, Any]], Counter[str]]:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    original = trace_module._event_payload

    def counted_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
        counts[str(args[0])] += 1
        return cast(dict[str, Any], original(*args, **kwargs))

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(trace_module, "_event_payload", counted_event)
        traces = [
            trace_module.portfolio_trace_replay(
                name=expected["name"],
                start=expected["requested_start"],
                end=expected["requested_end"],
                symbols=tuple(expected["symbols"]),
                root=ROOT,
            )
            for expected in payload["scenarios"]
        ]
    return traces, counts


def test_task8_cleanup_inventory_is_exactly_derived_before_replacement(
    immutable_task8_inventory: dict[str, Any],
) -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    _assert_inventory_seals(payload)
    assert payload == immutable_task8_inventory
    for entry in payload["entries"]:
        path = entry["path"]
        source = _git_source(path)
        expected_sha256, expected_size = _IMPLEMENTATION_IDENTITIES[path]
        assert hashlib.sha256(source).hexdigest() == expected_sha256
        assert len(source) == expected_size
        live = entry["live_references"]
        assert len(live["immutable_fixed_path_consumers"]) == _FIXED_REFERENCE_COUNTS[path]
        assert len(live["ast_import_consumers"]) == _IMPORT_CONSUMER_COUNTS[path]
        classified = set().union(
            live["current_executable_consumers"],
            live["historical_machine_evidence_to_preserve"],
            live["documentation_references"],
            live["other_current_or_contract_consumers"],
        )
        assert classified == set(live["immutable_fixed_path_consumers"])


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "mutation", ("omit_consumer", "change_blob")
)
def test_task8_resigned_inventory_mutations_are_rejected(
    immutable_task8_inventory: dict[str, Any], mutation: str
) -> None:
    payload = copy.deepcopy(immutable_task8_inventory)
    if mutation == "omit_consumer":
        payload["entries"][0]["live_references"]["immutable_fixed_path_consumers"].pop()
    else:
        payload["entries"][0]["git_blob_sha1"] = "0" * 40
    payload["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "canonical_sha256"}
    )
    _assert_inventory_seals(payload)
    with pytest.raises(AssertionError):
        assert payload == immutable_task8_inventory


def test_task8_public_mro_pickle_reflection_and_import_modes_are_exact() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    expected = {
        "normal": {
            "portfolio_all": payload["portfolio_public_contract"]["runtime"]["all"],
            "classes": {
                entry["class_name"]: entry["reflection_pickle_mro_contract"] for entry in payload["entries"]
            },
            "functions": payload["portfolio_public_contract"]["runtime"]["functions"],
        },
        "mode_sha256": payload["portfolio_public_contract"]["runtime"]["import_mode_sha256"],
    }
    assert current_reflection_contract(ROOT) == expected
    assert expected["normal"]["classes"]["PortfolioAllocator"]["mro"] == [
        "uquant.portfolio.PortfolioAllocator",
        "uquant.portfolio_recovery.RecoveryPortfolioPolicy",
        "uquant.portfolio_leaders.LeaderPortfolioPolicy",
        "uquant.portfolio_strategic.StrategicPortfolioPolicy",
        "uquant.portfolio_core.PortfolioCore",
        "builtins.object",
    ]


def test_task8_historical_class_and_instance_monkeypatch_seams_remain_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    def class_seam(*args: object, **kwargs: object) -> tuple[()]:
        return ()

    monkeypatch.setattr(PortfolioAllocator, "_allocate_strategy", class_seam)
    assert allocator._allocate_strategy.__func__ is class_seam

    def instance_seam(*args: object, **kwargs: object) -> tuple[()]:
        return ()

    monkeypatch.setattr(allocator, "_allocate_strategy", instance_seam)
    assert allocator._allocate_strategy is instance_seam


def test_task8_historical_machine_evidence_and_requirements_remain_bytes_exact() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    historical = {
        path
        for entry in payload["entries"]
        for path in entry["live_references"]["historical_machine_evidence_to_preserve"]
    }
    paths = historical | {
        "requirements.txt",
        "benchmarks/architecture_refactor_public_api.json",
    }
    for path in paths:
        assert (ROOT / path).read_bytes() == _git_source(path)


def test_task8_daily_allocation_oracle_is_fresh_immutable_and_exact(
    immutable_task8_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    assert_trace_seals(
        payload,
        baseline_commit=_TASK8_START,
        baseline_tree=_TASK8_START_TREE,
        checkpoint_names=trace_module._CHECKPOINT_NAMES,
    )
    assert payload == immutable_task8_trace


def test_task8_resigned_trace_tamper_is_rejected(
    immutable_task8_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    first = payload["scenarios"][0]["records"][0]
    first["checkpoint_sha256"][0]["sha256"] = "0" * 64
    first["ordered_checkpoint_sha256"] = "1" * 64
    payload["scenarios"][0]["records_sha256"] = canonical_json_sha256(payload["scenarios"][0]["records"])
    payload["payload_sha256"] = canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "payload_sha256"}
    )
    assert_trace_seals(
        payload,
        baseline_commit=_TASK8_START,
        baseline_tree=_TASK8_START_TREE,
        checkpoint_names=trace_module._CHECKPOINT_NAMES,
    )
    with pytest.raises(AssertionError):
        assert payload == immutable_task8_trace


@pytest.mark.parametrize("scenario_index", range(3))  # type: ignore[untyped-decorator]
def test_task8_candidate_daily_nine_checkpoint_trace_is_exact(
    candidate_task8_traces: tuple[list[dict[str, Any]], Counter[str]],
    scenario_index: int,
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    observed, _ = candidate_task8_traces
    assert observed[scenario_index] == payload["scenarios"][scenario_index]


def test_task8_oracle_owner_event_coverage_is_nonempty_and_explicit(
    candidate_task8_traces: tuple[list[dict[str, Any]], Counter[str]],
) -> None:
    _, counts = candidate_task8_traces
    assert counts["_allocate_strategy"] == 60
    assert counts["_strategic_cohort_targets"] == 40
    assert counts["_recovery_anchor_substitution"] == 45
    assert counts["_frozen_existing_targets"] == 21
    assert counts["_sparse_risk_reduce"] == 1
    assert counts["_update_leader_cycle_arm"] == 25
    # The three windows do not reach leader target construction; the focused
    # leader branch fixtures remain mandatory at checkpoint 2.
    assert counts["_leader_targets"] == 0


def test_task8_checkpoint1_real_package_owners_replace_only_portfolio_monolith() -> None:
    assert not (ROOT / "uquant/portfolio.py").exists()
    assert all((ROOT / path).is_file() for path in _CHECKPOINT1_PACKAGE_PATHS)
    assert (ROOT / "uquant/portfolio_leaders.py").is_file()
    assert (ROOT / "uquant/portfolio_strategic.py").is_file()
    assert (ROOT / "uquant/portfolio_recovery.py").is_file()


def test_task8_checkpoint1_moved_methods_are_immutable_ast_exact() -> None:
    immutable = _immutable_allocator_methods()
    observed: set[str] = set()
    for path, names in _CHECKPOINT1_OWNER_METHODS.items():
        candidate = _function_nodes((ROOT / path).read_text(encoding="utf-8"))
        for name in names:
            observed.add(name)
            assert _normalized_method(candidate[name]) == _normalized_method(immutable[name])
    assert observed == set(immutable)


def test_task8_checkpoint1_ast_gate_rejects_threshold_compare_and_call_order_mutations() -> None:
    immutable = _immutable_allocator_methods()
    threshold = copy.deepcopy(immutable["_confirmed_recovery_gross"])
    numeric = next(
        node
        for node in ast.walk(threshold)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    )
    numeric.value = float(numeric.value) + 0.01
    assert _normalized_method(threshold) != _normalized_method(immutable["_confirmed_recovery_gross"])

    comparison = copy.deepcopy(immutable["allocate"])
    compare = next(node for node in ast.walk(comparison) if isinstance(node, ast.Compare))
    compare.ops[0] = ast.Gt() if not isinstance(compare.ops[0], ast.Gt) else ast.Lt()
    assert _normalized_method(comparison) != _normalized_method(immutable["allocate"])

    orchestration = copy.deepcopy(immutable["_allocate_strategy"])
    body_index = next(
        index
        for index, statement in enumerate(orchestration.body[:-1])
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr))
        and isinstance(orchestration.body[index + 1], (ast.Assign, ast.AnnAssign, ast.Expr))
    )
    orchestration.body[body_index], orchestration.body[body_index + 1] = (
        orchestration.body[body_index + 1],
        orchestration.body[body_index],
    )
    assert _normalized_method(orchestration) != _normalized_method(immutable["_allocate_strategy"])


def test_task8_checkpoint1_source_surface_migration_is_exact() -> None:
    immutable = json.loads(_git_source("benchmarks/source_surface_registry.json"))
    candidate = json.loads(
        (ROOT / "benchmarks/source_surface_registry.json").read_text(encoding="utf-8")
    )
    assert candidate["canonical_sha256"] == canonical_json_sha256(
        {key: value for key, value in candidate.items() if key != "canonical_sha256"}
    )
    immutable_surfaces = {surface["id"]: surface for surface in immutable["surfaces"]}
    candidate_surfaces = {surface["id"]: surface for surface in candidate["surfaces"]}
    assert tuple(candidate_surfaces) == tuple(immutable_surfaces)
    for identifier, baseline in immutable_surfaces.items():
        expected = set(baseline["source_paths"])
        if "uquant/portfolio.py" in expected:
            expected.remove("uquant/portfolio.py")
            expected.update(_CHECKPOINT1_PACKAGE_PATHS)
        assert set(candidate_surfaces[identifier]["source_paths"]) == expected
        assert candidate_surfaces[identifier]["resource_paths"] == baseline["resource_paths"]
        assert {
            key: value
            for key, value in candidate_surfaces[identifier].items()
            if key not in {"source_paths", "resource_paths"}
        } == {
            key: value
            for key, value in baseline.items()
            if key not in {"source_paths", "resource_paths"}
        }


def test_task8_checkpoint1_has_one_allocator_and_sparse_reducer_without_reverse_imports() -> None:
    package_sources = {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "uquant/portfolio").glob("*.py"))
    }
    classes = [
        node
        for tree in package_sources.values()
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PortfolioAllocator"
    ]
    reducers = [
        node
        for tree in package_sources.values()
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_sparse_risk_reduce"
    ]
    assert len(classes) == 1
    assert len(reducers) == 1
    forbidden = {
        "fcntl",
        "research",
        "scripts",
        "tests",
        "uquant.account",
        "uquant.application",
        "uquant.engine",
        "uquant.execution",
        "uquant.validation",
    }
    for path, tree in package_sources.items():
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        }
        assert not {
            name
            for name in imports
            for blocked in forbidden
            if name == blocked or name.startswith(f"{blocked}.")
        }, path


def test_task8_checkpoint1_private_and_complexity_relocations_are_exact_and_closed() -> None:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    relocated = graph["task8_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    assert {str(row["id"]) for row in relocated} == _TASK8_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in ordinary
        if str(row["importer"]).startswith("uquant.portfolio.")
        or str(row["imported_from"]).startswith("uquant.portfolio.")
    }
    assert set(_TASK8_RELOCATED_FUNCTION_DEBT) == {
        f"{path.removesuffix('.py').replace('/', '.')}:{name}"
        for path, names in _CHECKPOINT1_OWNER_METHODS.items()
        for name in names
    }
    assert set(_TASK8_RELOCATED_FUNCTION_DEBT.values()) == {
        f"uquant.portfolio:PortfolioAllocator.{name}"
        for names in _CHECKPOINT1_OWNER_METHODS.values()
        for name in names
    }
    observed_type_ignores = {
        str(row["id"])
        for row in snapshot["type_ignores"]
        if str(row["path"]).startswith("uquant/portfolio/")
    }
    assert observed_type_ignores == set(_TASK8_RELOCATED_TYPE_IGNORES)

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/portfolio/allocator.py"] += (
        "\nfrom .freeze import _unreviewed_task8_edge\n\n"
        "def _unreviewed_task8_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert "uquant.portfolio.allocator:uquant.portfolio.freeze:_unreviewed_task8_edge" in {
        str(row["id"]) for row in mutation_graph["cross_module_private_imports"]
    }
    mutation_debt = measured_debt(mutation)
    assert "uquant.portfolio:_unreviewed_task8_debt" in {
        str(row["id"]) for row in mutation_debt["long_functions"]
    }
