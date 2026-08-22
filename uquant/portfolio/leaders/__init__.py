"""Assemble the historical leader policy from explicit mechanical owners."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import Any, cast

from .admission import (
    LeaderPortfolioPolicy as LeaderPortfolioPolicy,
)
from .admission import (
    _admission_utility,
    _conviction_evidence_qualified,
    _conviction_shares,
    _correlations,
    _dynamic_k,
)
from .lifecycle import (
    _industry_handoff,
    _leader_lifecycle_exit_confirmed,
    _leader_session_distance,
    _retention_score,
    _rotation_allowed,
    _session_clock,
    _update_leader_cycle_arm,
)
from .targets import _cap_opportunity_gross, _leader_targets


def _leader_compatibility_method[Function: Callable[..., Any]](
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
    runtime_function.__module__ = "uquant.portfolio_leaders"
    runtime_function.__qualname__ = f"LeaderPortfolioPolicy.{name}"
    return function


def _bind_leader_compatibility_method[Function: Callable[..., Any]](
    name: str,
    function: Function,
    *,
    static: bool = False,
) -> None:
    compatible = _leader_compatibility_method(function, name)
    descriptor: object = staticmethod(compatible) if static else compatible
    setattr(LeaderPortfolioPolicy, name, descriptor)


_bind_leader_compatibility_method("_cap_opportunity_gross", _cap_opportunity_gross)
_bind_leader_compatibility_method("_conviction_shares", _conviction_shares)
_bind_leader_compatibility_method(
    "_conviction_evidence_qualified", _conviction_evidence_qualified
)
_bind_leader_compatibility_method("_session_clock", _session_clock, static=True)
_bind_leader_compatibility_method(
    "_session_distance", _leader_session_distance, static=True
)
_bind_leader_compatibility_method("_correlations", _correlations)
_bind_leader_compatibility_method("_admission_utility", _admission_utility)
_bind_leader_compatibility_method("_dynamic_k", _dynamic_k)
_bind_leader_compatibility_method("_rotation_allowed", _rotation_allowed)
_bind_leader_compatibility_method("_update_leader_cycle_arm", _update_leader_cycle_arm)
_bind_leader_compatibility_method("_retention_score", _retention_score, static=True)
_bind_leader_compatibility_method(
    "_leader_lifecycle_exit_confirmed", _leader_lifecycle_exit_confirmed
)
_bind_leader_compatibility_method("_industry_handoff", _industry_handoff)
_bind_leader_compatibility_method("_leader_targets", _leader_targets)
