"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ...config import DEFAULT_CONFIG, SystemConfig
from ..generalization_matrix import head_and_source as _head_and_source
from .cell_policy import (
    RandomTailStatistics as RandomTailStatistics,
)
from .cell_policy import (
    evaluate_recovered_against_group_envelope as _evaluate_recovered_group_envelope_owner,
)
from .cell_policy import (
    evaluate_relative_cell_non_regression,
)
from .cell_policy import (
    policy_quantile as _policy_quantile_owner,
)
from .cell_policy import (
    random_tail_statistics as _random_tail_statistics_owner,
)
from .cell_policy import (
    violates_effective_floor as _violates_effective_floor_owner,
)
from .schema import GeneralizationBaseline, GeneralizationPolicy

type HeadAndSource = Callable[[Path], tuple[str, str]]

_HEAD_AND_SOURCE: ContextVar[HeadAndSource] = ContextVar(
    "uquant_generalization_head_and_source",
    default=_head_and_source,
)


@contextmanager
def generalization_policy_capabilities(
    *, head_and_source: HeadAndSource
) -> Iterator[None]:
    token = _HEAD_AND_SOURCE.set(head_and_source)
    try:
        yield
    finally:
        _HEAD_AND_SOURCE.reset(token)


def evaluate_cell_non_regression(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Apply the frozen relative per-cell wealth, risk, order, and turnover gates."""
    return evaluate_relative_cell_non_regression(candidate, reference, policy=policy)


def _evaluate_recovered_against_group_envelope(
    candidate: Mapping[str, Any],
    authenticated_valid_group: Sequence[Mapping[str, Any]],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Bound one recovered replay by the worst authenticated valid peer metrics."""
    return _evaluate_recovered_group_envelope_owner(
        candidate,
        authenticated_valid_group,
        policy=policy,
    )


def _policy_quantile(values: Sequence[float], probability: float) -> float:
    return _policy_quantile_owner(values, probability)


_RandomTailStatistics = RandomTailStatistics


def _random_tail_statistics(
    group: Sequence[tuple[str, Mapping[str, Any] | None, bool]],
    *,
    requested: int,
) -> RandomTailStatistics:
    return _random_tail_statistics_owner(group, requested=requested)


def _violates_effective_floor(
    value: float,
    *,
    literal: float,
    baseline: float,
    strict: bool = False,
) -> tuple[bool, float]:
    """Keep the literal floor unless the authenticated champion is lower."""
    return _violates_effective_floor_owner(
        value,
        literal=literal,
        baseline=baseline,
        strict=strict,
    )


def evaluate_generalization_policy_artifact(
    artifact: Mapping[str, Any],
    *,
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    require_exact_equality: bool = False,
    data_dir: str | Path | None = None,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Recompute frozen relative, intrinsic, and random-tail results from raw cells."""
    from .evaluation_stages import evaluate_policy_stages

    return evaluate_policy_stages(
        artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=require_exact_equality,
        data_dir=data_dir,
        expected_config=expected_config,
        head_and_source=_HEAD_AND_SOURCE.get(),
    )


_quantile = _policy_quantile

evaluate_recovered_against_group_envelope = _evaluate_recovered_against_group_envelope
quantile = _quantile
random_tail_statistics = _random_tail_statistics
violates_effective_floor = _violates_effective_floor
