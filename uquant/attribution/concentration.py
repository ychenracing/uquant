"""Pure contribution, PnL grouping, and concentration calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

RECONCILIATION_TOLERANCE = 1e-6


def _finite(value: Any, *, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{label} must be a finite number")
    return number


def _empty_pnl_bucket() -> dict[str, float]:
    return {
        "realized_pnl": 0.0,
        "open_pnl": 0.0,
        "total_pnl": 0.0,
        "cash_fees": 0.0,
        "slippage": 0.0,
        "all_in_costs": 0.0,
        "gross_transaction_value": 0.0,
    }


def contribution_concentration(values: Mapping[str, float]) -> dict[str, Any]:
    """Describe positive, signed-net, and absolute PnL contribution denominators."""

    normalized: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError("contribution keys must be non-empty text")
        normalized[key] = _finite(value, label=f"contribution for {key}")
    positive_values = sorted((value for value in normalized.values() if value > 0.0), reverse=True)
    positive_denominator = sum(positive_values)
    signed_denominator = sum(normalized.values())
    absolute_denominator = sum(abs(value) for value in normalized.values())
    if positive_denominator > 0.0:
        positive_weights = [value / positive_denominator for value in positive_values]
        positive: dict[str, Any] = {
            "status": "DEFINED",
            "top1": positive_weights[0],
            "top3": sum(positive_weights[:3]),
            "hhi": sum(weight * weight for weight in positive_weights),
        }
    else:
        positive = {
            "status": "UNDEFINED_NO_POSITIVE_PNL",
            "top1": None,
            "top3": None,
            "hhi": None,
        }
    signed = (
        {
            "status": "DEFINED",
            "contributions": {key: value / signed_denominator for key, value in sorted(normalized.items())},
        }
        if signed_denominator > 0.0
        else {
            "status": "UNDEFINED_NONPOSITIVE_NET_PNL",
            "contributions": None,
        }
    )
    absolute = (
        {
            "status": "DEFINED",
            "contributions": {
                key: abs(value) / absolute_denominator for key, value in sorted(normalized.items())
            },
            "hhi": sum((abs(value) / absolute_denominator) ** 2 for value in normalized.values()),
        }
        if absolute_denominator > 0.0
        else {
            "status": "UNDEFINED_ZERO_ABSOLUTE_PNL",
            "contributions": None,
            "hhi": None,
        }
    )
    return {
        "denominators": {
            "positive": positive_denominator,
            "signed_net": signed_denominator,
            "absolute": absolute_denominator,
        },
        "positive": positive,
        "signed": signed,
        "absolute": absolute,
        "winner_count": sum(value > 0.0 for value in normalized.values()),
        "loser_count": sum(value < 0.0 for value in normalized.values()),
        "zero_count": sum(value == 0.0 for value in normalized.values()),
    }


def _group_lot_pnl(
    lots: Sequence[Mapping[str, Any]],
    field: str,
    *,
    registry: Sequence[str] = (),
) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = {}
    for lot in lots:
        name = str(lot[field])
        bucket = grouped.setdefault(name, _empty_pnl_bucket())
        for pnl_field in ("realized_pnl", "open_pnl", "total_pnl"):
            bucket[pnl_field] += float(lot[pnl_field])
        costs = lot["costs"]
        bucket["cash_fees"] += float(costs["cash_fees"])
        bucket["slippage"] += float(costs["slippage"])
        bucket["all_in_costs"] += float(costs["all_in"])
        bucket["gross_transaction_value"] += float(lot["gross_transaction_value"])
    for name in registry:
        grouped.setdefault(name, _empty_pnl_bucket())
    return dict(sorted(grouped.items()))


def _holding_summary(lots: Sequence[Mapping[str, Any]]) -> dict[str, float | int | None]:
    total_shares = sum(int(lot["shares"]) for lot in lots)
    if total_shares == 0:
        return {"lot_count": 0, "shares": 0, "weighted_average": None}
    return {
        "lot_count": len(lots),
        "shares": total_shares,
        "weighted_average": sum(int(lot["shares"]) * int(lot["holding_sessions"]) for lot in lots)
        / total_shares,
    }
