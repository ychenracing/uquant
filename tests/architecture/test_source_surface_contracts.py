from __future__ import annotations

import tomllib

from uquant.contracts.source_surfaces import SOURCE_SURFACE_IDS
from uquant.provenance.surfaces import load_source_surface_registry

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


def test_full_package_surface_and_distribution_boundary_are_explicitly_separate() -> None:
    """Keep repository provenance intact while shipping only production packages."""
    registry = load_source_surface_registry(ROOT)
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = configuration["tool"]["setuptools"]
    discovery = setuptools["packages"]["find"]
    assert discovery == {
        "include": ["uquant*"],
        "exclude": ["research*", "scripts*", "tests*"],
    }
    distribution_roots = tuple(
        ROOT / pattern.removesuffix("*") for pattern in discovery["include"]
    )
    distribution_sources = {
        path.relative_to(ROOT).as_posix()
        for package_root in distribution_roots
        for path in package_root.rglob("*.py")
        if path.is_file()
    }
    package_roots = (ROOT / "uquant", ROOT / "research")
    expected_sources = {
        path.relative_to(ROOT).as_posix()
        for package_root in package_roots
        for path in package_root.rglob("*.py")
        if path.is_file()
    }
    expected_package_data = {
        path.relative_to(ROOT).as_posix()
        for package, patterns in setuptools["package-data"].items()
        for pattern in patterns
        for path in (ROOT.joinpath(*package.split("."))).glob(pattern)
        if path.is_file()
    }
    readme = configuration["project"]["readme"]
    license_files = configuration["project"]["license-files"]
    expected_resources = expected_package_data | {
        str(readme),
        *(str(path) for path in license_files),
        "benchmarks/source_surface_registry.json",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    }

    full_package = registry.surface("full_package_v1")
    validation_runner = registry.surface("validation_runner_v1")
    script_sources = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts").rglob("*.py")
        if path.is_file()
    }

    assert set(full_package.source_paths) == expected_sources
    assert set(full_package.resource_paths) == expected_resources
    assert distribution_sources < set(full_package.source_paths)
    assert not any(path.startswith("research/") for path in distribution_sources)
    assert script_sources <= set(validation_runner.source_paths)
    assert script_sources.isdisjoint(full_package.source_paths)


def test_economic_surface_covers_runtime_imports_without_runner_authority() -> None:
    registry = load_source_surface_registry(ROOT)
    economic = registry.surface("economic_decision_v1")
    economic_modules = {_module_for(relative) for relative in economic.source_paths}
    closure = _production_closure("uquant", "uquant.engine")

    assert closure <= economic_modules
    assert not any(module.startswith("uquant.validation") for module in closure)
    assert all(MODULE_AUTHORITIES[module] == "production_safe" for module in economic_modules)
    assert not any(
        relative.startswith(("research/", "scripts/", "tests/", "uquant/validation/"))
        for relative in economic.paths
    )
    assert "uquant.validation" not in economic_modules
    assert "uquant.risk_sentinel.cli" not in economic_modules
