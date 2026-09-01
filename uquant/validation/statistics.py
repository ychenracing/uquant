"""Finite deterministic statistics shared by validation policies."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def _finite_quantile_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def linear_quantile(values: Sequence[float], probability: float) -> float:
    """Return the sorted ``(n - 1) * probability`` linear interpolation."""

    selected_probability = _finite_quantile_number(
        probability, label="quantile probability"
    )
    if not 0.0 <= selected_probability <= 1.0:
        raise ValueError("quantile probability must be in [0, 1]")
    finite_values = tuple(
        _finite_quantile_number(value, label="quantile value") for value in values
    )
    if not finite_values:
        raise ValueError("quantile requires a non-empty metric sequence")

    ordered = sorted(finite_values)
    location = (len(ordered) - 1) * selected_probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


__all__ = ("linear_quantile",)
