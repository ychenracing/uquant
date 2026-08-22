from __future__ import annotations

from uquant.contracts.source_surfaces import SOURCE_SURFACE_IDS
from uquant.provenance.source_surfaces import load_source_surface_registry

from ._analysis import MODULE_AUTHORITIES, ROOT, architecture_snapshot, module_name


def _module_for(relative: str) -> str:
    return module_name(ROOT, ROOT / relative)


def _production_closure(*roots: str) -> set[str]:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    edges = graph["edges"]
    assert isinstance(edges, list)
    imports: dict[str, set[str]] = {module: set() for module in MODULE_AUTHORITIES}
    for edge in edges:
        assert isinstance(edge, dict)
        imports[str(edge["importer"])].add(str(edge["imported"]))
    closure = set(roots)
    pending = list(roots)
    while pending:
        importer = pending.pop()
        for target in imports[importer]:
            if MODULE_AUTHORITIES[target] != "production_safe" or target in closure:
                continue
            closure.add(target)
            pending.append(target)
    return closure


def test_reviewed_source_registry_uses_exact_physical_paths() -> None:
    registry = load_source_surface_registry(ROOT)

    assert tuple(surface.identifier for surface in registry.surfaces) == SOURCE_SURFACE_IDS
    for surface in registry.surfaces:
        for relative in surface.paths:
            path = ROOT / relative
            assert path.is_file(), f"missing {surface.identifier} member: {relative}"
            assert not path.is_symlink(), f"symlinked {surface.identifier} member: {relative}"
    assert "requirements.txt" in registry.surface("full_package_v1").resource_paths


def test_full_package_surface_covers_every_owned_python_source() -> None:
    registry = load_source_surface_registry(ROOT)
    expected = {
        path.relative_to(ROOT).as_posix()
        for parent in (ROOT / "uquant", ROOT / "research", ROOT / "scripts")
        for path in parent.rglob("*.py")
        if path.is_file()
    }

    assert set(registry.surface("full_package_v1").source_paths) == expected


def test_economic_surface_covers_runtime_imports_without_runner_authority() -> None:
    registry = load_source_surface_registry(ROOT)
    economic = registry.surface("economic_decision_v1")
    economic_modules = {_module_for(relative) for relative in economic.source_paths}
    closure = _production_closure("uquant", "uquant.engine")

    assert closure <= economic_modules
    assert all(MODULE_AUTHORITIES[module] == "production_safe" for module in economic_modules)
    assert not any(
        relative.startswith(("research/", "scripts/", "tests/"))
        for relative in economic.source_paths
    )
    assert "uquant.validation" not in economic_modules
    assert "uquant.risk_sentinel.cli" not in economic_modules
