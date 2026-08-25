"""The only portfolio allocator: alpha and risk never submit orders directly."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import TYPE_CHECKING, Any, cast

from ..portfolio_core import current_weights, effective_n


def _compatibility_method[Function: Callable[..., Any]](function: Function, name: str) -> Function:
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
    owner: type[Any],
    name: str,
    function: Function,
    *,
    static: bool = False,
) -> None:
    compatible = _compatibility_method(function, name)
    descriptor: object = staticmethod(compatible) if static else compatible
    setattr(owner, name, descriptor)


def _load_allocator() -> type[Any]:
    # This remains the one eager assembly step. Keeping the imports inside it
    # lets the historical leader facade load its nested owner package while
    # the parent portfolio package is still initializing.
    from .allocator import (
        PortfolioAllocator as owner,
    )
    from .allocator import (
        allocate,
    )
    from .allocator import (
        confirmed_recovery_gross as _confirmed_recovery_gross,
    )
    from .freeze import commit_frozen_exit_state as _commit_frozen_exit_state
    from .freeze import frozen_existing_targets as _frozen_existing_targets
    from .pipeline import allocate_strategy as _allocate_strategy
    from .risk_reduction import (
        risk_attribution_mechanism as _risk_attribution_mechanism,
    )
    from .risk_reduction import (
        risk_lifecycle_rank as _risk_lifecycle_rank,
    )
    from .risk_reduction import (
        risk_reduction_metadata as _risk_reduction_metadata,
    )
    from .risk_reduction import (
        risk_retention_score as _risk_retention_score,
    )
    from .risk_reduction import (
        risk_retention_vector as _risk_retention_vector,
    )
    from .risk_reduction import (
        sparse_risk_reduce as _sparse_risk_reduce,
    )
    from .risk_reduction import (
        subset_retention_vector as _subset_retention_vector,
    )
    from .risk_reduction import (
        turnover_aware_sector_cap as _turnover_aware_sector_cap,
    )

    _bind_compatibility_method(
        owner,
        "_confirmed_recovery_gross",
        _confirmed_recovery_gross,
    )
    _bind_compatibility_method(
        owner,
        "_risk_attribution_mechanism",
        _risk_attribution_mechanism,
        static=True,
    )
    _bind_compatibility_method(
        owner,
        "_risk_retention_score",
        _risk_retention_score,
    )
    _bind_compatibility_method(
        owner,
        "_risk_retention_vector",
        _risk_retention_vector,
        static=True,
    )
    _bind_compatibility_method(
        owner,
        "_risk_lifecycle_rank",
        _risk_lifecycle_rank,
        static=True,
    )
    _bind_compatibility_method(
        owner,
        "_subset_retention_vector",
        _subset_retention_vector,
    )
    _bind_compatibility_method(
        owner,
        "_sparse_risk_reduce",
        _sparse_risk_reduce,
    )
    _bind_compatibility_method(
        owner,
        "_risk_reduction_metadata",
        _risk_reduction_metadata,
        static=True,
    )
    _bind_compatibility_method(
        owner,
        "_turnover_aware_sector_cap",
        _turnover_aware_sector_cap,
    )
    _bind_compatibility_method(owner, "allocate", allocate)
    _bind_compatibility_method(
        owner,
        "_commit_frozen_exit_state",
        _commit_frozen_exit_state,
        static=True,
    )
    _bind_compatibility_method(
        owner,
        "_frozen_existing_targets",
        _frozen_existing_targets,
        static=True,
    )
    _bind_compatibility_method(owner, "_allocate_strategy", _allocate_strategy)
    return owner


if TYPE_CHECKING:
    from .allocator import PortfolioAllocator
else:
    PortfolioAllocator = _load_allocator()

__all__ = ("PortfolioAllocator", "current_weights", "effective_n")
