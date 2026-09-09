"""Opportunity budget cap retained for compatibility callers."""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING

from ...types import LeaderScore, Opportunity

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy


def _cap_opportunity_gross(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    gross_cap: float,
    weights_now: dict[str, float],
    leaders: dict[str, LeaderScore],
    reasons: dict[str, str],
    opportunity: Opportunity,
) -> dict[str, float]:
    """Limit new opportunity risk without manufacturing incumbent sells.

    CHOPPY/WEAK are alpha-budget observations. Confirmed structural risk
    overlays own forced reductions. This distinction gives the continuous
    opportunity axis an economic hysteresis band: existing exposure may
    drift above the entry budget, while only proposed increments are
    sparsely removed.
    """
    capped = dict(proposed)
    increments = {
        symbol: max(0.0, weight - max(0.0, weights_now.get(symbol, 0.0))) for symbol, weight in capped.items()
    }
    baseline_total = sum(capped.values()) - sum(increments.values())
    allowed_total = max(gross_cap, baseline_total)
    excess = max(0.0, sum(max(0.0, value) for value in capped.values()) - allowed_total)
    if excess <= 1e-12:
        return capped
    symbols = tuple(sorted(symbol for symbol, weight in increments.items() if weight > 1e-12))
    feasible = [
        subset
        for size in range(1, len(symbols) + 1)
        for subset in combinations(symbols, size)
        if sum(increments[symbol] for symbol in subset) >= excess - 1e-12
    ]
    selected = min(
        feasible,
        key=lambda subset: (
            len(subset),
            sum(leaders[symbol].score if symbol in leaders else 0.0 for symbol in subset),
            -sum(increments[symbol] for symbol in subset),
            subset,
        ),
    )
    remaining = excess
    for symbol in sorted(
        selected,
        key=lambda item: (
            leaders[item].score if item in leaders else 0.0,
            -increments[item],
            item,
        ),
    ):
        reduction = min(increments[symbol], remaining)
        if reduction <= 1e-12:
            continue
        capped[symbol] = max(0.0, capped[symbol] - reduction)
        reasons[symbol] = f"{opportunity.value.lower()} opportunity gross contraction"
        remaining -= reduction
    if remaining > 1e-8:
        raise RuntimeError("leader opportunity cap could not be reconciled")
    return capped


cap_opportunity_gross = _cap_opportunity_gross
