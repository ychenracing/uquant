"""Fail-closed transport across reviewed Task-10 owner-split commits."""

from __future__ import annotations

import ast
import copy
import subprocess
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path

from ._governance_inventory import ARCHITECTURE_REFERENCE_COMMIT

_REVIEWED_OWNER_CHAINS: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("uquant/portfolio/leaders/admission.py", "_dynamic_k"): ("71bb2bd",),
    ("uquant/portfolio/leaders/lifecycle.py", "_update_leader_cycle_arm"): ("71bb2bd",),
    ("uquant/portfolio/leaders/targets.py", "_leader_targets"): (
        "71bb2bd",
        "32c624e",
    ),
    ("uquant/portfolio/strategic/discovery.py", "_initialize_strategic_cohort"): ("b37daed",),
    ("uquant/portfolio/strategic/lifecycle.py", "_strategic_cohort_targets"): ("b37daed",),
    ("uquant/portfolio/recovery/substitution.py", "_recovery_anchor_substitution"): (
        "8e5c152",
        "b37daed",
    ),
    ("uquant/portfolio/recovery/admission.py", "_recovery_admission_targets"): ("b1eeb4c",),
}


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
    """Read one reviewed owner source from its immutable transport chain."""
    key = (relative, name)
    assert key in _REVIEWED_OWNER_CHAINS
    return _git_source(root, _REVIEWED_OWNER_CHAINS[key][-1], relative)


def expand_reviewed_architecture_owner(
    *,
    root: Path,
    relative: str,
    name: str,
    candidate: ast.FunctionDef | None,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Expand one exact reviewed split chain to its immutable start owner."""
    start_source = _git_source(root, ARCHITECTURE_REFERENCE_COMMIT, relative)
    frozen = _function(start_source, name)
    key = (relative, name)
    if key not in _REVIEWED_OWNER_CHAINS:
        if candidate is not None:
            assert ast.dump(candidate, include_attributes=False) == ast.dump(
                frozen, include_attributes=False
            )
        return copy.deepcopy(frozen)
    chain = _REVIEWED_OWNER_CHAINS[key]
    first_parent = _function(_git_source(root, f"{chain[0]}^", relative), name)
    assert ast.dump(first_parent, include_attributes=False) == ast.dump(frozen, include_attributes=False)
    for previous, current in pairwise(chain):
        previous_source = _git_source(root, previous, relative)
        current_parent_source = _git_source(root, f"{current}^", relative)
        assert ast.dump(ast.parse(previous_source, type_comments=True), include_attributes=False) == ast.dump(
            ast.parse(current_parent_source, type_comments=True), include_attributes=False
        )
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
