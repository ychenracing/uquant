from __future__ import annotations

import ast
import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class StageSlice:
    path: str
    source_start: int
    source_stop: int
    terminal_constructor: str | None = None
    terminal_fields: tuple[tuple[str, str], ...] = ()
    terminal_expression: str | None = None
    transport: str | None = None


@dataclass(frozen=True)
class OrchestrationCall:
    index: int
    result: str
    keywords: tuple[tuple[str, str], ...]
    post_statements: tuple[str, ...]


def same_fields(names: str) -> tuple[tuple[str, str], ...]:
    return tuple((name, name) for name in names.split())


def same_keywords(names: str) -> tuple[tuple[str, str], ...]:
    return tuple((name, name) for name in names.split())


def field_unpacks(
    result: str,
    fields: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    return tuple(f"{local} = {result}.{field}" for local, field in fields)


def ast_dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def parsed_expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def parsed_statement(source: str) -> ast.stmt:
    body = ast.parse(source).body
    assert len(body) == 1
    return body[0]


def _expected_terminal(spec: StageSlice) -> ast.expr:
    if spec.terminal_constructor is not None:
        return ast.Call(
            func=ast.Name(id=spec.terminal_constructor, ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(arg=field, value=ast.Name(id=local, ctx=ast.Load()))
                for field, local in spec.terminal_fields
            ],
        )
    assert spec.terminal_expression is not None
    return parsed_expression(spec.terminal_expression)


def normalized_stage_statements(
    function: ast.FunctionDef,
    spec: StageSlice,
) -> list[ast.stmt]:
    """Remove only the declared stage-boundary transport from an owner slice."""
    assert (
        function.body
        and isinstance(function.body[0], ast.Expr)
        and isinstance(function.body[0].value, ast.Constant)
        and isinstance(function.body[0].value.value, str)
    )
    terminal = function.body[-1]
    assert isinstance(terminal, ast.Return) and terminal.value is not None
    assert ast_dump(terminal.value) == ast_dump(_expected_terminal(spec))

    statements = copy.deepcopy(function.body[1:-1])
    transport_count = 0
    for statement in statements:
        for node in ast.walk(statement):
            if (
                spec.transport == "live_anchor_callee"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "update_dynamic_anchors"
            ):
                node.func.id = "_update_dynamic_anchors"
                transport_count += 1
            elif (
                spec.transport == "operating_drawdown_parameter"
                and isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id == "operating_drawdown"
            ):
                node.id = "operating_dd"
                transport_count += 1
    assert transport_count == (1 if spec.transport is not None else 0)
    return statements


def assert_stage_call(
    *,
    body: list[ast.stmt],
    stage_name: str,
    spec: OrchestrationCall,
) -> set[int]:
    statement = body[spec.index]
    assert isinstance(statement, ast.Assign) and len(statement.targets) == 1
    target = statement.targets[0]
    assert isinstance(target, ast.Name) and target.id == spec.result
    call = statement.value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == stage_name
    assert call.args == []
    actual_keywords = tuple((keyword.arg, ast_dump(keyword.value)) for keyword in call.keywords)
    expected_keywords = tuple(
        (keyword, ast_dump(parsed_expression(expression))) for keyword, expression in spec.keywords
    )
    assert actual_keywords == expected_keywords

    covered = {spec.index}
    for offset, expected in enumerate(spec.post_statements, start=1):
        index = spec.index + offset
        assert ast_dump(body[index]) == ast_dump(parsed_statement(expected))
        covered.add(index)
    return covered


__all__ = [
    "OrchestrationCall",
    "StageSlice",
    "assert_stage_call",
    "ast_dump",
    "field_unpacks",
    "normalized_stage_statements",
    "same_fields",
    "same_keywords",
]
