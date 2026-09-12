"""Ordered market and foundational configuration validation."""

from __future__ import annotations

import math
from typing import Any


def validate_market(config: Any) -> None:
    """Validate foundational values and bounded market inputs."""

    if config.initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if not 0 < config.max_gross <= 1:
        raise ValueError("max_gross must be in (0, 1]")
    if not 0 < config.max_symbol_weight <= 0.60:
        raise ValueError("max_symbol_weight must be in (0, 0.60]")
    if not 1 <= config.max_positions <= 6:
        raise ValueError("max_positions must be in [1, 6]")
    if not 20 <= config.emerging_min_history <= config.min_history:
        raise ValueError("emerging_min_history must be in [20, min_history]")
    for name in (
        "leader_mature_score",
        "leader_emerging_score",
        "leader_min_confidence",
        "industry_weight_cap",
        "concentrated_break_ratio",
        "recovery_breadth_min",
        "recovery_conviction_retention_bonus",
        "industry_rotation_min_score",
        "industry_rotation_min_confidence",
        "industry_rotation_deterioration",
        "industry_rotation_breadth",
    ):
        if not 0 <= getattr(config, name) <= 1:
            raise ValueError(f"{name} must be in [0, 1]")


__all__ = ("validate_market",)


def validate_public_inputs(config: Any) -> None:
    """Reject nonnumeric and nonfinite public settings before rule evaluation."""
    for name in (
        "initial_cash", "max_gross", "max_symbol_weight", "max_positions",
        "commission_rate", "min_commission", "stamp_duty", "transfer_fee",
        "slippage", "max_volume_participation", "minimum_median_amount", "min_trade_value",
    ):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        if name == "max_positions" and type(value) is not int:
            raise ValueError("max_positions must be an integer")
