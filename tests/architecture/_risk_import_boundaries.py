from __future__ import annotations

from .test_risk_boundaries import (
    _COMPATIBILITY_NAMES,
    _INVENTORY,
    ROOT,
    _git_source,
    _top_level_definitions,
    architecture_snapshot,
    ast,
    inspect,
    json,
    pytest,
    subprocess,
    sys,
)


def test_risk_facade_preserves_consumed_names_reflection_and_live_anchor_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uquant.risk as risk_module

    assert set(vars(risk_module)) >= _COMPATIBILITY_NAMES
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))
    inventory = json.loads(_INVENTORY.read_text(encoding="utf-8"))["entries"][0]
    reflection = inventory["reflection_contract"]
    for name in immutable:
        function = getattr(risk_module, name)
        assert function.__module__ == "uquant.risk"
        assert function.__name__ == name
        assert function.__qualname__ == name
        assert str(inspect.signature(function)) == reflection[name]["signature"]
        assert function.__doc__ == reflection[name]["raw_docstring"]

    observed: list[bool] = []
    original = risk_module._update_dynamic_anchors

    def capture(*args: object, **kwargs: object) -> object:
        observed.append(bool(kwargs["allow_reanchor"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(risk_module, "_update_dynamic_anchors", capture)
    # Existing focused risk tests exercise the full path; this gate pins the
    # explicit facade capability so owner imports cannot capture a stale function.
    assert risk_module.dynamic_anchor_updater() is capture
    assert "_risk_runtime_seam" not in vars(risk_module)
    assert observed == []


def test_risk_package_has_no_reverse_owner_or_platform_imports() -> None:
    forbidden = {
        "fcntl",
        "research",
        "scripts",
        "tests",
        "uquant.account",
        "uquant.application",
        "uquant.execution",
        "uquant.portfolio",
        "uquant.validation",
    }
    for path in sorted((ROOT / "uquant/risk").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
        assert not any(
            name == blocked or name.startswith(f"{blocked}.") for name in imports for blocked in forbidden
        ), (path, imports)

    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, dict)
    cycles = graph["cycles"]
    assert isinstance(cycles, list)
    assert len(cycles) <= 2
    assert all(
        not any(
            str(module) == "uquant.risk" or str(module).startswith("uquant.risk.")
            for module in cycle["modules"]
        )
        for cycle in cycles
    )


def test_risk_imports_under_optimized_and_windows_style_smoke() -> None:
    command = (
        "import builtins; real=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: "
        "(_ for _ in ()).throw(ImportError('blocked fcntl')) "
        "if name=='fcntl' else real(name,*a,**k); "
        "import uquant.risk; assert uquant.risk.assess_risk.__module__=='uquant.risk'"
    )
    completed = subprocess.run(
        [sys.executable, "-OO", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
