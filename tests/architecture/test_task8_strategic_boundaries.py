from __future__ import annotations

import ast
import copy
import inspect
import subprocess

from ._analysis import (
    _TASK8_RELOCATED_FUNCTION_DEBT,
    _TASK8_RELOCATED_PRIVATE_IMPORTS,
    ROOT,
    architecture_snapshot,
    measured_debt,
)

_TASK8_START = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_CHECKPOINT3_OWNER_METHODS = {
    "uquant/portfolio/strategic/discovery.py": ("_initialize_strategic_cohort",),
    "uquant/portfolio/strategic/lifecycle.py": (
        "_bounded_strategic_restore_risk_open",
        "_retire_strategic_member",
        "_strategic_cohort_targets",
    ),
}
_CHECKPOINT3_TARGET_HELPERS = {
    "_strategic_completed_exit_targets",
    "_strategic_active_targets",
}
_CHECKPOINT3_PACKAGE_PATHS = (
    "uquant/portfolio/strategic/__init__.py",
    "uquant/portfolio/strategic/discovery.py",
    "uquant/portfolio/strategic/lifecycle.py",
    "uquant/portfolio/strategic/targets.py",
)


def _git_source(path: str) -> str:
    return subprocess.run(
        ["git", "show", f"{_TASK8_START}:{path}"],
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


def _immutable_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_git_source("uquant/portfolio_strategic.py"))
    policy = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StrategicPortfolioPolicy"
    )
    return {
        node.name: node for node in policy.body if isinstance(node, ast.FunctionDef)
    }


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


def _delegation(return_: ast.Return) -> str | None:
    call = return_.value
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _target_returns(node: ast.FunctionDef) -> tuple[ast.Return, ast.Return]:
    returns = [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Return)
        and isinstance(candidate.value, ast.Call)
        and isinstance(candidate.value.func, ast.Attribute)
        and isinstance(candidate.value.func.value, ast.Name)
        and candidate.value.func.value.id == "self"
        and candidate.value.func.attr == "_targets"
    ]
    assert len(returns) == 2
    completed = next(
        candidate
        for candidate in returns
        if any(
            keyword.arg == "proposed" and isinstance(keyword.value, ast.Dict)
            for keyword in candidate.value.keywords  # type: ignore[union-attr]
        )
    )
    active = next(candidate for candidate in returns if candidate is not completed)
    return completed, active


def test_task8_checkpoint3_strategic_owners_and_thin_facade_are_complete() -> None:
    assert all((ROOT / path).is_file() for path in _CHECKPOINT3_PACKAGE_PATHS)
    facade = ast.parse((ROOT / "uquant/portfolio_strategic.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in facade.body)
    imports = {
        alias.name
        for node in facade.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports == {"StrategicPortfolioPolicy"}


def test_task8_checkpoint3_strategic_methods_and_target_slices_are_ast_exact() -> None:
    immutable = _immutable_methods()
    discovery = _function_nodes(
        (ROOT / "uquant/portfolio/strategic/discovery.py").read_text(encoding="utf-8")
    )
    lifecycle = _function_nodes(
        (ROOT / "uquant/portfolio/strategic/lifecycle.py").read_text(encoding="utf-8")
    )
    for name in (
        "_initialize_strategic_cohort",
        "_bounded_strategic_restore_risk_open",
        "_retire_strategic_member",
    ):
        candidate = discovery[name] if name in discovery else lifecycle[name]
        assert _normalized_method(candidate) == _normalized_method(immutable[name])

    candidate_main = copy.deepcopy(lifecycle["_strategic_cohort_targets"])
    delegations = {
        _delegation(return_): return_
        for return_ in ast.walk(candidate_main)
        if isinstance(return_, ast.Return) and _delegation(return_) is not None
    }
    assert set(delegations) == _CHECKPOINT3_TARGET_HELPERS
    expected_keywords = {
        "_strategic_completed_exit_targets": ("self", "leaders", "account"),
        "_strategic_active_targets": (
            "self",
            "proposed",
            "leaders",
            "account",
            "dominant_profit_lock_armed_now",
            "dominant_symbol",
            "current_selected",
        ),
    }
    for name, return_ in delegations.items():
        assert name is not None
        call = return_.value
        assert isinstance(call, ast.Call)
        assert tuple(keyword.arg for keyword in call.keywords) == expected_keywords[name]
        assert all(
            isinstance(keyword.value, ast.Name) and keyword.value.id == keyword.arg
            for keyword in call.keywords
        )

    immutable_completed, immutable_active = _target_returns(
        immutable["_strategic_cohort_targets"]
    )
    targets = _function_nodes(
        (ROOT / "uquant/portfolio/strategic/targets.py").read_text(encoding="utf-8")
    )
    assert set(targets) == _CHECKPOINT3_TARGET_HELPERS
    assert len(targets["_strategic_completed_exit_targets"].body) == 1
    assert len(targets["_strategic_active_targets"].body) == 1
    assert ast.dump(
        targets["_strategic_completed_exit_targets"].body[0],
        include_attributes=False,
    ) == ast.dump(immutable_completed, include_attributes=False)
    assert ast.dump(
        targets["_strategic_active_targets"].body[0], include_attributes=False
    ) == ast.dump(immutable_active, include_attributes=False)

    class ExpandTargets(ast.NodeTransformer):
        def visit_Return(self, node: ast.Return) -> ast.Return:
            name = _delegation(node)
            if name == "_strategic_completed_exit_targets":
                return copy.deepcopy(immutable_completed)
            if name == "_strategic_active_targets":
                return copy.deepcopy(immutable_active)
            return node

    expanded = ExpandTargets().visit(candidate_main)
    assert isinstance(expanded, ast.FunctionDef)
    assert _normalized_method(expanded) == _normalized_method(
        immutable["_strategic_cohort_targets"]
    )


def test_task8_checkpoint3_ast_gate_rejects_strategic_rule_mutations() -> None:
    immutable = _immutable_methods()
    threshold = copy.deepcopy(immutable["_initialize_strategic_cohort"])
    numeric = next(
        node
        for node in ast.walk(threshold)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    )
    numeric.value = float(numeric.value) + 0.01
    assert _normalized_method(threshold) != _normalized_method(
        immutable["_initialize_strategic_cohort"]
    )

    main = immutable["_strategic_cohort_targets"]
    comparison = copy.deepcopy(main)
    compare = next(node for node in ast.walk(comparison) if isinstance(node, ast.Compare))
    compare.ops[0] = ast.Gt() if not isinstance(compare.ops[0], ast.Gt) else ast.Lt()
    assert _normalized_method(comparison) != _normalized_method(main)

    boolean = copy.deepcopy(main)
    bool_op = next(node for node in ast.walk(boolean) if isinstance(node, ast.BoolOp))
    bool_op.op = ast.Or() if isinstance(bool_op.op, ast.And) else ast.And()
    assert _normalized_method(boolean) != _normalized_method(main)

    mutation_order = copy.deepcopy(main)
    body_index = next(
        index
        for index, statement in enumerate(mutation_order.body[:-1])
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr))
        and isinstance(
            mutation_order.body[index + 1], (ast.Assign, ast.AnnAssign, ast.Expr)
        )
    )
    mutation_order.body[body_index], mutation_order.body[body_index + 1] = (
        mutation_order.body[body_index + 1],
        mutation_order.body[body_index],
    )
    assert _normalized_method(mutation_order) != _normalized_method(main)


def test_task8_checkpoint3_private_and_complexity_relocations_are_closed() -> None:
    expected = {
        f"{path.removesuffix('.py').replace('/', '.')}:{name}"
        for path, names in _CHECKPOINT3_OWNER_METHODS.items()
        for name in names
    }
    observed = {
        identifier: legacy
        for identifier, legacy in _TASK8_RELOCATED_FUNCTION_DEBT.items()
        if legacy.startswith("uquant.portfolio_strategic:")
    }
    assert set(observed) == expected
    assert set(observed.values()) == {
        f"uquant.portfolio_strategic:StrategicPortfolioPolicy.{name}"
        for names in _CHECKPOINT3_OWNER_METHODS.values()
        for name in names
    }

    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    assert {
        str(row["id"]) for row in graph["task8_relocated_private_imports"]
    } == _TASK8_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if str(row["importer"]).startswith("uquant.portfolio.strategic")
        or str(row["imported_from"]).startswith("uquant.portfolio.strategic")
    }

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/portfolio/strategic/discovery.py"] += (
        "\nfrom .lifecycle import _unreviewed_strategic_edge\n\n"
        "def _unreviewed_strategic_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert (
        "uquant.portfolio.strategic.discovery:"
        "uquant.portfolio.strategic.lifecycle:_unreviewed_strategic_edge"
    ) in {str(row["id"]) for row in mutation_graph["cross_module_private_imports"]}
    assert "uquant.portfolio_strategic:_unreviewed_strategic_debt" in {
        str(row["id"]) for row in measured_debt(mutation)["long_functions"]
    }
