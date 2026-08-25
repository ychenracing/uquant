"""Shared scalar and durable-boundary account validation."""

from __future__ import annotations

import math
import re
from datetime import date as date_type
from typing import Any

from ..types import AccountOrder, Fill

_EVENT_ID = re.compile(r"^evt_[0-9a-f]{64}$")


_ORDER_ID = re.compile(r"^O[0-9]{9}$")


_LEGACY_INDUSTRY = "legacy_unmapped"


_LEGACY_MANIFEST_SHA256 = "0" * 64


_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION = 4


_UNLINKED_NATIVE_IDENTITY_FIELDS = (
    "signal_date",
    "symbol",
    "side",
    "lifecycle",
    "reduction_policy",
    "exit_kind",
    "event_id",
    "origin_subsystem",
    "mechanism",
    "origin_lifecycle",
    "replaces_symbol",
    "industry_at_entry",
    "industry_manifest_sha256",
)


_UNLINKED_LEGACY_IDENTITY_FIELDS = (
    "signal_date",
    "symbol",
    "side",
    "lifecycle",
    "reduction_policy",
    "reason_code",
    "exit_kind",
)


_SHOCK_STATES = frozenset(
    {
        "NONE",
        "SHOCK",
        "RECOVERY",
        "FAILED_REPAIR",
        "PERSISTENT_STRESS",
        "SECTOR_GUARD",
        "CAPITAL_GUARD_COOLDOWN",
        "UNBACKED_COOLDOWN",
        "FAST_V_RECOVERY",
        "ROTATION_RECOVERY",
    }
)


_SHOCK_SEVERITIES = frozenset(
    {
        "NORMAL",
        "MARKET",
        "CONCENTRATED",
        "SEVERE",
        "ANCHOR_BREAK",  # Accepted when normalizing compatible durable accounts.
        "COHORT_BREAK",
        "INCOMPLETE_UNIVERSE",
        "INCOMPLETE_UNIVERSE_UNBACKED",
    }
)


def _unlinked_fill_matches_order(
    fill: Fill,
    order: AccountOrder,
    *,
    native: bool,
) -> bool:
    """Match only stable structured fields; prose is never a join key."""

    fields = _UNLINKED_NATIVE_IDENTITY_FIELDS if native else _UNLINKED_LEGACY_IDENTITY_FIELDS
    return all(getattr(fill, field) == getattr(order, field) for field in fields)


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject JavaScript numeric extensions that are not valid JSON numbers."""
    raise ValueError(f"non-standard JSON numeric constant: {value}")


def _required_iso_date(value: Any, *, field: str) -> date_type:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field} requires an ISO date")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"{field} requires an ISO date") from exc


def _finite_number(
    value: Any,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise RuntimeError(f"{field} must be a finite number")
    if minimum is not None and converted < minimum:
        raise RuntimeError(f"{field} is below its minimum")
    if maximum is not None and converted > maximum:
        raise RuntimeError(f"{field} exceeds its maximum")
    return converted


def _nonnegative_integer(value: Any, *, field: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{field} must be an integer")
    if value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise RuntimeError(f"{field} must be {qualifier}")
    return int(value)


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field} must be non-empty text")
    return value


def _optional_iso_date(value: Any, *, field: str) -> None:
    if value == "":
        return
    _required_iso_date(value, field=field)


def _validate_nonnegative_integer_map(values: Any, *, field: str) -> None:
    if not isinstance(values, dict):
        raise RuntimeError(f"{field} must be an object")
    for key, value in values.items():
        _required_text(key, field=f"{field} key")
        _nonnegative_integer(value, field=f"{field}[{key}]")


def _validate_weight_map(values: Any, *, field: str) -> set[str]:
    if not isinstance(values, dict):
        raise RuntimeError(f"{field} must be an object")
    total = 0.0
    for key, value in values.items():
        _required_text(key, field=f"{field} key")
        total += _finite_number(
            value,
            field=f"{field}[{key}]",
            minimum=0.0,
            maximum=1.0,
        )
    if total > 1.0 + 1e-6:
        raise RuntimeError(f"{field} total weight exceeds one")
    return set(values)


def _validate_symbol_list(values: Any, *, field: str) -> set[str]:
    if not isinstance(values, list):
        raise RuntimeError(f"{field} must be an array")
    for value in values:
        _required_text(value, field=f"{field} symbol")
    if len(values) != len(set(values)):
        raise RuntimeError(f"{field} contains duplicate symbols")
    return set(values)


def _validate_event_array(values: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise RuntimeError(f"{field} must be an array")
    for event in values:
        if not isinstance(event, dict):
            raise RuntimeError(f"{field} must contain objects")
    return values


def _optional_finite_event_number(
    event: dict[str, Any],
    name: str,
    *,
    field: str,
    minimum: float | None = None,
) -> None:
    if name in event and event[name] is not None:
        _finite_number(
            event[name],
            field=f"{field} {name}",
            minimum=minimum,
        )


# Stable owned validation vocabulary used by domain-specific account validators.
EVENT_ID_PATTERN = _EVENT_ID
finite_number = _finite_number
HISTORICAL_ATTRIBUTION_SCHEMA_VERSION = _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
LEGACY_INDUSTRY = _LEGACY_INDUSTRY
LEGACY_MANIFEST_SHA256 = _LEGACY_MANIFEST_SHA256
nonnegative_integer = _nonnegative_integer
optional_finite_event_number = _optional_finite_event_number
optional_iso_date = _optional_iso_date
ORDER_ID_PATTERN = _ORDER_ID
reject_nonstandard_account_json_constant = _reject_nonstandard_json_constant
required_iso_date = _required_iso_date
required_text = _required_text
SHOCK_SEVERITIES = _SHOCK_SEVERITIES
SHOCK_STATES = _SHOCK_STATES
unlinked_fill_matches_order = _unlinked_fill_matches_order
UNLINKED_LEGACY_IDENTITY_FIELDS = _UNLINKED_LEGACY_IDENTITY_FIELDS
UNLINKED_NATIVE_IDENTITY_FIELDS = _UNLINKED_NATIVE_IDENTITY_FIELDS
validate_account_event_array = _validate_event_array
validate_account_symbol_list = _validate_symbol_list
validate_account_weight_map = _validate_weight_map
validate_nonnegative_account_integer_map = _validate_nonnegative_integer_map
