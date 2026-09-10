"""Explicit post-observation acceptance revision; frozen evidence stays intact."""
from typing import Any


def wealth_floor(original: float, *, authorized: bool = True) -> float:
    """Scale final wealth (capital included), never profit or a wealth delta."""
    return original * 0.9 if authorized else original


def principal_wealth_floor(original: float, *, authorized: bool = True) -> float:
    """The user's full/champion floor includes capital and has no extra discount."""
    return 15.0 if authorized else original


def order_ceiling(original: float, *, authorized: bool = True) -> float:
    """At most40orders per account replay, retaining every original judgment."""
    return 40 if authorized else original


def acceptance_revision() -> dict[str, Any]:
    return {
        "revision_id": "cross-ai-principal15-orders40-20260910-v4",
        "previous_revision_id": "cross-ai-h1-drawdown-baseline-20260909-v3",
        "authorized_after_observing_candidate": True,
        "authorization": "User explicitly authorizes a hard15-fold principal wealth floor and at most40orders per account replay (2026-09-10)",
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
        "unchanged": ["wealth improvement deltas and improved-window qualification", "positive-return fraction", "p10 wealth floor", "all other drawdown gates", "acute return", "turnover", "costs", "recovery", "Absolute/Ownership obligations other than explicitly revised champion wealth/orders"],
        "original_judgment": "authorized=False reproduces original frozen comparisons before order revisions",
    }


def half_year_drawdown_ceiling(original: float, *, case: str, window: str, authorized: bool = True) -> float:
    """One explicit current-main comparison, with no inherited extra buffer."""
    if authorized and case == "no_optical" and window == "h1_2023":
        return 0.2442425185317515
    return original
