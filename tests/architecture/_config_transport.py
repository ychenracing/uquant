"""Exact transport for attribution private edges."""

from __future__ import annotations

import ast
import copy
import subprocess
from collections import Counter
from collections.abc import Mapping, Set
from pathlib import Path
from typing import cast

from ._governance_inventory import ARCHITECTURE_REFERENCE_TREE
from ._private_imports import scan_sealed_governed_private_edges

_IMMUTABLE_GLOBALS = "ec8d4b7d1502ad50a73deab4543480b8b01f7d03"
_ATTRIBUTION_SPLIT = "32af12b93b8981343a97a1754b67da8331c123ba"
_CONFIG_PUBLIC_OWNERS_COMMIT = "fd8863d50635e1bd5a80eba8ba9b14fc6a6b17bb"
_CONFIG_PUBLIC_OWNERS_TREE = "41bced003106d67e5061713b6f25e9fbb14d64d6"
_REVIEWED_PATH_CHAINS: Mapping[str, tuple[str, ...]] = {
    "uquant/attribution/builder.py": (_ATTRIBUTION_SPLIT,),
    "uquant/attribution/concentration.py": (_ATTRIBUTION_SPLIT,),
    "uquant/attribution/replay_evidence.py": (
        _IMMUTABLE_GLOBALS,
        _ATTRIBUTION_SPLIT,
    ),
    "uquant/attribution/validation.py": (
        _IMMUTABLE_GLOBALS,
        _ATTRIBUTION_SPLIT,
    ),
    "uquant/attribution/validation_artifact.py": (_ATTRIBUTION_SPLIT,),
    "uquant/attribution/validation_lots.py": (_ATTRIBUTION_SPLIT,),
}
_TRANSPORTED_IDS = frozenset(
    {
        "uquant.attribution.builder:uquant.attribution.concentration:_empty_pnl_bucket",
        "uquant.attribution.validation:uquant.attribution.concentration:_group_lot_pnl",
        "uquant.attribution.validation:uquant.attribution.concentration:_holding_summary",
        "uquant.attribution.validation:uquant.attribution.replay_evidence:_LEDGER_FIELDS",
        "uquant.attribution.validation:uquant.attribution.replay_evidence:_require_exact_fields",
    }
)
_VALIDATION_TARGETS = (
    "_group_lot_pnl",
    "_holding_summary",
    "_require_exact_fields",
)
_VALIDATION_STAGE_ORDER = (
    "_canonical_attribution_copy",
    "validated_economic_lots",
    "_validated_accounting",
    "_validated_attribution_costs",
    "_validated_attribution_groups",
    "_validate_replacements",
    "_validate_turnover",
    "_validate_holding_periods",
    "_validate_daily_ledger",
    "_validate_diagnostics",
)


def _git_source(root: Path, revision: str, relative: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        text=True,
    )


def config_reviewed_source(root: Path, relative: str) -> str:
    """Read one attribution proof input from the immutable reviewed commit."""

    assert relative in _REVIEWED_PATH_CHAINS
    return (root / relative).read_text(encoding="utf-8")


def config_post_checkpoint_private_edges(
    root: Path,
) -> dict[str, list[dict[str, object]]]:
    """Measure the sealed attribution-owner tree, never the live tree."""

    return scan_sealed_governed_private_edges(
        root,
        commit="105695aacd3d1c7e62705f64188da88d202db4cd",
        tree="e3e2832eb1321e6d45f103cab538aeb9c95852d3",
    )


def _tree(source: str) -> ast.Module:
    return ast.parse(source, type_comments=True)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_reviewed_sources(
    root: Path,
    overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    if overrides is not None:
        assert set(overrides) <= set(_REVIEWED_PATH_CHAINS)
    sources: dict[str, str] = {}
    for relative in _REVIEWED_PATH_CHAINS:
        reviewed = config_reviewed_source(root, relative)
        candidate = (
            overrides[relative]
            if overrides is not None and relative in overrides
            else reviewed
        )
        assert ast.dump(_tree(candidate), include_attributes=False) == ast.dump(
            _tree(reviewed), include_attributes=False
        )
        sources[relative] = candidate
    return sources


def _assert_alias(tree: ast.Module, public: str, private: str) -> None:
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == public
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Name) and value.id == private


def _assert_import(
    tree: ast.Module,
    *,
    module: str,
    public: str,
    local: str,
) -> None:
    matches = [
        alias
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == module
        for alias in node.names
        if alias.name == public and alias.asname == local
    ]
    assert len(matches) == 1


def _call_counter(tree: ast.Module, name: str) -> Counter[str]:
    return Counter(
        ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


class _ExpectedFieldsToImmutable(ast.NodeTransformer):
    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == "expected_fields":
            return ast.copy_location(
                ast.Name(id="expected_diagnostic_fields", ctx=node.ctx),
                node,
            )
        return node


def _validation_target_counter(
    artifact: ast.Module,
    lots: ast.Module,
    name: str,
) -> Counter[str]:
    if name != "_require_exact_fields":
        return _call_counter(artifact, name) + _call_counter(lots, name)
    artifact_copy = copy.deepcopy(artifact)
    diagnostic = _function(artifact_copy, "_validate_diagnostic")
    renamed = _ExpectedFieldsToImmutable().visit(diagnostic)
    assert isinstance(renamed, ast.FunctionDef)
    ast.fix_missing_locations(renamed)
    return _call_counter(artifact_copy, name) + _call_counter(lots, name)


def _assert_validation_public_transport(
    root: Path,
    sources: Mapping[str, str],
) -> None:
    concentration = _tree(sources["uquant/attribution/concentration.py"])
    replay = _tree(sources["uquant/attribution/replay_evidence.py"])
    artifact = _tree(sources["uquant/attribution/validation_artifact.py"])
    lots = _tree(sources["uquant/attribution/validation_lots.py"])
    immutable = _tree(
        _git_source(root, ARCHITECTURE_REFERENCE_TREE, "uquant/attribution/validation.py")
    )
    for public, private in (
        ("group_lot_pnl", "_group_lot_pnl"),
        ("holding_summary", "_holding_summary"),
    ):
        _assert_alias(concentration, public, private)
        _assert_import(artifact, module="concentration", public=public, local=private)
    for public, private in (
        ("LEDGER_FIELDS", "_LEDGER_FIELDS"),
        ("require_exact_attribution_fields", "_require_exact_fields"),
    ):
        _assert_alias(replay, public, private)
    _assert_import(
        artifact,
        module="replay_evidence",
        public="LEDGER_FIELDS",
        local="_LEDGER_FIELDS",
    )
    for owner in (artifact, lots):
        _assert_import(
            owner,
            module="replay_evidence",
            public="require_exact_attribution_fields",
            local="_require_exact_fields",
        )
    for name in _VALIDATION_TARGETS:
        assert _validation_target_counter(artifact, lots, name) == _call_counter(
            immutable, name
        )
    immutable_ledger = sum(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "_LEDGER_FIELDS"
        for node in ast.walk(immutable)
    )
    current_ledger = sum(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "_LEDGER_FIELDS"
        for node in ast.walk(artifact)
    )
    assert immutable_ledger == current_ledger == 1
    orchestrator = _function(artifact, "validate_economic_attribution_artifact")
    observed_order = tuple(
        cast(ast.Name, node.func).id
        for node in sorted(
            (
                node
                for node in ast.walk(orchestrator)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _VALIDATION_STAGE_ORDER
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
    )
    assert observed_order == _VALIDATION_STAGE_ORDER


def _assert_empty_bucket_absorption(
    root: Path,
    sources: Mapping[str, str],
) -> None:
    immutable_builder = _function(
        _tree(_git_source(root, ARCHITECTURE_REFERENCE_TREE, "uquant/attribution/builder.py")),
        "build_economic_attribution",
    )
    empty_calls = [
        node
        for node in ast.walk(immutable_builder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_empty_pnl_bucket"
    ]
    assert [ast.unparse(node) for node in empty_calls] == [
        "_empty_pnl_bucket()",
        "_empty_pnl_bucket()",
    ]
    group_assignments = [
        node
        for node in ast.walk(immutable_builder)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "by_symbol"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_group_lot_pnl"
        and tuple(
            ast.dump(argument, include_attributes=False)
            for argument in node.value.args
        )
        == (
            "Name(id='lots', ctx=Load())",
            "Constant(value='symbol')",
        )
        and not node.value.keywords
    ]
    assert len(group_assignments) == 1
    group_line = group_assignments[0].lineno
    assert max(node.lineno for node in empty_calls) < group_line
    assert not [
        node
        for node in ast.walk(immutable_builder)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "by_symbol"
        and max(call.lineno for call in empty_calls) < node.lineno < group_line
    ]

    immutable_concentration = _tree(
        _git_source(root, ARCHITECTURE_REFERENCE_TREE, "uquant/attribution/concentration.py")
    )
    current_concentration = _tree(sources["uquant/attribution/concentration.py"])
    assert ast.dump(
        _function(current_concentration, "_group_lot_pnl"),
        include_attributes=False,
    ) == ast.dump(
        _function(immutable_concentration, "_group_lot_pnl"),
        include_attributes=False,
    )
    current_builder = _tree(sources["uquant/attribution/builder.py"])
    group_stage = _function(current_builder, "_attribution_groups")
    reviewed_first = _function(
        _tree(config_reviewed_source(root, "uquant/attribution/builder.py")),
        "_attribution_groups",
    ).body[0]
    assert ast.dump(group_stage.body[0], include_attributes=False) == ast.dump(
        reviewed_first,
        include_attributes=False,
    )
    build = _function(current_builder, "build_economic_attribution")
    stage_calls = [
        node
        for node in ast.walk(build)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_attribution_groups"
    ]
    assert len(stage_calls) == 1
    assert ast.unparse(stage_calls[0]) == "_attribution_groups(lots)"


def validate_config_private_transport(
    *,
    root: Path,
    source_overrides: Mapping[str, str] | None = None,
) -> None:
    """Require the exact reviewed owner, identity, callee, argument, and order transport."""
    sources = _assert_reviewed_sources(root, source_overrides)
    _assert_validation_public_transport(root, sources)
    _assert_empty_bucket_absorption(root, sources)


def config_private_relocation_projection(
    *,
    root: Path,
    expected: Set[str],
    observed: Set[str],
    source_overrides: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Project only the five exact reviewed transports onto frozen attribution history."""
    expected_set = frozenset(expected)
    observed_set = frozenset(observed)
    assert observed_set <= expected_set
    assert expected_set - observed_set == _TRANSPORTED_IDS
    validate_config_private_transport(root=root, source_overrides=source_overrides)
    return observed_set | _TRANSPORTED_IDS
