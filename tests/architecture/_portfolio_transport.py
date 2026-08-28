"""Exact transport for frozen portfolio owner checkpoints."""

from __future__ import annotations

import ast
import copy
import subprocess
from collections.abc import Mapping, Set
from pathlib import Path

from ._governance_inventory import ARCHITECTURE_REFERENCE_TREE
from ._owner_transport import architecture_portfolio_reviewed_sources


def _definitions(source: str) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.parse(source, type_comments=True).body
        if isinstance(node, ast.FunctionDef)
    }


def _source(root: Path, relative: str, overrides: Mapping[str, str] | None) -> str:
    if overrides is not None and relative in overrides:
        return overrides[relative]
    return (root / relative).read_text(encoding="utf-8")


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _same(observed: ast.AST, expected: ast.AST) -> None:
    assert _dump(observed) == _dump(expected)


def _call(function: ast.FunctionDef, name: str) -> ast.Call:
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]
    assert len(calls) == 1
    return calls[0]


def _exact_keyword_call(
    call: ast.Call,
    names: tuple[str, ...],
    *,
    positional: tuple[str, ...] = (),
) -> None:
    assert tuple(ast.unparse(value) for value in call.args) == positional
    assert not any(keyword.arg is None for keyword in call.keywords)
    assert tuple(keyword.arg for keyword in call.keywords) == names
    assert tuple(ast.unparse(keyword.value) for keyword in call.keywords) == names


def _expand_allocator(
    current: ast.FunctionDef,
    definitions: Mapping[str, ast.FunctionDef],
    frozen: ast.FunctionDef,
) -> None:
    current = copy.deepcopy(current)
    targets = copy.deepcopy(definitions["_allocate_strategy_targets"])
    dominant = definitions["_dominant_level1_retention"]
    observation_keywords = (
        "qualification_panel",
        "qualification_leaders",
        "strategic_universe",
    )
    strategy_calls = [
        node
        for node in ast.walk(targets)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_allocate_strategy"
    ]
    assert len(strategy_calls) == 1
    strategy_call = strategy_calls[0]
    assert tuple(keyword.arg for keyword in strategy_call.keywords[-3:]) == observation_keywords
    assert tuple(ast.unparse(keyword.value) for keyword in strategy_call.keywords[-3:]) == (
        "qualification_panel",
        "qualification_leaders",
        "strategic_universe",
    )
    del strategy_call.keywords[-3:]
    assert len(targets.body) == 6 and len(dominant.body) == 3
    for observed, expected in zip(targets.body[:-1], frozen.body[1:6], strict=True):
        _same(observed, expected)
    target_return = targets.body[-1]
    assert isinstance(target_return, ast.Return)
    assert ast.unparse(target_return.value) == "(targets, strategy_account, sentinel_only_freeze)"
    _same(current.body[0], frozen.body[0])
    target_call = _call(current, "_allocate_strategy_targets")
    _exact_keyword_call(
        target_call,
        (
            "date",
            "opportunity",
            "risk",
            "user_panel",
            "leaders",
            "account",
            "prices",
            *observation_keywords,
        ),
        positional=("self",),
    )
    del target_call.keywords[-3:]
    _exact_keyword_call(
        target_call,
        ("date", "opportunity", "risk", "user_panel", "leaders", "account", "prices"),
        positional=("self",),
    )
    sentinel_projection = current.body[2]
    assert isinstance(sentinel_projection, ast.If)
    assert len(sentinel_projection.body) == 6
    qualification_copy = sentinel_projection.body[0]
    assert isinstance(qualification_copy, ast.Assign)
    assert ast.unparse(qualification_copy.targets[0]) == "account.strategic_qualification"
    assert ast.unparse(qualification_copy.value) == (
        "deepcopy(strategy_account.strategic_qualification)"
    )
    qualification_observation = sentinel_projection.body[1]
    assert isinstance(qualification_observation, ast.If)
    assert ast.unparse(qualification_observation.test) == (
        "account.strategic_qualification.candidate_symbol"
    )
    del sentinel_projection.body[:2]
    _same(current.body[2], frozen.body[6])
    for observed, expected in zip(current.body[3:8], frozen.body[7:12], strict=True):
        _same(observed, expected)
    _same(dominant.body[0], frozen.body[12])
    _same(dominant.body[1], frozen.body[13])
    dominant_return = dominant.body[2]
    assert isinstance(dominant_return, ast.Return) and dominant_return.value is not None
    expected_assignment = copy.deepcopy(frozen.body[14])
    assert isinstance(expected_assignment, ast.Assign)
    _same(dominant_return.value, expected_assignment.value)
    dominant_call = _call(current, "_dominant_level1_retention")
    _exact_keyword_call(
        dominant_call,
        ("risk", "account", "weights_now", "target_gross", "current_gross"),
    )
    for observed, expected in zip(current.body[9:], frozen.body[15:], strict=True):
        _same(observed, expected)


class _SixTupleElement(ast.NodeTransformer):
    def __init__(self, *, index: int, target: str, direct_values: bool) -> None:
        self._index = index
        self._target = target
        self._direct_values = direct_values

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == self._target:
            replacement: ast.expr = ast.Constant(value=self._index)
            if self._direct_values:
                replacement = ast.Subscript(
                    value=ast.Name(id="retained", ctx=ast.Load()),
                    slice=replacement,
                    ctx=ast.Load(),
                )
            return ast.copy_location(replacement, node)
        return node


def _expanded_six_tuple(expression: ast.expr) -> ast.Tuple:
    assert isinstance(expression, ast.Call) and ast.unparse(expression.func) == "tuple"
    assert len(expression.args) == 1 and not expression.keywords
    generator = expression.args[0]
    assert isinstance(generator, ast.GeneratorExp) and len(generator.generators) == 1
    iterator = generator.generators[0]
    target = ast.unparse(iterator.target)
    direct_values = ast.unparse(iterator.iter) == "retained"
    assert (target, ast.unparse(iterator.iter)) in {
        ("value", "retained"),
        ("index", "range(6)"),
    }
    return ast.Tuple(
        elts=[
            _SixTupleElement(
                index=index,
                target=target,
                direct_values=direct_values,
            ).visit(copy.deepcopy(generator.elt))
            for index in range(6)
        ],
        ctx=ast.Load(),
    )


def _expand_typed_tuple_owner(
    name: str,
    current: ast.FunctionDef,
    definitions: Mapping[str, ast.FunctionDef],
    frozen: ast.FunctionDef,
) -> None:
    _same(current.body[0], frozen.body[0])
    frozen_return = frozen.body[-1]
    current_return = current.body[-1]
    assert isinstance(frozen_return, ast.Return) and frozen_return.value is not None
    assert isinstance(current_return, ast.Return) and current_return.value is not None
    expanded = _expanded_six_tuple(frozen_return.value)
    if name == "_risk_lifecycle_rank":
        _same(current_return.value, expanded)
        return
    assert name == "_subset_retention_vector"
    totals = definitions["_retention_totals"]
    assert len(totals.body) == 1 and isinstance(totals.body[0], ast.Return)
    assert totals.body[0].value is not None
    _same(totals.body[0].value, expanded)
    call = _call(current, "_retention_totals")
    assert [ast.unparse(value) for value in call.args] == ["vectors"] and not call.keywords


def _expand_sparse(
    current: ast.FunctionDef,
    definitions: Mapping[str, ast.FunctionDef],
    frozen: ast.FunctionDef,
) -> None:
    for observed, expected in zip(current.body[:6], frozen.body[:6], strict=True):
        _same(observed, expected)
    chunks = (
        ("_retained_lifecycle_buckets", 6, 10),
        ("_risk_boundary_bucket", 10, 14),
        ("_risk_boundary_plans", 14, 17),
    )
    for helper_name, start, stop in chunks:
        helper = definitions[helper_name]
        assert isinstance(helper.body[-1], ast.Return)
        for observed, expected in zip(helper.body[:-1], frozen.body[start:stop], strict=True):
            _same(observed, expected)
        call = _call(current, helper_name)
        assert not call.args and not any(keyword.arg is None for keyword in call.keywords)
    frozen_rank = frozen.body[17]
    assert isinstance(frozen_rank, ast.FunctionDef)
    current_rank = definitions["_risk_plan_rank"]
    assert len(current_rank.body) + 1 == len(frozen_rank.body)
    _same(current_rank.body[0], frozen_rank.body[1])
    current_totals = current_rank.body[1]
    frozen_totals = frozen_rank.body[2]
    assert isinstance(current_totals, ast.Assign) and isinstance(frozen_totals, ast.Assign)
    totals = definitions["_retention_totals"].body[0]
    assert isinstance(totals, ast.Return) and totals.value is not None
    _same(totals.value, _expanded_six_tuple(frozen_totals.value))
    for observed, expected in zip(current_rank.body[2:], frozen_rank.body[3:], strict=True):
        _same(observed, expected)
    rank_call = _call(current, "_risk_plan_rank")
    assert [ast.unparse(value) for value in rank_call.args] == ["self", "plan"]
    assert tuple(keyword.arg for keyword in rank_call.keywords) == (
        "target_by_symbol",
        "account",
        "weights_now",
        "safe_weights",
        "eligible",
        "prices",
        "risk_reason_code",
    )
    materialize = definitions["_materialize_risk_reduction_targets"]
    for observed, expected in zip(materialize.body, frozen.body[19:], strict=True):
        _same(observed, expected)
    materialize_call = _call(current, "_materialize_risk_reduction_targets")
    _exact_keyword_call(
        materialize_call,
        (
            "targets",
            "retained",
            "weights_now",
            "gross_cap",
            "risk_reason",
            "risk_reason_code",
            "risk_exit_kind",
        ),
        positional=("self",),
    )


def _expand_freeze(
    current: ast.FunctionDef,
    definitions: Mapping[str, ast.FunctionDef],
    frozen: ast.FunctionDef,
) -> None:
    _same(current.body[0], frozen.body[0])
    fragments = (
        ("_frozen_cleanup_scope", frozen.body[1:9]),
        ("_commit_frozen_symbol_cleanup", frozen.body[10:13]),
        ("_commit_frozen_exit_events", frozen.body[13:15]),
        ("_commit_frozen_recovery_exit", frozen.body[17].body),
    )
    for helper_name, expected in fragments:
        helper = definitions[helper_name]
        observed = helper.body[:-1] if helper_name == "_frozen_cleanup_scope" else helper.body
        assert len(observed) == len(expected)
        for candidate, source in zip(observed, expected, strict=True):
            _same(candidate, source)
    _same(current.body[2], frozen.body[9])
    frozen_tenure = frozen.body[15]
    assert isinstance(frozen_tenure, ast.FunctionDef)
    current_tenure = definitions["_commit_frozen_tenure_prefixes"]
    assert len(current_tenure.body) == len(frozen_tenure.body)
    for observed, expected in zip(current_tenure.body, frozen_tenure.body, strict=True):
        _same(observed, expected)
    expected_calls = (
        "_frozen_cleanup_scope",
        "_commit_frozen_symbol_cleanup",
        "_commit_frozen_exit_events",
        "_commit_frozen_tenure_prefixes",
        "_commit_frozen_recovery_exit",
        "_commit_frozen_tenure_prefixes",
    )
    calls = [
        node.func.id
        for statement in current.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in expected_calls
    ]
    assert tuple(calls) == expected_calls


def expand_portfolio_allocator_method(
    *,
    root: Path,
    relative: str,
    name: str,
    candidate: ast.FunctionDef | None,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Expand one exact reviewed portfolio owner to the governance anchor AST."""
    overrides = architecture_portfolio_reviewed_sources(root=root, overrides=overrides)
    frozen_source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:{relative}"],
        cwd=root,
        text=True,
    )
    frozen = _definitions(frozen_source)[name]
    definitions = _definitions(_source(root, relative, overrides))
    current = definitions[name]
    if candidate is not None:
        _same(candidate, current)
    if _dump(current) == _dump(frozen):
        return copy.deepcopy(frozen)
    if name == "allocate":
        _expand_allocator(current, definitions, frozen)
    elif name in {"_risk_lifecycle_rank", "_subset_retention_vector"}:
        _expand_typed_tuple_owner(name, current, definitions, frozen)
    elif name == "_sparse_risk_reduce":
        _expand_sparse(current, definitions, frozen)
    else:
        assert name == "_commit_frozen_exit_state"
        _expand_freeze(current, definitions, frozen)
    return copy.deepcopy(frozen)


def architecture_portfolio_type_ignore_projection(
    *,
    root: Path,
    observed: Set[str],
    expected: Set[str],
) -> set[str]:
    """Project three frozen ignores only after exact typed-owner expansion."""
    assert observed == set()
    frozen_source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:uquant/portfolio/risk_reduction.py"],
        cwd=root,
        text=True,
    )
    for identifier in expected:
        statement = identifier.split(":", 2)[2].rsplit(":", 1)[0]
        assert statement in frozen_source
    for name in ("_risk_lifecycle_rank", "_subset_retention_vector", "_sparse_risk_reduce"):
        expand_portfolio_allocator_method(
            root=root,
            relative="uquant/portfolio/risk_reduction.py",
            name=name,
            candidate=None,
        )
    return set(expected)
