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
    reviewed = _function(candidate_source, name)
    if candidate is not None:
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            reviewed, include_attributes=False
        )
    return copy.deepcopy(frozen)
