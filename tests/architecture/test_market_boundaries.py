from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from ._analysis import ROOT

_RISK_REFERENCE_COMMIT = "36bc6968ee61eb578a8f19ee132aecb9b03fe7ca"
_RISK_REFERENCE_TREE = "3cc640cf565e116aa524466485dc7d9e1b511538"


def _immutable_engine_reference_alias() -> str:
    source = subprocess.run(
        ["git", "show", f"{_RISK_REFERENCE_TREE}:uquant/engine.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree = ast.parse(source, filename="uquant/engine.py")
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "REFERENCE_UNIVERSE" for target in node.targets)
    )
    return ast.dump(assignment, include_attributes=False)


def _trees(roots: tuple[str, ...]) -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for root in roots
        for path in (ROOT / root).rglob("*.py")
    ]


def _reference_universe_violations(trees: list[tuple[Path, ast.Module]]) -> list[str]:
    violations: list[str] = []
    immutable_engine_alias = _immutable_engine_reference_alias()
    for path, tree in trees:
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
                    if (
                        relative == "uquant/engine.py"
                        and ast.dump(node, include_attributes=False) == immutable_engine_alias
                    ):
                        continue
                    violations.append(f"{relative}:{node.lineno}")
    return violations


def test_no_code_mutates_reference_universe_process_globals() -> None:
    assert _reference_universe_violations(_trees(("uquant", "research", "scripts"))) == []


def test_reference_universe_guard_rejects_new_name_assignment() -> None:
    mutation = ast.parse("REFERENCE_UNIVERSE = ('mutation',)\n")
    assert _reference_universe_violations([(ROOT / "uquant/engine.py", mutation)]) == ["uquant/engine.py:1"]


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
