"""Single lossless identity owner for broker and simulated execution rows."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.models.trading import AccountOrder, Fill

PhysicalFillIdentity = tuple[object, ...]


def _value(fill: Fill | Mapping[str, object], name: str) -> object:
    return getattr(fill, name) if isinstance(fill, Fill) else fill.get(name)


def _identity_text(value: object, *, label: str, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise ValueError(f"absolute simulated fill identity {label} differs")
    return value


def _identity_positive_integer(value: object, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"absolute simulated fill identity {label} differs")
    return value


def _positive_float_hex(value: object, *, label: str) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise ValueError(f"absolute simulated fill identity {label} differs")
    return float(value).hex()


def physical_fill_identity(
    fill: Fill | Mapping[str, object],
) -> PhysicalFillIdentity:
    """Return the real broker ID or complete simulator execution identity."""

    if not isinstance(fill, (Fill, Mapping)):
        raise ValueError("absolute fill identity runtime type differs")
    fill_id = _value(fill, "fill_id")
    if type(fill_id) is not str:
        raise ValueError("absolute fill identity differs")
    if fill_id:
        return ("BROKER", fill_id)
    return (
        "SIMULATED",
        _identity_text(_value(fill, "order_id"), label="order"),
        _identity_text(_value(fill, "signal_date"), label="signal date"),
        _identity_text(_value(fill, "fill_date"), label="fill date"),
        _identity_text(_value(fill, "symbol"), label="symbol"),
        _identity_text(_value(fill, "side"), label="side"),
        _identity_positive_integer(_value(fill, "shares"), label="shares"),
        _positive_float_hex(_value(fill, "price"), label="price"),
        _positive_float_hex(_value(fill, "gross_value"), label="gross value"),
        _identity_text(_value(fill, "event_id"), label="event"),
        _identity_text(_value(fill, "grant_id"), label="grant", empty=True),
        _identity_text(_value(fill, "epoch_id"), label="epoch", empty=True),
    )


def physical_fill_identity_sha256(fill: Fill | Mapping[str, object]) -> str:
    """Seal the complete native physical identity for JSON manifests."""

    return canonical_json_sha256(list(physical_fill_identity(fill)))


def physical_fill_identity_map(
    fills: Sequence[Fill | Mapping[str, object]],
) -> dict[PhysicalFillIdentity, Fill | Mapping[str, object]]:
    """Index fill rows by their lossless native identity and reject duplicates."""

    result: dict[PhysicalFillIdentity, Fill | Mapping[str, object]] = {}
    for fill in fills:
        identity = physical_fill_identity(fill)
        if identity in result:
            raise ValueError("absolute duplicate physical fill identity")
        result[identity] = fill
    return result


def validate_physical_execution_identities(
    *,
    orders: Sequence[AccountOrder],
    fills: Sequence[Fill],
) -> None:
    """Reject empty/duplicate order IDs and duplicate physical fill identities."""

    if any(type(order) is not AccountOrder for order in orders) or any(
        type(fill) is not Fill for fill in fills
    ):
        raise ValueError("strategic outlet physical rows have invalid runtime types")
    order_ids = [order.order_id for order in orders]
    fill_ids = [physical_fill_identity(fill) for fill in fills]
    if any(not value for value in order_ids) or len(order_ids) != len(set(order_ids)):
        raise ValueError("strategic outlet has duplicate or empty order identity")
    if len(fill_ids) != len(set(fill_ids)):
        raise ValueError("strategic outlet has duplicate or empty fill identity")


__all__ = (
    "PhysicalFillIdentity",
    "physical_fill_identity",
    "physical_fill_identity_map",
    "physical_fill_identity_sha256",
    "validate_physical_execution_identities",
)
