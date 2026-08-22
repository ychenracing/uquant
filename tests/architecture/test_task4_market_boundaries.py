from __future__ import annotations

import ast
from pathlib import Path

from ._analysis import ROOT


def _trees(roots: tuple[str, ...]) -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for root in roots
        for path in (ROOT / root).rglob("*.py")
    ]


def test_no_code_mutates_reference_universe_process_globals() -> None:
    violations: list[str] = []
    for path, tree in _trees(("uquant", "research", "scripts")):
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "REFERENCE_UNIVERSE":
                    violations.append(f"{relative}:{node.lineno}")
                if (
                    isinstance(target, ast.Name)
                    and target.id == "REFERENCE_UNIVERSE"
                    and relative != "uquant/leader.py"
                ):
                    violations.append(f"{relative}:{node.lineno}")
    assert violations == []


def test_non_test_consumers_use_market_api_not_engine_market_privates() -> None:
    forbidden = {"_raw", "_features", "_load", "_price", "_reference_returns"}
    violations: list[str] = []
    for path, tree in _trees(("uquant/validation", "research", "scripts")):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(
            f"{relative}:{node.lineno}:{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden
        )
    assert violations == []


def test_market_package_has_no_forbidden_authority_or_global_universe_dependency() -> None:
    forbidden_roots = {
        "application",
        "research",
        "scripts",
        "validation",
        "observation",
        "account",
    }
    violations: list[str] = []
    for path, tree in _trees(("uquant/market",)):
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = (module,)
            else:
                continue
            for name in names:
                parts = name.split(".")
                if any(part in forbidden_roots for part in parts):
                    violations.append(f"{relative}:{node.lineno}:{name}")
    assert violations == []
