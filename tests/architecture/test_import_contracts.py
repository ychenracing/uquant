from __future__ import annotations

import ast
import copy
from collections.abc import Mapping
from pathlib import Path

import pytest

from ._analysis import ROOT, architecture_snapshot


def _ids(rows: object) -> set[str]:
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows if isinstance(row, Mapping)}


def test_production_imports_never_point_to_nonproduction_roots(
    current_architecture: dict[str, object],
) -> None:
    graph = current_architecture["import_graph"]
    assert isinstance(graph, Mapping)
    assert graph["forbidden_imports"] == []


def test_every_internal_module_has_an_explicit_authority(
    current_architecture: dict[str, object],
) -> None:
    modules = current_architecture["modules"]
    graph = current_architecture["import_graph"]
    assert isinstance(modules, Mapping)
    assert isinstance(graph, Mapping)
    authorities = graph["module_authorities"]
    assert isinstance(authorities, Mapping)
    assert set(authorities) == set(modules)
    assert authorities["uquant.engine"] == "production_safe"
    assert authorities["uquant.validation.competitor"] == "validation_runner"
    assert authorities["uquant.validation.promotion"] == "validation_runner"
    assert authorities["uquant.validation.holdout"] == "validation_runner"
    assert authorities["uquant.validation.generalization"] == "validation_runner"
    assert authorities["uquant.cli"] == "cli_runner"
    assert authorities["uquant.risk_sentinel.cli"] == "cli_runner"


@pytest.mark.parametrize(
    ("target_module", "target_authority"),
    (
        ("uquant.validation.competitor", "validation_runner"),
        ("uquant.validation.promotion", "validation_runner"),
        ("uquant.validation.holdout", "validation_runner"),
        ("uquant.validation.generalization", "validation_runner"),
        ("uquant.cli", "cli_runner"),
    ),
)
def test_production_safe_imports_of_runner_authorities_are_forbidden(
    tmp_path: Path, target_module: str, target_authority: str
) -> None:
    paths = {
        "uquant": "",
        "uquant.core": f"import {target_module}\n",
        target_module.rpartition(".")[0]: "",
        target_module: "",
    }
    authorities = {
        module: (
            "production_safe"
            if module in {"uquant", "uquant.core"}
            else target_authority
        )
        for module in paths
    }
    for module, source in paths.items():
        relative = Path(*module.split("."))
        path = tmp_path / relative.with_suffix(".py")
        if not source:
            path = tmp_path / relative / "__init__.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    snapshot = architecture_snapshot(tmp_path, module_authorities=authorities)
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    assert graph["forbidden_imports"] == [
        {
            "id": f"uquant.core:1:{target_module}",
            "importer": "uquant.core",
            "importer_authority": "production_safe",
            "line": 1,
            "target": target_module,
            "target_authority": target_authority,
        }
    ]


def test_from_import_resolves_the_specific_runner_module_authority(tmp_path: Path) -> None:
    sources = {
        "uquant/__init__.py": "",
        "uquant/core.py": "from uquant import cli\n",
        "uquant/cli.py": "",
    }
    for relative, source in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    snapshot = architecture_snapshot(
        tmp_path,
        module_authorities={
            "uquant": "production_safe",
            "uquant.cli": "cli_runner",
            "uquant.core": "production_safe",
        },
    )
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    assert graph["forbidden_imports"] == [
        {
            "id": "uquant.core:1:uquant.cli",
            "importer": "uquant.core",
            "importer_authority": "production_safe",
            "line": 1,
            "target": "uquant.cli",
            "target_authority": "cli_runner",
        }
    ]


def test_cross_module_private_import_debt_is_exact_and_can_only_shrink(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    debt = baseline_inventory["architecture_debt"]
    graph = current_architecture["import_graph"]
    assert isinstance(debt, Mapping)
    assert isinstance(graph, Mapping)
    initial = _ids(debt["initial"]["cross_module_private_imports"])
    allowed = set(debt["temporary_allowlist"]["cross_module_private_imports"])
    observed = _ids(graph["cross_module_private_imports"])
    assert allowed == initial
    assert observed <= allowed


def test_internal_import_cycles_are_exact_and_can_only_disappear(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    debt = baseline_inventory["architecture_debt"]
    graph = current_architecture["import_graph"]
    assert isinstance(debt, Mapping)
    assert isinstance(graph, Mapping)
    initial = _ids(debt["initial"]["internal_import_cycles"])
    allowed = set(debt["temporary_allowlist"]["internal_import_cycles"])
    observed = _ids(graph["cycles"])
    assert allowed == initial
    assert observed <= allowed


def test_private_import_and_cycle_debt_can_disappear_without_baseline_edits(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    reduced = copy.deepcopy(current_architecture)
    graph = reduced["import_graph"]
    assert isinstance(graph, dict)
    private_imports = graph["cross_module_private_imports"]
    cycles = graph["cycles"]
    assert isinstance(private_imports, list)
    assert isinstance(cycles, list)
    graph["cross_module_private_imports"] = private_imports[1:]
    graph["cycles"] = cycles[1:]
    test_cross_module_private_import_debt_is_exact_and_can_only_shrink(
        baseline_inventory, reduced
    )
    test_internal_import_cycles_are_exact_and_can_only_disappear(
        baseline_inventory, reduced
    )


def test_domain_models_have_no_dataframe_filesystem_or_runner_dependencies() -> None:
    forbidden_external = {
        "glob",
        "os",
        "pandas",
        "pathlib",
        "research",
        "shutil",
        "subprocess",
        "tempfile",
    }
    observed_external: set[str] = set()
    for path in (ROOT / "uquant" / "models").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                observed_external.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                observed_external.add(node.module.partition(".")[0])

    assert observed_external.isdisjoint(forbidden_external)


def test_config_and_models_keep_the_required_leaf_dependency_direction(
    current_architecture: dict[str, object],
) -> None:
    graph = current_architecture["import_graph"]
    assert isinstance(graph, Mapping)
    edges = graph["edges"]
    assert isinstance(edges, list)
    forbidden_model_prefixes = (
        "uquant.application",
        "uquant.validation",
    )
    forbidden_config_prefixes = (
        "uquant.account",
        "uquant.engine",
        "uquant.execution",
        "uquant.market",
        "uquant.portfolio",
        "uquant.risk",
    )
    violations = []
    for edge in edges:
        assert isinstance(edge, Mapping)
        importer = str(edge["importer"])
        imported = str(edge["imported"])
        if importer.startswith("uquant.models") and imported.startswith(forbidden_model_prefixes):
            violations.append(dict(edge))
        if importer.startswith("uquant.config") and imported.startswith(forbidden_config_prefixes):
            violations.append(dict(edge))

    assert violations == []
