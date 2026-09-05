from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import inspect
import io
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_bytes, canonical_json_sha256
from uquant.provenance.fingerprints import source_surface_fingerprint

from ._analysis import (
    _EXECUTION_RELOCATED_FUNCTION_DEBT,
    _EXECUTION_RELOCATED_GLOBAL_DEBT,
    _EXECUTION_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._execution_application_transport import (
    ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS,
    ARCHITECTURE_EXECUTION_REVIEWED_DEFINITIONS,
    architecture_execution_decision_fanout,
    architecture_execution_historical_debt_projection,
    reviewed_execution_debt_definition,
    validate_engine_descriptor_transport,
)
from ._owner_transport import (
    architecture_resource_surface_projection,
    architecture_source_surface_projection,
)
from ._validation_relocation import (
    GENERALIZATION_OWNERS,
    HOLDOUT_LANES_FACADE,
    HOLDOUT_OWNERS,
    HOLDOUT_RUNTIME_FACADE,
    POLICY_OWNERS,
)

_EXECUTION_REFERENCE_COMMIT = "908399a80f27a028c35f201b9bf5f1688eb412c0"
_EXECUTION_REFERENCE_TREE = "8fd744507922e3d143923939e7d5e75f9148afc1"
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task6_cleanup_inventory.json"

_EXECUTION_PACKAGE_PATHS = {
    "uquant/execution/__init__.py",
    "uquant/execution/fees.py",
    "uquant/execution/market_constraints.py",
    "uquant/execution/open_execution.py",
    "uquant/execution/order_planning.py",
    "uquant/execution/pending.py",
    "uquant/execution/reconciliation.py",
    "uquant/execution/tranches.py",
}
_APPLICATION_PACKAGE_PATHS = {
    "uquant/application/__init__.py",
    "uquant/application/backtest.py",
    "uquant/application/decision.py",
    "uquant/application/metrics.py",
    "uquant/application/risk_timeline_cache.py",
}
_FINGERPRINT_RUNTIME_CLOSURE_PATHS = {
    "uquant/contracts/source_surfaces.py",
    "uquant/contracts/strict_json.py",
    "uquant/infrastructure/git_source.py",
    "uquant/provenance/fingerprints.py",
    "uquant/provenance/surfaces.py",
}
_RISK_RISK_PACKAGE_PATHS = {
    "uquant/risk/__init__.py",
    "uquant/risk/anchors.py",
    "uquant/risk/assessment.py",
    "uquant/risk/capital.py",
    "uquant/risk/recovery_state.py",
    "uquant/risk/strategic_guard.py",
    "uquant/risk/transitions.py",
}
_PORTFOLIO_PORTFOLIO_PACKAGE_PATHS = {
    "uquant/portfolio/__init__.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/context.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/leaders/__init__.py",
    "uquant/portfolio/leaders/admission.py",
    "uquant/portfolio/leaders/lifecycle.py",
    "uquant/portfolio/leaders/targets.py",
    "uquant/portfolio/pipeline.py",
    "uquant/portfolio/recovery/__init__.py",
    "uquant/portfolio/recovery/admission.py",
    "uquant/portfolio/recovery/substitution.py",
    "uquant/portfolio/recovery/targets.py",
    "uquant/portfolio/risk_reduction.py",
    "uquant/portfolio/strategic/__init__.py",
    "uquant/portfolio/strategic/discovery.py",
    "uquant/portfolio/strategic/lifecycle.py",
    "uquant/portfolio/strategic/targets.py",
}

_EXECUTION_OWNERS = {
    "fee_components": "uquant/execution/fees.py",
    "_limit_rate": "uquant/execution/market_constraints.py",
    "_blocked": "uquant/execution/market_constraints.py",
    "plan_orders": "uquant/execution/order_planning.py",
    "merge_pending_orders": "uquant/execution/pending.py",
    "_register_account_order": "uquant/execution/reconciliation.py",
    "_active_order_status": "uquant/execution/reconciliation.py",
    "_reconcile_account_orders_mutating": "uquant/execution/reconciliation.py",
    "_preflight_reconciliation_batch": "uquant/execution/reconciliation.py",
    "reconcile_account_orders": "uquant/execution/reconciliation.py",
    "risk_priority_tranche_key": "uquant/execution/tranches.py",
    "_sell_tranches": "uquant/execution/tranches.py",
    "_consume_sell_tranches": "uquant/execution/tranches.py",
    "_allocate_sell_costs": "uquant/execution/tranches.py",
    "_rebuild_position_from_tranches": "uquant/execution/tranches.py",
    "ExecutionPlanner": "uquant/execution/open_execution.py",
}

_ENGINE_APPLICATION_OWNERS = {
    "_canonical_json": "uquant/application/risk_timeline_cache.py",
    "_risk_timeline_disk_path": "uquant/application/risk_timeline_cache.py",
    "_load_risk_timeline_disk_cache": "uquant/application/risk_timeline_cache.py",
    "_write_risk_timeline_disk_cache": "uquant/application/risk_timeline_cache.py",
    "_causal_risk_timeline": "uquant/application/risk_timeline_cache.py",
    "_decision_config_for_universe": "uquant/application/decision.py",
    "_attach_target_attribution": "uquant/application/decision.py",
    "_mark_account_positions": "uquant/application/decision.py",
    "decide": "uquant/application/decision.py",
    "deterministic_decision": "uquant/application/decision.py",
    "equity": "uquant/application/backtest.py",
    "backtest": "uquant/application/backtest.py",
    "_drawdown_stats": "uquant/application/metrics.py",
    "performance_metrics": "uquant/application/metrics.py",
}

_ENGINE_COMPATIBILITY_METHODS = (
    "__setattr__",
    "__init__",
    "_load",
    "_price",
    "_reference_returns",
)

_ALLOWED_DELEGATION_PARAMETERS = {
    "_load_risk_timeline_disk_cache": ("cache_schema",),
    "_write_risk_timeline_disk_cache": ("cache_schema",),
    "_attach_target_attribution": (
        "legacy_industry",
        "legacy_manifest_sha256",
    ),
    "_causal_risk_timeline": (
        "timeline_builder",
        "native_timeline_builder",
        "code_fingerprint_fn",
        "shared_timeline_cache",
        "load_disk_cache_fn",
        "write_disk_cache_fn",
    ),
    "decide": (
        "assess_risk_fn",
        "evaluate_sentinel_fn",
        "reconcile_account_orders_fn",
        "code_fingerprint_fn",
        "attach_target_attribution_fn",
    ),
    "backtest": ("performance_metrics_fn",),
}

_DELEGATION_NAME_NORMALIZATION = {
    "timeline_builder": "build_risk_evidence_timeline",
    "native_timeline_builder": "_RISK_TIMELINE_BUILDER",
    "code_fingerprint_fn": "code_fingerprint",
    "shared_timeline_cache": "_SHARED_RISK_TIMELINE_CACHE",
    "load_disk_cache_fn": "_load_risk_timeline_disk_cache",
    "write_disk_cache_fn": "_write_risk_timeline_disk_cache",
    "cache_schema": "_RISK_TIMELINE_CACHE_SCHEMA",
    "legacy_industry": "_LEGACY_INDUSTRY",
    "legacy_manifest_sha256": "_LEGACY_MANIFEST_SHA256",
    "assess_risk_fn": "assess_risk",
    "evaluate_sentinel_fn": "evaluate_sentinel",
    "reconcile_account_orders_fn": "reconcile_account_orders",
    "attach_target_attribution_fn": "_attach_target_attribution",
    "performance_metrics_fn": "performance_metrics",
}


def _git_source(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{_EXECUTION_REFERENCE_TREE}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _immutable_python_sources() -> dict[str, bytes]:
    paths = [
        path
        for path in subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", _EXECUTION_REFERENCE_TREE],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if path.endswith(".py")
    ]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input="".join(f"{_EXECUTION_REFERENCE_TREE}:{path}\n" for path in paths).encode(),
        check=True,
        capture_output=True,
    ).stdout
    stream = io.BytesIO(batch)
    sources: dict[str, bytes] = {}
    for path in paths:
        header = stream.readline().decode("ascii").split()
        assert len(header) == 3 and header[1] == "blob"
        size = int(header[2])
        sources[path] = stream.read(size)
        assert stream.read(1) == b"\n"
    assert not stream.read()
    return sources


def _immutable_module(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolved_import_from(path: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module = _immutable_module(path)
    package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - (node.level - 1))]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _immutable_import_consumers(
    sources: dict[str, bytes],
    target: str,
) -> list[dict[str, object]]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[dict[str, object]] = []
    for path, source in sources.items():
        symbols: list[str] = []
        seen: set[str] = set()
        tree = ast.parse(source, filename=path)
        imports = sorted(
            (node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in imports:
            if isinstance(node, ast.Import):
                candidates = (f"{leaf} module" for alias in node.names if alias.name == target)
            else:
                imported_from = _resolved_import_from(path, node)
                candidates = (
                    alias.name if imported_from == target else f"{leaf} module"
                    for alias in node.names
                    if imported_from == target or (imported_from == parent and alias.name == leaf)
                )
            for symbol in candidates:
                if symbol not in seen:
                    seen.add(symbol)
                    symbols.append(symbol)
        if symbols:
            consumers.append({"path": path, "symbols": symbols})
    return consumers


def _immutable_module_attribute_consumers(
    sources: dict[str, bytes],
    target: str,
) -> list[str]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[str] = []
    for path, source in sources.items():
        tree = ast.parse(source, filename=path)
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update(
                    alias.asname for alias in node.names if alias.name == target and alias.asname is not None
                )
            elif isinstance(node, ast.ImportFrom) and _resolved_import_from(path, node) == parent:
                aliases.update(alias.asname or alias.name for alias in node.names if alias.name == leaf)
        if aliases and any(
            isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases
            for node in ast.walk(tree)
        ):
            consumers.append(path)
    return consumers


def _immutable_dotted_runtime_identity_consumers(
    sources: dict[str, bytes],
    target: str,
) -> list[dict[str, object]]:
    prefix = f"{target}."
    consumers: list[dict[str, object]] = []
    for path, source in sources.items():
        values = sorted(
            {
                node.value
                for node in ast.walk(ast.parse(source, filename=path))
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(prefix)
            }
        )
        if values:
            consumers.append({"path": path, "values": values})
    return consumers


def _top_level_definitions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def _engine_methods(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    engine = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ProductionEngine"
    )
    return {node.name: node for node in engine.body if isinstance(node, ast.FunctionDef)}


class _NormalizeDelegationNames(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.AST:
        replacement = _DELEGATION_NAME_NORMALIZATION.get(node.id)
        return ast.copy_location(ast.Name(id=replacement, ctx=node.ctx), node) if replacement else node


def _normalized_docstring_indentation(
    node: ast.FunctionDef | ast.ClassDef,
) -> ast.FunctionDef | ast.ClassDef:
    normalized = copy.deepcopy(node)
    if (
        normalized.body
        and isinstance(normalized.body[0], ast.Expr)
        and isinstance(normalized.body[0].value, ast.Constant)
        and isinstance(normalized.body[0].value.value, str)
    ):
        normalized.body[0].value.value = inspect.cleandoc(normalized.body[0].value.value)
    return normalized


def _normalized_application_definition(
    node: ast.FunctionDef | ast.ClassDef,
) -> ast.FunctionDef | ast.ClassDef:
    normalized = _normalized_docstring_indentation(node)
    allowed = _ALLOWED_DELEGATION_PARAMETERS.get(node.name, ())
    if isinstance(normalized, ast.FunctionDef):
        positional = normalized.args.args
        if positional and positional[0].arg == "self":
            positional[0].annotation = None
        observed = tuple(argument.arg for argument in positional[-len(allowed) :]) if allowed else ()
        assert observed == allowed
        if allowed:
            del positional[-len(allowed) :]
    normalized = _NormalizeDelegationNames().visit(normalized)
    ast.fix_missing_locations(normalized)
    return normalized


def test_execution_inventory_is_bound_to_immutable_blobs_and_reference_sets() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    immutable_sources = _immutable_python_sources()
    immutable_registry = json.loads(
        subprocess.run(
            [
                "git",
                "show",
                f"{_EXECUTION_REFERENCE_TREE}:benchmarks/source_surface_registry.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
    )
    assert payload["baseline_commit"] == _EXECUTION_REFERENCE_COMMIT
    assert {entry["path"] for entry in payload["entries"]} == {
        "uquant/execution.py",
        "uquant/engine.py",
    }
    for entry in payload["entries"]:
        source = subprocess.run(
            ["git", "cat-file", "blob", entry["git_blob_sha1"]],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert len(source) == entry["size_bytes"]
        assert hashlib.sha256(source).hexdigest() == entry["content_sha256"]
        references = subprocess.run(
            ["git", "grep", "-l", "--fixed-strings", entry["path"], _EXECUTION_REFERENCE_TREE, "--", "."],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        observed = {line.split(":", 1)[1] for line in references}
        assert observed == set(entry["live_references"]["immutable_fixed_path_consumers"])
        target = entry["path"].removesuffix(".py").replace("/", ".")
        assert (
            _immutable_import_consumers(immutable_sources, target)
            == entry["live_references"]["ast_import_consumers"]
        )
        expected_surfaces = [
            surface["id"]
            for surface in immutable_registry["surfaces"]
            if entry["path"] in surface["source_paths"]
        ]
        assert expected_surfaces == entry["live_references"]["current_source_surface_registry"]
        dotted_consumers = _immutable_dotted_runtime_identity_consumers(immutable_sources, target)
        if entry["path"] == "uquant/engine.py":
            assert dotted_consumers == entry["live_references"]["dotted_runtime_identity_consumers"]
            assert (
                _immutable_module_attribute_consumers(immutable_sources, target)
                == entry["live_references"]["runtime_monkeypatch_and_cache_consumers"]
            )
        else:
            assert dotted_consumers == []
            assert "dotted_runtime_identity_consumers" not in entry["live_references"]


def test_execution_source_surface_migration_is_exact_for_all_five_v1_surfaces() -> None:
    immutable_registry = json.loads(_git_source("benchmarks/source_surface_registry.json"))
    candidate_registry = json.loads(
        (ROOT / "benchmarks/source_surface_registry.json").read_text(encoding="utf-8")
    )
    immutable = {surface["id"]: surface for surface in immutable_registry["surfaces"]}
    candidate = {surface["id"]: surface for surface in candidate_registry["surfaces"]}
    identifiers = (
        "economic_decision_v1",
        "execution_account_v1",
        "sentinel_v1",
        "validation_runner_v1",
        "full_package_v1",
    )
    assert tuple(candidate) == identifiers
    additions = {
        "economic_decision_v1": (
            _EXECUTION_PACKAGE_PATHS | _APPLICATION_PACKAGE_PATHS | _FINGERPRINT_RUNTIME_CLOSURE_PATHS
        ),
        "execution_account_v1": _EXECUTION_PACKAGE_PATHS,
        "sentinel_v1": set(),
        "validation_runner_v1": set(),
        "full_package_v1": _EXECUTION_PACKAGE_PATHS | _APPLICATION_PACKAGE_PATHS,
    }
    for identifier in identifiers:
        expected_sources = set(immutable[identifier]["source_paths"])
        expected_sources.discard("uquant/execution.py")
        expected_sources.update(additions[identifier])
        if "uquant/risk.py" in expected_sources:
            expected_sources.remove("uquant/risk.py")
            expected_sources.update(_RISK_RISK_PACKAGE_PATHS)
        if "uquant/portfolio.py" in expected_sources:
            expected_sources.remove("uquant/portfolio.py")
            expected_sources.update(_PORTFOLIO_PORTFOLIO_PACKAGE_PATHS)
        if "uquant/validation/generalization.py" in expected_sources:
            expected_sources.remove("uquant/validation/generalization.py")
            expected_sources.update(GENERALIZATION_OWNERS)
        if "uquant/validation/generalization_reference.py" in expected_sources:
            expected_sources.update(POLICY_OWNERS)
        if "uquant/validation/holdout.py" in expected_sources:
            expected_sources.remove("uquant/validation/holdout.py")
            expected_sources.update(HOLDOUT_OWNERS)
        if HOLDOUT_RUNTIME_FACADE in expected_sources:
            expected_sources.update(HOLDOUT_OWNERS[5:])
        if HOLDOUT_LANES_FACADE in expected_sources:
            expected_sources.add(HOLDOUT_OWNERS[4])
        if {
            "uquant/risk_sentinel/cli.py",
            "uquant/risk_sentinel/validation.py",
        } & expected_sources:
            expected_sources.add("uquant/risk_sentinel/provenance.py")
        expected_sources = architecture_source_surface_projection(
            identifier,
            expected_sources,
        )
        assert set(candidate[identifier]["source_paths"]) == expected_sources
        assert candidate[identifier]["resource_paths"] == (
            architecture_resource_surface_projection(
                identifier,
                immutable[identifier]["resource_paths"]
            )
        )

    assert (ROOT / "requirements.txt").read_bytes() == _git_source("requirements.txt")


def test_execution_responsibility_owners_replace_execution_monolith_and_engine_bodies() -> None:
    assert not (ROOT / "uquant/execution.py").exists()
    expected = {
        "uquant/execution/__init__.py",
        "uquant/execution/fees.py",
        "uquant/execution/market_constraints.py",
        "uquant/execution/order_planning.py",
        "uquant/execution/pending.py",
        "uquant/execution/reconciliation.py",
        "uquant/execution/tranches.py",
        "uquant/execution/open_execution.py",
        "uquant/application/__init__.py",
        "uquant/application/decision.py",
        "uquant/application/backtest.py",
        "uquant/application/metrics.py",
        "uquant/application/risk_timeline_cache.py",
    }
    assert all((ROOT / relative).is_file() for relative in expected)


def test_execution_facade_and_decision_fanout_are_bounded() -> None:
    engine_lines = len((ROOT / "uquant/engine.py").read_text(encoding="utf-8").splitlines())
    snapshot = architecture_snapshot()
    modules = snapshot["modules"]
    assert isinstance(modules, dict)
    assert engine_lines < 120
    assert modules["uquant.engine"]["fan_out_count"] <= 4
    decision_fan_out = modules["uquant.application.decision"]["fan_out"]
    target_fan_out = modules["uquant.application.target_attribution"]["fan_out"]
    assert isinstance(decision_fan_out, list)
    assert isinstance(target_fan_out, list)
    assert architecture_execution_decision_fanout(
        root=ROOT,
        decision_fan_out={str(value) for value in decision_fan_out},
        extracted_owner_fan_out={str(value) for value in target_fan_out},
    ) <= 13


def test_execution_private_edges_are_exactly_bound_to_the_mechanical_split() -> None:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, dict)
    relocated = graph["task6_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    private_module_calls = graph["cross_module_private_module_calls"]
    assert isinstance(relocated, list)
    assert isinstance(ordinary, list)
    assert isinstance(private_module_calls, list)
    assert {str(row["id"]) for row in relocated} == _EXECUTION_RELOCATED_PRIVATE_IMPORTS

    execution_prefixes = ("uquant.application", "uquant.engine", "uquant.execution")
    baseline = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(encoding="utf-8")
    )
    allowed = set(baseline["architecture_debt"]["temporary_allowlist"]["cross_module_private_imports"])
    assert (
        not {
            str(row["id"])
            for row in ordinary
            if str(row["importer"]).startswith(execution_prefixes)
            or str(row["imported_from"]).startswith(execution_prefixes)
        }
        - allowed
    )
    assert not {
        str(row["id"])
        for row in private_module_calls
        if str(row["importer"]).startswith(execution_prefixes)
        or str(row["imported_from"]).startswith(execution_prefixes)
    }


def test_execution_complexity_debt_relocations_are_exact_and_do_not_create_an_exemption() -> None:
    snapshot = architecture_snapshot()
    functions = snapshot["functions"]
    assert isinstance(functions, list)
    observed = {
        str(row["id"])
        for row in functions
        if isinstance(row, dict)
        and str(row["module"]).startswith(("uquant.application", "uquant.execution"))
        and (
            int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
            or int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
        )
    }
    baseline = json.loads(_git_source("artifacts/architecture_refactor/baseline_inventory.json"))
    initial = baseline["architecture_debt"]["initial"]
    immutable = {
        str(row["id"])
        for category in ("long_functions", "branchy_functions")
        for row in initial[category]
        if str(row["id"]).startswith(("uquant.engine:", "uquant.execution:"))
    }
    assert {legacy for legacy, _overhead in _EXECUTION_RELOCATED_FUNCTION_DEBT.values()} == immutable
    assert {
        current: overhead
        for current, (_legacy, overhead) in _EXECUTION_RELOCATED_FUNCTION_DEBT.items()
        if overhead
    } == {"uquant.application.decision:_attach_target_attribution": 2}

    normalized = measured_debt(snapshot)
    normalized_ids = {
        str(row["id"]) for category in ("long_functions", "branchy_functions") for row in normalized[category]
    }
    assert not observed & normalized_ids
    assert not {
        identifier for identifier in normalized_ids
        if identifier.startswith(("uquant.application", "uquant.execution", "uquant.engine:"))
    }

    globals_ = snapshot["module_globals"]
    assert isinstance(globals_, list)
    relocated_globals = {
        str(row["id"])
        for row in globals_
        if isinstance(row, dict)
        and str(row["module"]).startswith(("uquant.application", "uquant.execution"))
        and (bool(row["mutable_initializer"]) or bool(row["mutation_sites"]))
    }
    projected_functions, projected_globals = architecture_execution_historical_debt_projection(
        root=ROOT,
        current_functions=observed,
        historical_functions=set(_EXECUTION_RELOCATED_FUNCTION_DEBT),
        current_globals=relocated_globals,
        historical_globals=set(_EXECUTION_RELOCATED_GLOBAL_DEBT),
        function_rows=functions,
        global_rows=globals_,
    )
    assert projected_functions == set(_EXECUTION_RELOCATED_FUNCTION_DEBT)
    assert projected_globals == set(_EXECUTION_RELOCATED_GLOBAL_DEBT)
    assert set(_EXECUTION_RELOCATED_GLOBAL_DEBT.values()) == {"uquant.execution:_RISK_LIFECYCLE_PRIORITY"}

    mutation = copy.deepcopy(snapshot)
    mutated_functions = mutation["functions"]
    assert isinstance(mutated_functions, list)
    mutated_functions.append(
        {
            "id": "uquant.application.decision:unreviewed_long_function",
            "module": "uquant.application.decision",
            "path": "uquant/application/decision.py",
            "qualname": "unreviewed_long_function",
            "line": 1,
            "lines": FINAL_BUDGETS["max_function_lines"] + 1,
            "branch_points": 0,
        }
    )
    mutated_ids = {str(row["id"]) for row in measured_debt(mutation)["long_functions"]}
    assert "uquant.application.decision:unreviewed_long_function" in mutated_ids
    mutated_globals = mutation["module_globals"]
    assert isinstance(mutated_globals, list)
    mutated_globals.append(
        {
            "id": "uquant.execution.tranches:unreviewed_mutable",
            "module": "uquant.execution.tranches",
            "path": "uquant/execution/tranches.py",
            "name": "unreviewed_mutable",
            "mutable_initializer": True,
            "mutation_sites": [],
        }
    )
    mutable_ids = {str(row["id"]) for row in measured_debt(mutation)["mutable_module_globals"]}
    assert "uquant.execution.tranches:unreviewed_mutable" in mutable_ids


def test_execution_moved_definitions_are_mechanically_bound_to_immutable_source() -> None:
    execution_tree = ast.parse(_git_source("uquant/execution.py"))
    immutable_execution = _top_level_definitions(execution_tree)
    assert set(immutable_execution) == set(_EXECUTION_OWNERS)
    for name, relative in _EXECUTION_OWNERS.items():
        if (relative, name) in ARCHITECTURE_EXECUTION_REVIEWED_DEFINITIONS:
            candidate = reviewed_execution_debt_definition(
                root=ROOT,
                relative=relative,
                name=name,
                candidate=None,
                frozen=immutable_execution[name],
            )
        else:
            candidate = _top_level_definitions(ast.parse((ROOT / relative).read_bytes()))[name]
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            immutable_execution[name], include_attributes=False
        )

    engine_tree = ast.parse(_git_source("uquant/engine.py"))
    immutable_top_level = _top_level_definitions(engine_tree)
    immutable_methods = _engine_methods(engine_tree)
    for name, relative in _ENGINE_APPLICATION_OWNERS.items():
        immutable = immutable_top_level.get(name) or immutable_methods[name]
        immutable_normalized = _normalized_docstring_indentation(immutable)
        normalized = (
            reviewed_execution_debt_definition(
                root=ROOT,
                relative=relative,
                name=name,
                candidate=None,
                frozen=immutable_normalized,
            )
            if (relative, name) in ARCHITECTURE_EXECUTION_REVIEWED_DEFINITIONS
            else _normalized_application_definition(
                _top_level_definitions(ast.parse((ROOT / relative).read_bytes()))[name]
            )
        )
        assert ast.dump(normalized, include_attributes=False) == ast.dump(
            immutable_normalized, include_attributes=False
        )

        mutation = copy.deepcopy(normalized)
        first_name = next(node for node in ast.walk(mutation) if isinstance(node, ast.Name))
        first_name.id = f"{first_name.id}_mutation"
        assert ast.dump(mutation, include_attributes=False) != ast.dump(
            immutable_normalized, include_attributes=False
        )
        if ast.get_docstring(normalized, clean=False) is not None:
            doc_mutation = copy.deepcopy(normalized)
            assert isinstance(doc_mutation.body[0], ast.Expr)
            assert isinstance(doc_mutation.body[0].value, ast.Constant)
            doc_mutation.body[0].value.value += " changed"
            assert ast.dump(doc_mutation, include_attributes=False) != ast.dump(
                immutable_normalized, include_attributes=False
            )

    candidate_methods = _engine_methods(ast.parse((ROOT / "uquant/engine.py").read_bytes()))
    for name in _ENGINE_COMPATIBILITY_METHODS:
        candidate = _normalized_docstring_indentation(candidate_methods[name])
        immutable = _normalized_docstring_indentation(immutable_methods[name])
        current_docstring = ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS.get(name)
        if current_docstring is not None:
            assert ast.get_docstring(candidate) == current_docstring
            candidate.body[0] = copy.deepcopy(immutable.body[0])
        assert ast.dump(candidate, include_attributes=False) == ast.dump(immutable, include_attributes=False)


def test_execution_public_reflection_and_pickle_identities_remain_legacy() -> None:
    execution = importlib.import_module("uquant.execution")
    engine = importlib.import_module("uquant.engine")
    for name in (
        "fee_components",
        "merge_pending_orders",
        "plan_orders",
        "reconcile_account_orders",
        "risk_priority_tranche_key",
    ):
        value = getattr(execution, name)
        assert value.__module__ == "uquant.execution"
        assert value.__qualname__ == name
    planner = execution.ExecutionPlanner(DEFAULT_CONFIG)
    assert type(pickle.loads(pickle.dumps(planner))) is execution.ExecutionPlanner
    assert execution.ExecutionPlanner.__module__ == "uquant.execution"
    assert execution.ExecutionPlanner.__qualname__ == "ExecutionPlanner"
    assert execution.ExecutionPlanner.__init__.__module__ == "uquant.execution"
    assert execution.ExecutionPlanner.execute_open.__module__ == "uquant.execution"
    assert engine.ProductionEngine.__module__ == "uquant.engine"
    assert engine.ProductionEngine.__qualname__ == "ProductionEngine"
    signature = inspect.signature(engine.ProductionEngine)
    assert tuple(signature.parameters) == ("data_dir", "cfg")
    assert signature.parameters["data_dir"].annotation == "str | Path"
    assert signature.parameters["cfg"].annotation == "SystemConfig"
    assert signature.parameters["cfg"].default is DEFAULT_CONFIG
    assert signature.return_annotation == "None"


def test_execution_engine_imports_when_python_strips_docstrings_and_assertions() -> None:
    completed = subprocess.run(
        [sys.executable, "-OO", "-c", "import uquant.engine"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_execution_engine_method_reflection_and_descriptors_match_immutable_source() -> None:
    import uquant.engine as engine_module
    from uquant.engine import ProductionEngine

    namespace = {
        "__name__": "uquant.engine",
        "__package__": "uquant",
        "__file__": str(ROOT / "uquant/engine.py"),
    }
    exec(compile(_git_source("uquant/engine.py"), "uquant/engine.py", "exec"), namespace)
    immutable_engine = namespace["ProductionEngine"]
    immutable_definitions = _top_level_definitions(ast.parse(_git_source("uquant/engine.py")))
    for name, definition in immutable_definitions.items():
        if not isinstance(definition, ast.FunctionDef):
            continue
        expected = namespace[name]
        observed = getattr(engine_module, name)
        assert type(observed) is type(expected)
        assert observed.__name__ == expected.__name__ == name
        assert observed.__module__ == expected.__module__ == "uquant.engine"
        assert observed.__qualname__ == expected.__qualname__ == name
        validate_engine_descriptor_transport(
            name=name,
            observed=observed,
            expected=expected,
        )
        if name not in ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS:
            assert observed.__doc__ == expected.__doc__
    assert engine_module.REFERENCE_UNIVERSE is namespace["REFERENCE_UNIVERSE"]

    for name in (
        "__setattr__",
        "__init__",
        "_load",
        "_price",
        "_reference_returns",
        "_causal_risk_timeline",
        "equity",
        "_mark_account_positions",
        "decide",
        "deterministic_decision",
        "backtest",
    ):
        expected_descriptor = immutable_engine.__dict__[name]
        observed_descriptor = ProductionEngine.__dict__[name]
        assert isinstance(observed_descriptor, property) == isinstance(expected_descriptor, property)
        expected = (
            expected_descriptor.fget if isinstance(expected_descriptor, property) else expected_descriptor
        )
        observed = (
            observed_descriptor.fget if isinstance(observed_descriptor, property) else observed_descriptor
        )
        assert expected is not None and observed is not None
        assert type(observed) is type(expected)
        assert observed.__name__ == expected.__name__ == name
        assert observed.__module__ == "uquant.engine"
        assert observed.__qualname__ == f"ProductionEngine.{name}"
        validate_engine_descriptor_transport(
            name=name,
            observed=observed,
            expected=expected,
        )
        if name not in ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS:
            assert observed.__doc__ == expected.__doc__


def test_engine_code_fingerprint_uses_reviewed_registry_membership_without_tree_discovery(
    monkeypatch,
) -> None:
    from uquant import engine

    def forbidden_rglob(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("engine code fingerprint performed whole-tree discovery")

    monkeypatch.setattr(Path, "rglob", forbidden_rglob)
    assert engine.code_fingerprint() == source_surface_fingerprint(
        ROOT,
        "economic_decision_v1",
    )


def test_engine_code_fingerprint_fails_closed_for_missing_and_symlinked_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uquant import engine

    package = tmp_path / "uquant"
    package.mkdir()
    engine_path = package / "engine.py"
    engine_path.write_text("facade = 1\n", encoding="utf-8")
    member = package / "decision.py"
    member.write_text("decision = 1\n", encoding="utf-8")
    surfaces = [
        {
            "id": identifier,
            "source_paths": (
                ["uquant/decision.py", "uquant/engine.py"]
                if identifier == "economic_decision_v1"
                else ["uquant/engine.py"]
            ),
            "resource_paths": [],
        }
        for identifier in (
            "economic_decision_v1",
            "execution_account_v1",
            "sentinel_v1",
            "validation_runner_v1",
            "full_package_v1",
        )
    ]
    unsealed: dict[str, object] = {"registry_version": 2, "surfaces": surfaces}
    registry = tmp_path / "benchmarks/source_surface_registry.json"
    registry.parent.mkdir()
    registry.write_bytes(
        canonical_json_bytes({**unsealed, "canonical_sha256": canonical_json_sha256(unsealed)}) + b"\n"
    )
    monkeypatch.setattr(engine, "__file__", str(engine_path))
    assert engine.code_fingerprint()

    member.unlink()
    with pytest.raises(ValueError, match="source surface member is missing or unsafe"):
        engine.code_fingerprint()

    outside = tmp_path / "outside.py"
    outside.write_text("decision = 1\n", encoding="utf-8")
    member.symlink_to(outside)
    with pytest.raises(ValueError, match="source surface member is missing or unsafe"):
        engine.code_fingerprint()
