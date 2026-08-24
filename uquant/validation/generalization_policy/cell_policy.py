"""Relative cell and authenticated random-tail policy calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .schema import GeneralizationPolicy


def evaluate_relative_cell_non_regression(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Apply the frozen relative per-cell wealth, risk, order, and turnover gates."""

    failures: list[str] = []
    candidate_wealth = float(candidate["final_wealth"])
    reference_wealth = float(reference["final_wealth"])
    wealth_limit = reference_wealth * policy.wealth_ratio_min
    if candidate_wealth < wealth_limit:
        failures.append(f"final_wealth {candidate_wealth} is below 95% reference {wealth_limit:g}")
    candidate_drawdown = float(candidate["max_drawdown"])
    drawdown_limit = float(reference["max_drawdown"]) + policy.drawdown_absolute_buffer
    if candidate_drawdown > drawdown_limit:
        failures.append(
            f"max_drawdown {candidate_drawdown} exceeds reference-plus-buffer {drawdown_limit:g}"
        )
    candidate_orders = int(candidate["account_orders"])
    reference_orders = int(reference["account_orders"])
    order_limit = max(
        reference_orders + policy.orders_absolute_buffer,
        math.ceil(reference_orders * policy.orders_ratio_max),
    )
    if candidate_orders > order_limit:
        failures.append(
            f"account_orders {candidate_orders} exceeds reference activity limit {order_limit}"
        )
    for name in ("gross_turnover", "annual_turnover"):
        candidate_turnover = float(candidate[name])
        reference_turnover = float(reference[name])
        if reference_turnover == 0.0:
            if candidate_turnover != 0.0:
                failures.append(
                    f"{name} {candidate_turnover} must remain zero because reference is zero"
                )
        else:
            turnover_limit = reference_turnover * policy.turnover_ratio_max
            if candidate_turnover > turnover_limit:
                failures.append(
                    f"{name} {candidate_turnover} exceeds 110% reference {turnover_limit:g}"
                )
    return tuple(failures)


def evaluate_recovered_against_group_envelope(
    candidate: Mapping[str, Any],
    authenticated_valid_group: Sequence[Mapping[str, Any]],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Bound one recovered replay by the worst authenticated valid peer metrics."""

    if not authenticated_valid_group:
        return ("authenticated random group has no valid recovery envelope",)
    envelope = {
        "final_wealth": min(float(item["final_wealth"]) for item in authenticated_valid_group),
        "max_drawdown": max(float(item["max_drawdown"]) for item in authenticated_valid_group),
        "account_orders": max(int(item["account_orders"]) for item in authenticated_valid_group),
        "gross_turnover": max(float(item["gross_turnover"]) for item in authenticated_valid_group),
        "annual_turnover": max(float(item["annual_turnover"]) for item in authenticated_valid_group),
    }
    return evaluate_relative_cell_non_regression(candidate, envelope, policy=policy)


def policy_quantile(values: Sequence[float], probability: float) -> float:
    """Interpolate one deterministic policy quantile from ordered values."""

    ordered = sorted(values)
    if not ordered:
        raise ValueError("generalization tail quantile requires valid economic cells")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


@dataclass(frozen=True, slots=True)
class RandomTailStatistics:
    """Authenticated tail statistics for one fixed window/pool-size group."""

    valid_cells: int
    replay_error_cells: int
    positive_return_fraction: float
    p10_wealth: float | None
    p90_drawdown: float | None
    p90_orders: float | None


def random_tail_statistics(
    group: Sequence[tuple[str, Mapping[str, Any] | None, bool]],
    *,
    requested: int,
) -> RandomTailStatistics:
    """Calculate authenticated random-tail statistics for one group."""

    valid = [metrics for _, metrics, has_error in group if metrics is not None and not has_error]
    wealth_values = [float(item["final_wealth"]) for item in valid]
    drawdown_values = [float(item["max_drawdown"]) for item in valid]
    order_values = [float(item["account_orders"]) for item in valid]
    return RandomTailStatistics(
        valid_cells=len(valid),
        replay_error_cells=sum(has_error for _, _, has_error in group),
        positive_return_fraction=(sum(value > 1.0 for value in wealth_values) / requested),
        p10_wealth=policy_quantile(wealth_values, 0.10) if wealth_values else None,
        p90_drawdown=policy_quantile(drawdown_values, 0.90) if drawdown_values else None,
        p90_orders=policy_quantile(order_values, 0.90) if order_values else None,
    )


def violates_effective_floor(
    value: float,
    *,
    literal: float,
    baseline: float,
    strict: bool = False,
) -> tuple[bool, float]:
    """Keep the literal floor unless the authenticated champion is lower."""

    effective = min(literal, baseline)
    if strict and baseline > literal:
        return value <= effective, effective
    return value < effective, effective
