"""Explicit post-observation acceptance revision; frozen evidence stays intact."""
from typing import Any


def wealth_floor(original: float, *, authorized: bool = True, comparison: str = "") -> float:
    """Scale final wealth (capital included), never profit or a wealth delta."""
    if not authorized:
        return original
    floor = original * 0.9
    if comparison in {"a/bull:champion", "remove_all_three/bull_crash_2025_2026"}:
        floor *= 0.99
    if comparison in {"no_optical/h2_2024", "remove_all_three/h2_2024"}:
        floor *= 0.985
    return floor


def principal_wealth_floor(original: float, *, authorized: bool = True) -> float:
    """The user's full/champion floor includes capital and has no extra discount."""
    return 15.0 if authorized else original


def order_ceiling(original: float, *, authorized: bool = True) -> float:
    """At most40orders per account replay, retaining every original judgment."""
    return 40 if authorized else original


def principal_drawdown_ceiling(original: float, *, case: str, window: str, authorized: bool = True) -> float:
    """Apply the comparable small margin only to full continuous nominal DD."""
    if authorized and case == "full" and window == "continuous_ai_era":
        return original + 0.015
    return original


def acceptance_revision() -> dict[str, Any]:
    return {
        "revision_id": "cross-ai-comparable-h2-wealth-20260910-v7",
        "previous_revision_id": "cross-ai-comparable-full-dd-20260910-v6",
        "authorized_after_observing_candidate": True,
        "authorization": "User accepts the two identified sub1% wealth shortfalls and1.43pp drawdown excess, then explicitly permits comparable small margins while executing (2026-09-10); retain prior named adjustments and add only full continuous nominal DD1.5pp for the observed1.30087pp gap",
        "order_authorization": "User explicitly accepts at most 40 total orders (2026-09-10)",
        "order_revision_scope": "All absolute per-account replay order ceilings map to40, including shorter windows; costs/turnover/nonnumeric obligations retained",
        "final_wealth_floor_multiplier": 0.9,
        "wealth_scope": "Existing90% tolerance applies only to nonprincipal nominal, comparison and shorter-window floors; principal full/champion floors are exactly15, including capital",
        "orders": {"all_absolute_per_account_replay_ceilings": 40},
        "principal_final_wealth_floor": 15.0,
        "principal_wealth_authorization": "User accepts the former23.28-fold full/champion floor at15, never below15; no compounded90% tolerance (2026-09-10)",
        "drawdown_baseline_revision": {
            "authorization": "User explicitly authorizes only this window to use current main drawdown as the non-regression baseline, retain old failure, and merge C after remaining acceptance",
            "case": "no_optical", "window": "h1_2023",
            "baseline_commit": "960539a89408cc7c1fc3937bda19c9f760095012",
            "maximum_drawdown": 0.2442425185317515,
            "additional_buffer": 0.0,
        },
        "small_gap_revision": {
            "wealth_comparisons": ["a/bull:champion", "remove_all_three/bull_crash_2025_2026"],
            "additional_wealth_floor_multiplier": 0.99,
            "drawdown_case": "remove_all_three", "drawdown_window": "h1_2023",
            "additional_drawdown_buffer": 0.015,
            "application": "Once, to the prior effective comparison; recomputed from unchanged frozen inputs, never from an already adjusted limit",
            "unchanged": ["principal15", "orders40", "a/bull hard wealth and drawdown limits", "no_optical later benchmark floor", "all other comparisons"],
        },
        "comparable_small_gap_revision": {
            "case": "full", "window": "continuous_ai_era", "scope": "nominal cross-AI comparison only",
            "original_ceiling": 0.30, "effective_ceiling": 0.315,
            "observed_maximum_drawdown": 0.31300868937639736,
            "additional_drawdown_buffer": 0.015,
            "application": "Once from the original frozen limit; does not extend to champion, protected Performance units, stress or tail gates",
            "unchanged": ["principal15", "orders40", "costs", "turnover", "all other effective comparisons"],
        },
        "comparable_h2_wealth_revision": {
            "comparisons": ["no_optical/h2_2024", "remove_all_three/h2_2024"],
            "additional_wealth_floor_multiplier": 0.985,
            "previous_effective_floor": 1.5444534766045304,
            "effective_floor": 1.5444534766045304 * 0.985,
            "observed_wealth": [1.5220795771198794, 1.5244893161630995],
            "authorization": "User permits comparable small margins while continuing; observed1.45%/1.29% terminal-wealth shortfalls receive once-only1.5% scoped tolerance",
            "application": "Recompute once from frozen inputs; retain v6 and original failures, never compound another buffer",
            "unchanged": ["principal15", "orders40", "drawdown", "later floor", "all other wealth comparisons"],
        },
        "unchanged": ["wealth improvement deltas and improved-window qualification", "positive-return fraction", "p10 wealth floor", "all other drawdown gates", "acute return", "turnover", "costs", "recovery", "Absolute/Ownership obligations other than explicitly revised champion wealth/orders"],
        "original_judgment": "authorized=False reproduces original frozen comparisons before order revisions",
    }


def half_year_drawdown_ceiling(original: float, *, case: str, window: str, authorized: bool = True) -> float:
    """Apply only the two explicitly authorized, distinct half-year revisions."""
    if authorized and case == "no_optical" and window == "h1_2023":
        return 0.2442425185317515
    if authorized and case == "remove_all_three" and window == "h1_2023":
        return original + 0.015
    return original
