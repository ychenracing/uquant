"""Fail-closed transport between frozen owners and current domain owners."""

from __future__ import annotations

import ast
import copy
import subprocess
from collections.abc import Mapping
from pathlib import Path

from ._governance_inventory import ARCHITECTURE_REFERENCE_TREE

_REVIEWED_OWNER_FUNCTIONS = frozenset(
    {
        ("uquant/portfolio/leaders/admission.py", "_dynamic_k"),
        ("uquant/portfolio/leaders/lifecycle.py", "_update_leader_cycle_arm"),
        ("uquant/portfolio/leaders/targets.py", "_leader_targets"),
        ("uquant/portfolio/strategic/discovery.py", "_initialize_strategic_cohort"),
        ("uquant/portfolio/strategic/lifecycle.py", "_strategic_cohort_targets"),
        ("uquant/portfolio/recovery/substitution.py", "_recovery_anchor_substitution"),
        ("uquant/portfolio/recovery/admission.py", "_recovery_admission_targets"),
    }
)


RETIRED_LEADER_METHODS = frozenset({"_update_leader_cycle_arm", "_leader_targets"})


def assert_retired_leader_owners_absent(
    root: Path, overrides: Mapping[str, str] | None = None,
) -> None:
    """Keep frozen owners as history while forbidding their production revival."""
    retired = RETIRED_LEADER_METHODS | {name.removeprefix("_") for name in RETIRED_LEADER_METHODS}
    sources = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in (root / "uquant").rglob("*.py")
    }
    if overrides:
        sources.update(overrides)
    assert "uquant/portfolio/leaders/extensions.py" not in sources
    for relative, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            value = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                value = node.name
            elif isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.Attribute):
                value = node.attr
            elif isinstance(node, ast.alias):
                value = node.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
            assert value not in retired, (relative, value)


def _git_source(root: Path, revision: str, relative: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        text=True,
    )


def _function(source: str, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in ast.parse(source, type_comments=True).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def reviewed_architecture_owner_source(root: Path, relative: str, name: str) -> str:
    """Read one current owner whose frozen projection is governed here."""
    key = (relative, name)
    assert key in _REVIEWED_OWNER_FUNCTIONS
    return (root / relative).read_text(encoding="utf-8")


def expand_reviewed_architecture_owner(
    *,
    root: Path,
    relative: str,
    name: str,
    candidate: ast.FunctionDef | None,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Expand one exact current owner to its frozen starting definition."""
    start_source = _git_source(root, ARCHITECTURE_REFERENCE_TREE, relative)
    frozen = _function(start_source, name)
    key = (relative, name)
    if key not in _REVIEWED_OWNER_FUNCTIONS:
        if candidate is not None:
            assert ast.dump(candidate, include_attributes=False) == ast.dump(
                frozen, include_attributes=False
            )
        return copy.deepcopy(frozen)
    reviewed_source = reviewed_architecture_owner_source(root, relative, name)
    candidate_source = (
        overrides[relative]
        if overrides is not None and relative in overrides
        else reviewed_source
    )
    assert ast.dump(ast.parse(candidate_source, type_comments=True), include_attributes=False) == ast.dump(
        ast.parse(reviewed_source, type_comments=True), include_attributes=False
    )
    if name in RETIRED_LEADER_METHODS:
        assert candidate is None
        assert_retired_leader_owners_absent(root, overrides)
        return copy.deepcopy(frozen)
    reviewed = _function(candidate_source, name)
    if candidate is not None:
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            reviewed, include_attributes=False
        )
    return copy.deepcopy(frozen)
