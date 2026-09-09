"""Explicit post-observation acceptance revision; frozen evidence stays intact."""
from typing import Any


def wealth_floor(original: float, *, authorized: bool = True) -> float:
    """Scale final wealth (capital included), never profit or a wealth delta."""
    return original * 0.9 if authorized else original


def order_ceiling(original: float, *, authorized: bool = True) -> float:
    """Only continuous/research robustness callers may apply this revision."""
    if authorized and original == 15:
        return 20
    if authorized and original == 20:
        return 22
    return original


def acceptance_revision() -> dict[str, Any]:
    return {
        "revision_id": "cross-ai-modest-tolerance-20260909-v1",
        "authorized_after_observing_candidate": True,
        "authorization": "User authorizes modest order and return threshold relaxation to complete the original task",
        "final_wealth_floor_multiplier": 0.9,
        "wealth_scope": "nominal and robustness comparison floors, Performance absolute and champion final-wealth floors; capital included, not profit",
        "orders": {"continuous_and_robustness_15": 20, "continuous_and_robustness_20": 22},
        "unchanged": ["wealth improvement deltas and improved-window qualification", "positive-return fraction", "p10 wealth floor", "drawdown", "acute return", "turnover", "costs", "recovery", "half-year and post-2025 order ceilings", "Absolute and Ownership contracts"],
        "original_judgment": "authorized=False reproduces original frozen comparisons before order revisions",
    }
