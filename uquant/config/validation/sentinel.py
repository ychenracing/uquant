"""Ordered sentinel configuration validation."""

from __future__ import annotations

import math
from typing import Any


def validate_sentinel(config: Any) -> None:
    """Validate the frozen Sentinel authority configuration."""

    if str(config.risk_sentinel_mode) == "LIMITED_GROSS_CAP":
        raise ValueError("LIMITED_GROSS_CAP was rejected by the economic gate; use FREEZE_ONLY or SHADOW.")
    if config.risk_sentinel_mode not in {"SHADOW", "FREEZE_ONLY"}:
        raise ValueError("risk_sentinel_mode is invalid")
    if (
        isinstance(config.risk_sentinel_min_confidence, bool)
        or not isinstance(config.risk_sentinel_min_confidence, (int, float))
        or not math.isfinite(float(config.risk_sentinel_min_confidence))
        or not 0.0 <= config.risk_sentinel_min_confidence <= 1.0
    ):
        raise ValueError("risk_sentinel_min_confidence must be in [0, 1]")
    if not isinstance(config.risk_sentinel_severe_direct_enabled, bool):
        raise ValueError("risk_sentinel_severe_direct_enabled must be boolean")
    if not isinstance(config.risk_sentinel_causal_confirmation_enabled, bool):
        raise ValueError("risk_sentinel_causal_confirmation_enabled must be boolean")
    for name in ("risk_sentinel_confirm_days", "risk_sentinel_repair_days"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")


__all__ = ("validate_sentinel",)
