"""Ordered execution configuration validation."""

from __future__ import annotations

from typing import Any


def validate_execution(config: Any) -> None:
    """Validate execution fees, participation, and minimum actions."""

    for name in (
        "commission_rate",
        "min_commission",
        "stamp_duty",
        "transfer_fee",
        "slippage",
        "max_volume_participation",
    ):
        if getattr(config, name) < 0:
            raise ValueError(f"{name} cannot be negative")
    if not 0 <= config.max_volume_participation <= 1:
        raise ValueError("max_volume_participation must be in [0, 1]")
    if not (
        0
        < config.protected_restore_min_trade_weight
        <= config.restoration_min_trade_weight
        <= config.min_trade_weight
        <= 1
    ):
        raise ValueError(
            "protected_restore_min_trade_weight/restoration_min_trade_weight must be "
            "positive, ordered, and no greater than min_trade_weight"
        )
    if config.min_trade_value < 0:
        raise ValueError("min_trade_value cannot be negative")


__all__ = ("validate_execution",)
