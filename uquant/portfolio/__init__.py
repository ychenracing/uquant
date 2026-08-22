"""The only portfolio allocator: alpha and risk never submit orders directly."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import Any, cast

from ..portfolio_core import current_weights, effective_n
from .allocator import PortfolioAllocator, _confirmed_recovery_gross, allocate
from .freeze import _commit_frozen_exit_state, _frozen_existing_targets
from .pipeline import _allocate_strategy
from .risk_reduction import (
    _risk_attribution_mechanism,
    _risk_lifecycle_rank,
    _risk_reduction_metadata,
    _risk_retention_score,
    _risk_retention_vector,
    _sparse_risk_reduce,
    _subset_retention_vector,
    _turnover_aware_sector_cap,
)


def _compatibility_method[Function: Callable[..., Any]](
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
    runtime_function.__module__ = "uquant.portfolio"
    runtime_function.__qualname__ = f"PortfolioAllocator.{name}"
    return function


def _bind_compatibility_method[Function: Callable[..., Any]](
    name: str,
    function: Function,
    *,
    static: bool = False,
) -> None:
    compatible = _compatibility_method(function, name)
    descriptor: object = staticmethod(compatible) if static else compatible
    setattr(PortfolioAllocator, name, descriptor)


_bind_compatibility_method(
    "_confirmed_recovery_gross",
    _confirmed_recovery_gross,
)
_bind_compatibility_method(
    "_risk_attribution_mechanism",
    _risk_attribution_mechanism,
    static=True,
)
_bind_compatibility_method(
    "_risk_retention_score",
    _risk_retention_score,
)
_bind_compatibility_method(
    "_risk_retention_vector",
    _risk_retention_vector,
    static=True,
)
_bind_compatibility_method(
    "_risk_lifecycle_rank",
    _risk_lifecycle_rank,
    static=True,
)
_bind_compatibility_method(
    "_subset_retention_vector",
    _subset_retention_vector,
)
_bind_compatibility_method(
    "_sparse_risk_reduce",
    _sparse_risk_reduce,
)
_bind_compatibility_method(
    "_risk_reduction_metadata",
    _risk_reduction_metadata,
    static=True,
)
_bind_compatibility_method(
    "_turnover_aware_sector_cap",
    _turnover_aware_sector_cap,
)
_bind_compatibility_method(
    "allocate",
    allocate,
)
_bind_compatibility_method(
    "_commit_frozen_exit_state",
    _commit_frozen_exit_state,
    static=True,
)
_bind_compatibility_method(
    "_frozen_existing_targets",
    _frozen_existing_targets,
    static=True,
)
_bind_compatibility_method(
    "_allocate_strategy",
    _allocate_strategy,
)

__all__ = ["PortfolioAllocator", "current_weights", "effective_n"]
