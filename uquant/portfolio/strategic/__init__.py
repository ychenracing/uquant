"""Assemble the historical strategic policy from explicit owners."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import pandas as pd

    from ...types import AccountState, LeaderScore, RiskAssessment, Target

from .discovery import (
    StrategicPortfolioPolicy as StrategicPortfolioPolicy,
)
from .discovery import (
    initialize_strategic_cohort as _initialize_strategic_cohort,
)
from .lifecycle import (
    bounded_strategic_restore_risk_open as _bounded_strategic_restore_risk_open,
)
from .lifecycle import (
    retire_strategic_member as _retire_strategic_member,
)
from .lifecycle import (
    strategic_cohort_targets as _strategic_cohort_targets,
)


def _strategic_initialize_public_signature(
    self: Any,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool = True,
) -> None:
    raise NotImplementedError


def _strategic_targets_public_signature(
    self: Any,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    admission_open: bool = True,
) -> tuple[Target, ...] | None:
    raise NotImplementedError


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
    if name == "_initialize_strategic_cohort":
        public_annotations = dict(_strategic_initialize_public_signature.__annotations__)
        public_annotations.pop("self", None)
        _strategic_initialize_public_signature.__annotations__ = public_annotations
        cast(Any, runtime_function).__wrapped__ = _strategic_initialize_public_signature
    if name == "_strategic_cohort_targets":
        public_annotations = dict(_strategic_targets_public_signature.__annotations__)
        public_annotations.pop("self", None)
        _strategic_targets_public_signature.__annotations__ = public_annotations
        cast(Any, runtime_function).__wrapped__ = _strategic_targets_public_signature
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
