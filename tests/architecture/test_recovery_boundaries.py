from __future__ import annotations

import ast
import copy
import inspect
import subprocess

import pytest

from ._analysis import (
    _PORTFOLIO_ALLOCATE_STRATEGY_DEBT,
    _PORTFOLIO_RELOCATED_FUNCTION_DEBT,
    _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._owner_transport import (
    expand_architecture_portfolio_pipeline,
    architecture_private_relocation_projection,
)
from ._reviewed_owner_transport import expand_reviewed_architecture_owner

_PORTFOLIO_REFERENCE_COMMIT = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_RECOVERY_PACKAGE_PATHS = (
    "uquant/portfolio/recovery/__init__.py",
    "uquant/portfolio/recovery/admission.py",
    "uquant/portfolio/recovery/substitution.py",
    "uquant/portfolio/recovery/targets.py",
)
_ADMISSION_TARGET_HELPERS = {
    "_overextended_pullback_targets": ("self", "leaders", "account"),
    "_controlled_oversold_rebound_targets": (
        "self",
        "pick",
        "risk",
        "leaders",
        "account",
    ),
    "_locked_recovery_cohort_targets": (
        "self",
        "proposed",
        "leaders",
        "account",
    ),
    "_awaiting_recovery_cohort_targets": (
        "self",
        "anchored_held",
        "leaders",
        "account",
    ),
    "_recovery_cohort_targets": (
        "self",
        "proposed",
        "leaders",
        "account",
        "capped",
        "cohort_changed",
    ),
}
_SUBSTITUTION_TARGET_HELPERS = {
    "_pending_recovery_substitution_targets": (
        "self",
        "proposed",
        "leaders",
        "account",
        "structured_replacements",
    ),
    "_confirmed_recovery_substitution_targets": (
        "self",
        "proposed",
        "leaders",
        "account",
        "incumbent",
        "challenger",
    ),
}
_PIPELINE_ARGUMENTS = (
    "self",
    "date",
    "opportunity",
    "risk",
    "user_panel",
    "leaders",
    "account",
    "prices",
    "weights_now",
    "anchored_held",
    "bounded_recovery_repair",
    "freeze_active",
    "general_core_symbols",
    "level1_recovery_repair",
    "risk_neutral_recovery_handoff",
    "risk_neutral_recovery_transfer",
    "tactical_recovery_market",
    "transitional_recovery_market",
    "weak_secular_market",
)


def _git_source(path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{_PORTFOLIO_REFERENCE_COMMIT}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _function_nodes(source: str) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }


def _immutable_method(
    path: str,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef:
    tree = ast.parse(_git_source(path))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _normalized_method(node: ast.FunctionDef) -> str:
    normalized = copy.deepcopy(node)
    normalized.decorator_list = []
    if normalized.args.args and normalized.args.args[0].arg == "self":
        normalized.args.args[0].annotation = None
    if (
        normalized.body
        and isinstance(normalized.body[0], ast.Expr)
        and isinstance(normalized.body[0].value, ast.Constant)
        and isinstance(normalized.body[0].value.value, str)
    ):
        normalized.body[0].value.value = inspect.cleandoc(
            normalized.body[0].value.value
        )
    return ast.dump(normalized, include_attributes=False)


def _immutable_admission_slice() -> list[ast.stmt]:
    allocation = _immutable_method(
        "uquant/portfolio.py", "PortfolioAllocator", "_allocate_strategy"
    )
    selected = allocation.body[78:82]
    assert [(node.lineno, node.end_lineno) for node in selected] == [
        (1975, 1975),
        (1976, 1992),
        (1994, 2260),
        (2262, 2586),
    ]
    return selected


def _target_calls(nodes: list[ast.stmt]) -> dict[int, ast.Call]:
    calls = {
        candidate.lineno: candidate.value
        for statement in nodes
        for candidate in ast.walk(statement)
        if isinstance(candidate, ast.Return)
        and isinstance(candidate.value, ast.Call)
        and isinstance(candidate.value.func, ast.Attribute)
        and isinstance(candidate.value.func.value, ast.Name)
        and candidate.value.func.value.id == "self"
        and candidate.value.func.attr == "_targets"
    }
    assert set(calls) == {2102, 2247, 2300, 2404, 2568}
    return calls


def _delegation(node: ast.AST) -> str | None:
    value: ast.AST | None = None
    if isinstance(node, (ast.Assign, ast.Return)):
        value = node.value
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id
    return None


def _assert_delegation_arguments(
    functions: list[ast.stmt], expected: dict[str, tuple[str, ...]]
) -> None:
    observed: dict[str, ast.Call] = {}
    for statement in functions:
        for node in ast.walk(statement):
            name = _delegation(node)
            if name in expected:
                value = node.value
                assert isinstance(value, ast.Call)
                observed[name] = value
    assert set(observed) == set(expected)
    for name, call in observed.items():
        assert not call.args
        assert tuple(keyword.arg for keyword in call.keywords) == expected[name]
        assert all(
            isinstance(keyword.value, ast.Name) and keyword.value.id == keyword.arg
            for keyword in call.keywords
        )


def _statements_dump(nodes: list[ast.stmt]) -> str:
    return ast.dump(
        ast.Module(body=nodes, type_ignores=[]), include_attributes=False
    )


def _assert_pipeline_handoff(pipeline: ast.FunctionDef) -> None:
    index = next(
        index
        for index, statement in enumerate(pipeline.body)
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "_recovery_admission_targets"
    )
    assignment = pipeline.body[index]
    assert isinstance(assignment, ast.Assign)
    assert [ast.unparse(target) for target in assignment.targets] == [
        "recovery_admission_targets"
    ]
    call = assignment.value
    assert isinstance(call, ast.Call)
    assert not call.args
    assert tuple(keyword.arg for keyword in call.keywords) == _PIPELINE_ARGUMENTS
    assert all(
        isinstance(keyword.value, ast.Name) and keyword.value.id == keyword.arg
        for keyword in call.keywords
    )
    short_circuit = pipeline.body[index + 1]
    assert ast.unparse(short_circuit) == (
        "if recovery_admission_targets is not None:\n"
        "    return recovery_admission_targets"
    )


def test_portfolio_recovery_owners_and_thin_facade_are_complete() -> None:
    assert all((ROOT / path).is_file() for path in _RECOVERY_PACKAGE_PATHS)
    facade = ast.parse((ROOT / "uquant/portfolio_recovery.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in facade.body
    )
    imports = {
        alias.name
        for node in facade.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports == {"RecoveryPortfolioPolicy"}


def test_portfolio_recovery_substitution_method_and_targets_are_ast_exact() -> None:
    immutable = _immutable_method(
        "uquant/portfolio_recovery.py",
        "RecoveryPortfolioPolicy",
        "_recovery_anchor_substitution",
    )
    candidate = expand_reviewed_architecture_owner(
        root=ROOT,
        relative="uquant/portfolio/recovery/substitution.py",
        name="_recovery_anchor_substitution",
        candidate=None,
    )
    targets = _function_nodes(
        (ROOT / "uquant/portfolio/recovery/targets.py").read_text(encoding="utf-8")
    )
    immutable_pending = next(
        node.value
        for node in ast.walk(immutable)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "_targets"
    )
    immutable_confirmed = next(
        node.value
        for node in ast.walk(immutable)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "targets" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "_targets"
    )
    assert ast.dump(
        targets["_pending_recovery_substitution_targets"].body[0].value,
        include_attributes=False,
    ) == ast.dump(immutable_pending, include_attributes=False)
    assert ast.dump(
        targets["_confirmed_recovery_substitution_targets"].body[0].value,
        include_attributes=False,
    ) == ast.dump(immutable_confirmed, include_attributes=False)
    _assert_delegation_arguments(candidate.body, _SUBSTITUTION_TARGET_HELPERS)

    class ExpandTargets(ast.NodeTransformer):
        def visit_Return(self, node: ast.Return) -> ast.Return:
            if _delegation(node) == "_pending_recovery_substitution_targets":
                return ast.Return(value=copy.deepcopy(immutable_pending))
            return self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> ast.Assign:
            if _delegation(node) == "_confirmed_recovery_substitution_targets":
                expanded = copy.deepcopy(node)
                expanded.value = copy.deepcopy(immutable_confirmed)
                return expanded
            return self.generic_visit(node)

    expanded = ExpandTargets().visit(candidate)
    assert isinstance(expanded, ast.FunctionDef)
    assert _normalized_method(expanded) == _normalized_method(immutable)


def test_portfolio_recovery_admission_slice_and_target_builders_are_ast_exact() -> None:
    immutable_slice = _immutable_admission_slice()
    immutable_targets = _target_calls(immutable_slice)
    admission = expand_reviewed_architecture_owner(
        root=ROOT,
        relative="uquant/portfolio/recovery/admission.py",
        name="_recovery_admission_targets",
        candidate=None,
    )
    targets = _function_nodes(
        (ROOT / "uquant/portfolio/recovery/targets.py").read_text(encoding="utf-8")
    )
    target_lines = {
        "_overextended_pullback_targets": 2102,
        "_controlled_oversold_rebound_targets": 2247,
        "_locked_recovery_cohort_targets": 2300,
        "_awaiting_recovery_cohort_targets": 2404,
        "_recovery_cohort_targets": 2568,
    }
    for name, line in target_lines.items():
        body = targets[name].body
        assert len(body) == 1 and isinstance(body[0], ast.Return)
        assert ast.dump(body[0].value, include_attributes=False) == ast.dump(
            immutable_targets[line], include_attributes=False
        )
    _assert_delegation_arguments(admission.body, _ADMISSION_TARGET_HELPERS)

    class ExpandTargets(ast.NodeTransformer):
        def visit_Return(self, node: ast.Return) -> ast.Return:
            name = _delegation(node)
            if name in target_lines:
                return ast.Return(
                    value=copy.deepcopy(immutable_targets[target_lines[name]])
                )
            return self.generic_visit(node)

    assert isinstance(admission.body[-1], ast.Return)
    assert isinstance(admission.body[-1].value, ast.Constant)
    assert admission.body[-1].value.value is None
    expanded = ExpandTargets().visit(admission)
    assert isinstance(expanded, ast.FunctionDef)
    expanded_body = expanded.body[:-1]
    assert ast.dump(
        ast.Module(body=expanded_body, type_ignores=[]), include_attributes=False
    ) == ast.dump(
        ast.Module(body=copy.deepcopy(immutable_slice), type_ignores=[]),
        include_attributes=False,
    )


def test_portfolio_recovery_pipeline_pins_admission_args_unpack_and_return() -> None:
    pipeline = expand_architecture_portfolio_pipeline(root=ROOT, candidate=None)
    _assert_pipeline_handoff(pipeline)


def test_portfolio_recovery_ast_gate_rejects_recovery_rule_mutations() -> None:
    substitution = _immutable_method(
        "uquant/portfolio_recovery.py",
        "RecoveryPortfolioPolicy",
        "_recovery_anchor_substitution",
    )
    threshold = copy.deepcopy(substitution)
    numeric = next(
        node
        for node in ast.walk(threshold)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    )
    numeric.value = float(numeric.value) + 0.01
    assert _normalized_method(threshold) != _normalized_method(substitution)

    immutable_slice = _immutable_admission_slice()
    comparison = copy.deepcopy(immutable_slice)
    compare = next(
        node
        for statement in comparison
        for node in ast.walk(statement)
        if isinstance(node, ast.Compare)
    )
    compare.ops[0] = ast.Gt() if not isinstance(compare.ops[0], ast.Gt) else ast.Lt()
    assert _statements_dump(comparison) != _statements_dump(immutable_slice)

    boolean = copy.deepcopy(immutable_slice)
    bool_op = next(
        node
        for statement in boolean
        for node in ast.walk(statement)
        if isinstance(node, ast.BoolOp)
    )
    bool_op.op = ast.Or() if isinstance(bool_op.op, ast.And) else ast.And()
    assert _statements_dump(boolean) != _statements_dump(immutable_slice)

    sort_key = copy.deepcopy(immutable_slice)
    sorted_call = next(
        node
        for statement in sort_key
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
        and any(keyword.arg == "key" for keyword in node.keywords)
    )
    key = next(keyword for keyword in sorted_call.keywords if keyword.arg == "key")
    key.value = ast.Lambda(
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="item")],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=ast.Constant(value=0),
    )
    assert _statements_dump(sort_key) != _statements_dump(immutable_slice)

    statement_order = copy.deepcopy(immutable_slice)
    statement_order[0], statement_order[1] = statement_order[1], statement_order[0]
    assert _statements_dump(statement_order) != _statements_dump(immutable_slice)

    mutated_admission = expand_reviewed_architecture_owner(
        root=ROOT,
        relative="uquant/portfolio/recovery/admission.py",
        name="_recovery_admission_targets",
        candidate=None,
    )
    helper_call = next(
        node
        for node in ast.walk(mutated_admission)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _ADMISSION_TARGET_HELPERS
    )
    helper_call.keywords[0], helper_call.keywords[1] = (
        helper_call.keywords[1],
        helper_call.keywords[0],
    )
    with pytest.raises(AssertionError):
        _assert_delegation_arguments(
            mutated_admission.body, _ADMISSION_TARGET_HELPERS
        )

    pipeline = expand_architecture_portfolio_pipeline(root=ROOT, candidate=None)
    mutated_pipeline = copy.deepcopy(pipeline)
    stage_call = next(
        node
        for node in ast.walk(mutated_pipeline)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_recovery_admission_targets"
    )
    stage_call.keywords[0], stage_call.keywords[1] = (
        stage_call.keywords[1],
        stage_call.keywords[0],
    )
    with pytest.raises(AssertionError):
        _assert_pipeline_handoff(mutated_pipeline)


def test_portfolio_recovery_private_and_complexity_relocations_are_closed() -> None:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    assert architecture_private_relocation_projection(
        root=ROOT,
        task=8,
        observed={str(row["id"]) for row in graph["task8_relocated_private_imports"]},
        expected=set(_PORTFOLIO_RELOCATED_PRIVATE_IMPORTS),
    ) == _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if str(row["importer"]).startswith("uquant.portfolio.recovery")
        or str(row["imported_from"]).startswith("uquant.portfolio.recovery")
    }
    recovery_functions = {
        identifier: legacy
        for identifier, legacy in _PORTFOLIO_RELOCATED_FUNCTION_DEBT.items()
        if legacy.startswith("uquant.portfolio_recovery:")
        or (
            legacy == "uquant.portfolio:PortfolioAllocator._allocate_strategy"
            and identifier.startswith("uquant.portfolio.recovery")
        )
    }
    assert set(recovery_functions) == {
        "uquant.portfolio.recovery.admission:_recovery_admission_targets",
        "uquant.portfolio.recovery.substitution:_recovery_anchor_substitution",
        *{
            f"uquant.portfolio.recovery.targets:{name}"
            for name in _ADMISSION_TARGET_HELPERS | _SUBSTITUTION_TARGET_HELPERS
        },
    }
    assert {
        "uquant.portfolio.pipeline:_allocate_strategy",
        "uquant.portfolio.recovery.admission:_recovery_admission_targets",
        *{
            f"uquant.portfolio.recovery.targets:{name}"
            for name in _ADMISSION_TARGET_HELPERS
        },
    } == _PORTFOLIO_ALLOCATE_STRATEGY_DEBT

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/portfolio/recovery/admission.py"] += (
        "\nfrom .substitution import _unreviewed_recovery_edge\n\n"
        "def _unreviewed_recovery_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert (
        "uquant.portfolio.recovery.admission:"
        "uquant.portfolio.recovery.substitution:_unreviewed_recovery_edge"
    ) in {
        str(row["id"])
        for row in mutation_graph["cross_module_private_imports"]
    }
    assert "uquant.portfolio_recovery:_unreviewed_recovery_debt" in {
        str(row["id"]) for row in measured_debt(mutation)["long_functions"]
    }
