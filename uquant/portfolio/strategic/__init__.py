"""Assemble the historical strategic policy from explicit owners."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import Any, cast

from .discovery import (
    StrategicPortfolioPolicy as StrategicPortfolioPolicy,
)
from .discovery import (
    _initialize_strategic_cohort,
)
from .lifecycle import (
    _bounded_strategic_restore_risk_open,
    _retire_strategic_member,
    _strategic_cohort_targets,
)


def _strategic_compatibility_method[Function: Callable[..., Any]](
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
    runtime_function.__module__ = "uquant.portfolio_strategic"
    runtime_function.__qualname__ = f"StrategicPortfolioPolicy.{name}"
    return function


def _bind_strategic_compatibility_method[Function: Callable[..., Any]](
    name: str,
    function: Function,
    *,
    static: bool = False,
) -> None:
    compatible = _strategic_compatibility_method(function, name)
    descriptor: object = staticmethod(compatible) if static else compatible
    setattr(StrategicPortfolioPolicy, name, descriptor)


_bind_strategic_compatibility_method(
    "_bounded_strategic_restore_risk_open", _bounded_strategic_restore_risk_open
)
_bind_strategic_compatibility_method(
    "_retire_strategic_member", _retire_strategic_member, static=True
)
_bind_strategic_compatibility_method(
    "_initialize_strategic_cohort", _initialize_strategic_cohort
)
_bind_strategic_compatibility_method(
    "_strategic_cohort_targets", _strategic_cohort_targets
)
