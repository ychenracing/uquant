"""Verify explicit method forwarding without requiring runtime method injection."""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from collections.abc import Callable
from typing import Any


def assert_explicit_delegation(function: Callable[..., Any], target: Callable[..., Any]) -> None:
    """Require identical parameter semantics and one exact, side-effect-free call."""
    if function is target:
        return
    definition = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
    assert isinstance(definition, ast.FunctionDef)
    assert_explicit_delegation_source(definition, function, target)


def assert_explicit_delegation_source(
    definition: ast.FunctionDef, function: Callable[..., Any], target: Callable[..., Any]
) -> None:
    signature = inspect.signature(function)
    expected = inspect.signature(target)
    def without_self_annotation(value: inspect.Signature) -> inspect.Signature:
        return value.replace(parameters=[
            parameter.replace(annotation=inspect.Parameter.empty)
            if name == "self" else parameter
            for name, parameter in value.parameters.items()
        ])
    assert without_self_annotation(signature) == without_self_annotation(expected)
    body = list(definition.body)
    if ast.get_docstring(definition) is not None:
        body.pop(0)
    bindings = dict(function.__globals__)
    if body and isinstance(body[0], ast.ImportFrom):
        statement = body.pop(0)
        assert len(statement.names) == 1
        name = statement.names[0]
        assert name.name != "*"
        package = function.__module__.rpartition(".")[0]
        module = importlib.import_module("." * statement.level + (statement.module or ""), package)
        bindings[name.asname or name.name] = getattr(module, name.name)
    assert len(body) == 1 and isinstance(body[0], ast.Return)
    call = body[0].value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    assert bindings[call.func.id] is target
    assert not signature.parameters.get("args") and not signature.parameters.get("kwargs")
    values = {name: object() for name in signature.parameters}
    assert all(isinstance(argument, ast.Name) for argument in call.args)
    assert all(keyword.arg is not None and isinstance(keyword.value, ast.Name) for keyword in call.keywords)
    arguments = expected.bind(
        *(values[argument.id] for argument in call.args),
        **{keyword.arg: values[keyword.value.id] for keyword in call.keywords},
    )
    # Defaults may never hide a missing forward or swap two equally typed inputs.
    assert set(arguments.arguments) == set(values)
    assert all(arguments.arguments[name] is value for name, value in values.items())
