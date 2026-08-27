"""Causal route traces separated from intervention provenance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

_LAYER_ORDER = (
    "reference_context",
    "leaders",
    "risk",
    "opportunity",
    "targets",
    "orders",
    "fills",
    "account",
    "equity",
)


@dataclass(frozen=True, slots=True)
class RouteTraceRow:
    """One close-decision route plus its resulting durable economic state."""

    date: str
    reference_context: Mapping[str, Any]
    leaders: tuple[Mapping[str, Any], ...]
    risk: Mapping[str, Any]
    opportunity: str
    targets: tuple[Mapping[str, Any], ...]
    orders: tuple[Mapping[str, Any], ...]
    fills: tuple[Mapping[str, Any], ...]
    account_sha256: str
    equity: float
    target_gross: float = 0.0
    intervention_provenance: Mapping[str, Any] | None = None
    cash: float = 0.0
    position_shares: Mapping[str, int] = field(default_factory=dict)
    close_marks: Mapping[str, float] = field(default_factory=dict)

    def economic_payload(self) -> dict[str, Any]:
        """Return comparison data while deliberately excluding intervention audit data."""

        return {
            "date": self.date,
            "reference_context": dict(self.reference_context),
            "leaders": tuple(dict(item) for item in self.leaders),
            "risk": dict(self.risk),
            "opportunity": self.opportunity,
            "targets": {
                "items": tuple(dict(item) for item in self.targets),
                "target_gross": self.target_gross,
            },
            "orders": tuple(dict(item) for item in self.orders),
            "fills": tuple(dict(item) for item in self.fills),
            "account": self.account_sha256,
            "equity": self.equity,
        }


@dataclass(frozen=True, slots=True)
class RouteDivergence:
    """The earliest date and causal layer at which two route traces differ."""

    date: str
    changed_layers: tuple[str, ...]
    first_layer: str
    left: RouteTraceRow
    right: RouteTraceRow


def _validate_dates(rows: Sequence[RouteTraceRow]) -> tuple[str, ...]:
    values = tuple(row.date for row in rows)
    try:
        parsed = tuple(date.fromisoformat(value) for value in values)
    except ValueError as exc:
        raise ValueError("route traces require ISO-8601 dates") from exc
    if parsed != tuple(sorted(set(parsed))):
        raise ValueError("route traces require sorted unique dates")
    return values


def first_divergence(left: Sequence[RouteTraceRow], right: Sequence[RouteTraceRow]) -> RouteDivergence | None:
    """Locate the first difference using frozen causal-layer order."""

    left_dates = _validate_dates(left)
    right_dates = _validate_dates(right)
    if left_dates != right_dates:
        raise ValueError("route traces require aligned dates")
    for left_row, right_row in zip(left, right, strict=True):
        left_payload = left_row.economic_payload()
        right_payload = right_row.economic_payload()
        changed = tuple(layer for layer in _LAYER_ORDER if left_payload[layer] != right_payload[layer])
        if changed:
            return RouteDivergence(
                date=left_row.date,
                changed_layers=changed,
                first_layer=changed[0],
                left=left_row,
                right=right_row,
            )
    return None


def strip_intervention_provenance(rows: Sequence[RouteTraceRow]) -> tuple[RouteTraceRow, ...]:
    """Drop audit-only intervention metadata before exact economic comparison."""

    return tuple(replace(row, intervention_provenance=None) for row in rows)


__all__ = ("RouteDivergence", "RouteTraceRow", "first_divergence", "strip_intervention_provenance")
