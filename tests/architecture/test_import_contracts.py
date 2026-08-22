from __future__ import annotations

from collections.abc import Mapping


def _ids(rows: object) -> set[str]:
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows if isinstance(row, Mapping)}


def test_production_imports_never_point_to_nonproduction_roots(
    current_architecture: dict[str, object],
) -> None:
    graph = current_architecture["import_graph"]
    assert isinstance(graph, Mapping)
    assert graph["forbidden_imports"] == []


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
    assert allowed <= initial
    assert observed == allowed


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
    assert allowed <= initial
    assert observed == allowed
