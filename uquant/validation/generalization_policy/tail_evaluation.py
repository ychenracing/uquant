"""Authenticated random-tail evaluation for generalization policy artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .cell_policy import (
    evaluate_recovered_against_group_envelope,
    random_tail_statistics,
)
from .schema import GeneralizationBaseline, GeneralizationPolicy


@dataclass(slots=True)
class TailEvaluationContext:
    """Inputs and ordered output carriers for random-tail evaluation."""

    baseline: GeneralizationBaseline
    policy: GeneralizationPolicy
    random_groups: dict[
        tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]
    ]
    reference_random_groups: dict[
        tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]
    ]
    failures: list[str]
    results: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _TailBounds:
    replay_error_ceiling: int
    positive_floor: float
    p10_floor: float
    drawdown_ceiling: float
    orders_ceiling: float


def evaluate_random_tails(context: TailEvaluationContext) -> None:
    """Evaluate fixed-order random groups and append their reports and failures."""

    for (window, pool_size), group in sorted(context.random_groups.items()):
        _evaluate_random_tail_group(context, window, pool_size, group)


def _evaluate_random_tail_group(
    context: TailEvaluationContext,
    window: str,
    pool_size: int,
    group: Sequence[tuple[str, Mapping[str, Any] | None, bool]],
) -> None:
    requested = context.policy.requested_seeds_per_group
    if len(group) != requested:
        context.failures.append(
            f"random tail coverage failed: {window}/size-{pool_size}: "
            f"requested {requested}, observed {len(group)}"
        )
    candidate_tail = random_tail_statistics(group, requested=requested)
    reference_group = context.reference_random_groups[(window, pool_size)]
    baseline_tail = random_tail_statistics(reference_group, requested=requested)
    authenticated = [
        metrics
        for _, metrics, has_error in reference_group
        if metrics is not None and not has_error
    ]
    literal_fallback = not authenticated
    comparison_group = (
        group
        if literal_fallback
        else [item for item in group if context.baseline.cells[item[0]].metrics is not None]
    )
    comparison_tail = random_tail_statistics(comparison_group, requested=requested)
    bounds = _effective_tail_bounds(context.policy, baseline_tail, literal_fallback)
    literal_reasons = _literal_tail_reasons(context.policy, candidate_tail)
    reasons = _non_regression_tail_reasons(comparison_tail, candidate_tail, bounds)
    _extend_recovered_tail_reasons(
        context, reasons, group, authenticated, literal_fallback
    )
    context.results.append(
        _tail_result(
            window,
            pool_size,
            requested,
            candidate_tail,
            comparison_tail,
            authenticated,
            literal_fallback,
            bounds,
            literal_reasons,
            reasons,
        )
    )
    context.failures.extend(
        f"random tail failed: {window}/size-{pool_size}: {reason}" for reason in reasons
    )


def _effective_tail_bounds(
    policy: GeneralizationPolicy,
    baseline_tail: Any,
    literal_fallback: bool,
) -> _TailBounds:
    replay_error_ceiling = 0 if literal_fallback else baseline_tail.replay_error_cells
    positive_floor = (
        policy.positive_return_fraction_min
        if literal_fallback
        else min(
            policy.positive_return_fraction_min,
            baseline_tail.positive_return_fraction,
        )
    )
    p10_floor = (
        policy.p10_wealth_min
        if baseline_tail.p10_wealth is None
        else min(policy.p10_wealth_min, baseline_tail.p10_wealth)
    )
    drawdown_ceiling = (
        policy.p90_drawdown_max
        if baseline_tail.p90_drawdown is None
        else max(policy.p90_drawdown_max, baseline_tail.p90_drawdown)
    )
    orders_ceiling = (
        policy.p90_orders_max
        if baseline_tail.p90_orders is None
        else max(policy.p90_orders_max, baseline_tail.p90_orders)
    )
    return _TailBounds(
        replay_error_ceiling,
        positive_floor,
        p10_floor,
        drawdown_ceiling,
        orders_ceiling,
    )


def _literal_tail_reasons(
    policy: GeneralizationPolicy,
    candidate_tail: Any,
) -> list[str]:
    reasons: list[str] = []
    if candidate_tail.replay_error_cells:
        reasons.append(f"{candidate_tail.replay_error_cells} replay error cells")
    if candidate_tail.positive_return_fraction < policy.positive_return_fraction_min:
        reasons.append(
            f"positive-return fraction {candidate_tail.positive_return_fraction:g} is below 0.6"
        )
    if candidate_tail.p10_wealth is None or candidate_tail.p10_wealth < policy.p10_wealth_min:
        reasons.append(f"p10 wealth {candidate_tail.p10_wealth} is below 0.8")
    if candidate_tail.p90_drawdown is None or candidate_tail.p90_drawdown > policy.p90_drawdown_max:
        reasons.append(f"p90 drawdown {candidate_tail.p90_drawdown} exceeds 0.3")
    if candidate_tail.p90_orders is None or candidate_tail.p90_orders > policy.p90_orders_max:
        reasons.append(f"p90 orders {candidate_tail.p90_orders} exceeds 20")
    return reasons


def _non_regression_tail_reasons(
    comparison_tail: Any,
    candidate_tail: Any,
    bounds: _TailBounds,
) -> list[str]:
    reasons: list[str] = []
    if candidate_tail.replay_error_cells > bounds.replay_error_ceiling:
        reasons.append(
            f"replay error cells {candidate_tail.replay_error_cells} exceed "
            f"effective maximum {bounds.replay_error_ceiling}"
        )
    if comparison_tail.positive_return_fraction < bounds.positive_floor:
        reasons.append(
            f"positive-return fraction {comparison_tail.positive_return_fraction:g} "
            f"is below effective minimum {bounds.positive_floor:g}"
        )
    if comparison_tail.p10_wealth is None or comparison_tail.p10_wealth < bounds.p10_floor:
        reasons.append(
            f"p10 wealth {comparison_tail.p10_wealth} is below effective minimum {bounds.p10_floor:g}"
        )
    if comparison_tail.p90_drawdown is None or comparison_tail.p90_drawdown > bounds.drawdown_ceiling:
        reasons.append(
            f"p90 drawdown {comparison_tail.p90_drawdown} exceeds effective maximum {bounds.drawdown_ceiling:g}"
        )
    if comparison_tail.p90_orders is None or comparison_tail.p90_orders > bounds.orders_ceiling:
        reasons.append(
            f"p90 orders {comparison_tail.p90_orders} exceeds effective maximum {bounds.orders_ceiling:g}"
        )
    return reasons


def _extend_recovered_tail_reasons(
    context: TailEvaluationContext,
    reasons: list[str],
    group: Sequence[tuple[str, Mapping[str, Any] | None, bool]],
    authenticated: Sequence[Mapping[str, Any]],
    literal_fallback: bool,
) -> None:
    for identifier, metrics, has_error in group:
        if (
            literal_fallback
            or context.baseline.cells[identifier].replay_error is None
            or metrics is None
            or has_error
        ):
            continue
        reasons.extend(
            f"recovered cell {identifier} exceeds authenticated group envelope: {reason}"
            for reason in evaluate_recovered_against_group_envelope(
                metrics, authenticated, policy=context.policy
            )
        )


def _tail_result(
    window: str,
    pool_size: int,
    requested: int,
    candidate_tail: Any,
    comparison_tail: Any,
    authenticated: Sequence[Mapping[str, Any]],
    literal_fallback: bool,
    bounds: _TailBounds,
    literal_reasons: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "window": window,
        "pool_size": pool_size,
        "requested_cells": requested,
        "valid_cells": candidate_tail.valid_cells,
        "replay_error_cells": candidate_tail.replay_error_cells,
        "authenticated_support_cells": len(authenticated),
        "literal_fallback": literal_fallback,
        "positive_return_fraction": candidate_tail.positive_return_fraction,
        "p10_wealth": candidate_tail.p10_wealth,
        "p90_drawdown": candidate_tail.p90_drawdown,
        "p90_orders": candidate_tail.p90_orders,
        "non_regression_tail": {
            "valid_cells": comparison_tail.valid_cells,
            "positive_return_fraction": comparison_tail.positive_return_fraction,
            "p10_wealth": comparison_tail.p10_wealth,
            "p90_drawdown": comparison_tail.p90_drawdown,
            "p90_orders": comparison_tail.p90_orders,
        },
        "effective_bounds": {
            "replay_error_cells_max": bounds.replay_error_ceiling,
            "positive_return_fraction_min": bounds.positive_floor,
            "p10_wealth_min": bounds.p10_floor,
            "p90_drawdown_max": bounds.drawdown_ceiling,
            "p90_orders_max": bounds.orders_ceiling,
        },
        "literal_passed": not literal_reasons,
        "literal_failures": literal_reasons,
        "non_regression_passed": not reasons,
        "grandfathered": bool(literal_reasons and not reasons),
        "passed": not reasons,
        "failures": reasons,
    }
