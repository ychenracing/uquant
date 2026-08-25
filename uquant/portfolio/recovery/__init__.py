"""Assemble the historical recovery policy from explicit owners."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import Any, cast

from .admission import RecoveryPortfolioPolicy as RecoveryPortfolioPolicy
from .substitution import recovery_anchor_substitution as _recovery_anchor_substitution


def _recovery_compatibility_method[Function: Callable[..., Any]](
    function: Function, name: str
) -> Function:
    runtime_function = cast(FunctionType, function)
    raw_docstring = runtime_function.__doc__
    if isinstance(raw_docstring, str) and "\n" in raw_docstring:
        first, *remaining = raw_docstring.split("\n")
        runtime_function.__doc__ = "\n".join(
            [first, *(f"    {line}" if line else line for line in remaining)]
        )
    annotations = dict(runtime_function.__annotations__)
    annotations.pop("self", None)
    runtime_function.__annotations__ = annotations
    runtime_function.__module__ = "uquant.portfolio_recovery"
    runtime_function.__qualname__ = f"RecoveryPortfolioPolicy.{name}"
    return function


def _bind_recovery_compatibility_method[Function: Callable[..., Any]](
    name: str, function: Function
) -> None:
    compatible = _recovery_compatibility_method(function, name)
    setattr(RecoveryPortfolioPolicy, name, compatible)


_bind_recovery_compatibility_method(
    "_recovery_anchor_substitution", _recovery_anchor_substitution
)
