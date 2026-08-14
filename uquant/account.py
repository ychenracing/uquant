"""Strict persistence for the single real-account state."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from datetime import date as date_type
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .types import (
    ACCOUNT_SCHEMA_VERSION,
    ATTRIBUTION_IDENTITY_FIELDS,
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Fill,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Risk,
    Side,
    Tranche,
    derive_attribution_event_id,
    order_intent_metadata,
    validate_attribution_compatibility,
)
from .validation.universe import (
    CANONICAL_INDUSTRIES,
    REQUIRED_AI_UNIVERSE_SHA256,
    default_ai_universe,
)

_EVENT_ID = re.compile(r"^evt_[0-9a-f]{64}$")
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


def _unlinked_fill_matches_order(
    fill: Fill,
    order: AccountOrder,
    *,
    native: bool,
) -> bool:
    """Match only stable structured fields; prose is never a join key."""

    fields = (
        _UNLINKED_NATIVE_IDENTITY_FIELDS
        if native
        else _UNLINKED_LEGACY_IDENTITY_FIELDS
    )
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


def _derive_v4_attribution_event_id(
    *,
    signal_date: str,
    symbol: str,
    target_weight: float,
    lifecycle: str,
    origin_lifecycle: str,
    origin_subsystem: str,
    mechanism: str,
    replaces_symbol: str | None,
    industry_at_entry: str,
    industry_manifest_sha256: str,
    reduction_policy: str,
    reason_code: str,
    exit_kind: str,
) -> str:
    """Read only the exact machine-only event format written by schema v4."""

    # Current derivation performs the shared closed-vocabulary and scalar
    # validation. Its result is intentionally discarded at this migration-only
    # boundary before reconstructing the exact historical payload.
    derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=origin_lifecycle,
        origin_subsystem=origin_subsystem,
        mechanism=mechanism,
        replaces_symbol=replaces_symbol,
        industry_at_entry=industry_at_entry,
        industry_manifest_sha256=industry_manifest_sha256,
        reduction_policy=reduction_policy,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    # Schema-v4's v1 payload already excluded both display fields. They remain
    # function arguments only because persisted order objects carry them.
    del reason_code, exit_kind
    payload = {
        "schema": "uquant.attribution-event.v1",
        "signal_date": signal_date,
        "symbol": symbol,
        "target_weight": float(target_weight).hex(),
        "lifecycle": lifecycle,
        "origin_lifecycle": origin_lifecycle,
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "replaces_symbol": replaces_symbol,
        "industry_at_entry": industry_at_entry,
        "industry_manifest_sha256": industry_manifest_sha256,
        "reduction_policy": reduction_policy,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "evt_" + hashlib.sha256(encoded).hexdigest()


def _validate_attribution_identity(
    item: Any,
    *,
    label: str,
    verify_event_derivation: bool = False,
    event_schema_version: int = ACCOUNT_SCHEMA_VERSION,
) -> None:
    """Validate one canonical identity, including explicit migration defaults."""

    if not isinstance(item.event_id, str) or not _EVENT_ID.fullmatch(item.event_id):
        raise RuntimeError(f"{label} has invalid event_id")
    try:
        origin = OriginSubsystem(item.origin_subsystem)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} has invalid origin_subsystem") from exc
    try:
        mechanism = AttributionMechanism(item.mechanism)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} has invalid mechanism") from exc
    try:
        validate_attribution_compatibility(
            origin_subsystem=origin.value,
            mechanism=mechanism.value,
            side=getattr(item, "side", None),
        )
    except (TypeError, ValueError) as exc:
        if (
            getattr(item, "side", None) == Side.BUY.value
            and origin is OriginSubsystem.LEGACY_MIGRATION
        ):
            raise RuntimeError(
                f"{label} legacy migration identity cannot create a BUY"
            ) from exc
        elif (
            getattr(item, "side", None) == Side.BUY.value
            and origin is OriginSubsystem.BROKER_RECONCILIATION
        ):
            raise RuntimeError(
                f"{label} broker reconciliation identity cannot create a BUY"
            ) from exc
        else:
            raise RuntimeError(f"{label} has incompatible attribution: {exc}") from exc
    try:
        Lifecycle(item.origin_lifecycle)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} has invalid origin_lifecycle") from exc
    if item.replaces_symbol is not None and (
        not isinstance(item.replaces_symbol, str) or not item.replaces_symbol.strip()
    ):
        raise RuntimeError(f"{label} has invalid replaces_symbol")
    legacy_identity = bool(
        origin is OriginSubsystem.LEGACY_MIGRATION
        and mechanism is AttributionMechanism.LEGACY_MIGRATION
    )
    broker_degraded_identity = bool(
        origin is OriginSubsystem.BROKER_RECONCILIATION
        and mechanism is AttributionMechanism.BROKER_RECONCILIATION
    )
    migrated_inventory_sale = bool(
        getattr(item, "side", None) == Side.SELL.value
    )
    if item.industry_at_entry in CANONICAL_INDUSTRIES:
        if item.industry_manifest_sha256 != REQUIRED_AI_UNIVERSE_SHA256:
            raise RuntimeError(f"{label} has invalid industry manifest SHA-256")
    elif item.industry_at_entry == _LEGACY_INDUSTRY and (
        legacy_identity or broker_degraded_identity or migrated_inventory_sale
    ):
        if item.industry_manifest_sha256 != _LEGACY_MANIFEST_SHA256:
            raise RuntimeError(f"{label} has invalid legacy industry manifest SHA-256")
    else:
        raise RuntimeError(f"{label} has invalid industry_at_entry")
    if verify_event_derivation:
        try:
            derivation = (
                _derive_v4_attribution_event_id
                if event_schema_version == _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
                else derive_attribution_event_id
            )
            if event_schema_version not in {
                _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
                ACCOUNT_SCHEMA_VERSION,
            }:
                raise ValueError("unsupported attribution event schema")
            expected = derivation(
                signal_date=item.signal_date,
                symbol=item.symbol,
                target_weight=item.target_weight,
                lifecycle=item.lifecycle,
                origin_lifecycle=item.origin_lifecycle,
                origin_subsystem=item.origin_subsystem,
                mechanism=item.mechanism,
                replaces_symbol=item.replaces_symbol,
                industry_at_entry=item.industry_at_entry,
                industry_manifest_sha256=item.industry_manifest_sha256,
                reduction_policy=item.reduction_policy,
                reason_code=item.reason_code,
                exit_kind=item.exit_kind,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} has malformed attribution identity") from exc
        if item.event_id != expected:
            if event_schema_version == _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
                raise RuntimeError(
                    f"{label} v4 event_id differs from canonical derivation"
                )
            raise RuntimeError(f"{label} event_id differs from canonical derivation")


def _validate_order_intent(
    order: PendingOrder | AccountOrder,
    *,
    label: str,
    validate_attribution: bool = False,
    event_schema_version: int = ACCOUNT_SCHEMA_VERSION,
) -> date_type:
    """Validate the immutable economic identity shared by durable orders."""
    signal_date = _required_iso_date(order.signal_date, field=f"{label} signal_date")
    _required_text(order.symbol, field=f"{label} symbol")
    if not isinstance(order.side, str) or order.side not in {item.value for item in Side}:
        raise RuntimeError(f"{label} has invalid side")
    _finite_number(
        order.target_weight,
        field=f"{label} target_weight",
        minimum=0.0,
        maximum=1.0,
    )
    _required_text(order.reason, field=f"{label} reason")
    if not isinstance(order.lifecycle, str) or order.lifecycle not in {item.value for item in Lifecycle}:
        raise RuntimeError(f"{label} has invalid lifecycle")
    if not isinstance(order.reduction_policy, str) or order.reduction_policy not in {
        item.value for item in ReductionPolicy
    }:
        raise RuntimeError(f"{label} has invalid reduction policy")
    _required_text(order.reason_code, field=f"{label} reason_code")
    _required_text(order.exit_kind, field=f"{label} exit_kind")
    _finite_number(order.entry_score, field=f"{label} entry_score")
    _finite_number(
        order.entry_confidence,
        field=f"{label} entry_confidence",
        minimum=0.0,
        maximum=1.0,
    )
    if not isinstance(order.entry_regime, str) or order.entry_regime not in {
        item.value for item in Opportunity
    }:
        raise RuntimeError(f"{label} has invalid entry_regime")
    _finite_number(
        order.entry_industry_strength,
        field=f"{label} entry_industry_strength",
    )
    if validate_attribution:
        _validate_attribution_identity(
            order,
            label=label,
            verify_event_derivation=True,
            event_schema_version=event_schema_version,
        )
        if order.side == Side.BUY.value:
            expected_industry = default_ai_universe().industry_of(order.symbol, signal_date)
            if expected_industry == "unknown":
                raise RuntimeError(f"{label} BUY has no point-in-time AI-universe membership")
            if order.industry_at_entry != expected_industry:
                raise RuntimeError(f"{label} BUY industry_at_entry differs from point-in-time membership")
    return signal_date


def validate_pending_order_for_account_write(
    state: AccountState,
    order: PendingOrder,
) -> None:
    """Validate one current-schema order before execution mutates the ledger."""

    if state.schema_version != ACCOUNT_SCHEMA_VERSION:
        raise RuntimeError("pending order registration requires the current account schema")
    _validate_order_intent(
        order,
        label="pending order",
        validate_attribution=True,
    )


def _validate_fill(
    fill: Fill,
    *,
    ledger: dict[str, AccountOrder],
    allow_schema_v2_missing_sell_attribution: bool = False,
    validate_attribution: bool = False,
) -> None:
    """Validate one fill and reconcile its immutable order attribution."""

    signal_date = _required_iso_date(fill.signal_date, field="fill signal_date")
    fill_date = _required_iso_date(fill.fill_date, field="fill fill_date")
    if fill_date <= signal_date:
        raise RuntimeError("fill date must be after its signal date")
    _required_text(fill.symbol, field="fill symbol")
    if not isinstance(fill.side, str) or fill.side not in {item.value for item in Side}:
        raise RuntimeError("fill has invalid side")
    if not isinstance(fill.lifecycle, str) or fill.lifecycle not in {item.value for item in Lifecycle}:
        raise RuntimeError("fill has invalid lifecycle")
    shares = _nonnegative_integer(fill.shares, field="fill shares", positive=True)
    price = _finite_number(fill.price, field="fill price", minimum=0.0)
    if price == 0.0:
        raise RuntimeError("fill price must be positive")
    gross = _finite_number(fill.gross_value, field="fill gross_value", minimum=0.0)
    expected_gross = shares * price
    if not math.isclose(gross, expected_gross, rel_tol=1e-12, abs_tol=0.01):
        raise RuntimeError("fill gross_value does not reconcile to shares * price")
    for name in ("commission", "stamp_duty", "transfer_fee", "slippage_cost"):
        _finite_number(getattr(fill, name), field=f"fill {name}", minimum=0.0)
    _required_text(fill.reason, field="fill reason")
    if not isinstance(fill.reduction_policy, str) or fill.reduction_policy not in {
        item.value for item in ReductionPolicy
    }:
        raise RuntimeError("fill has invalid reduction policy")
    _required_text(fill.reason_code, field="fill reason_code")
    _required_text(fill.exit_kind, field="fill exit_kind")
    if not isinstance(fill.order_id, str) or not isinstance(fill.fill_id, str):
        raise RuntimeError("fill identifiers must be text")
    if fill.fill_id and not fill.fill_id.strip():
        raise RuntimeError("fill_id cannot contain only whitespace")
    if validate_attribution:
        _validate_attribution_identity(fill, label="fill")

    order = ledger.get(fill.order_id) if fill.order_id else None
    if fill.order_id and order is None:
        raise RuntimeError("fill references an unknown account order")
    if order is not None:
        fields_that_must_match = (
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
        changed = [name for name in fields_that_must_match if getattr(fill, name) != getattr(order, name)]
        if changed:
            raise RuntimeError("fill metadata differs from account order: " + ", ".join(changed))
        submitted_date = _required_iso_date(
            order.submitted_date,
            field="account order submitted_date",
        )
        if fill_date <= submitted_date:
            raise RuntimeError("fill date must be after its order submission date")

    if not isinstance(fill.sold_tranches, list):
        raise RuntimeError("fill sold-lot attribution must be an array")
    attributed_shares = 0
    allocated_fee_totals = {
        name: 0.0 for name in ("commission", "stamp_duty", "transfer_fee", "slippage_cost")
    }
    allocations_with_fee_detail = 0
    for allocation in fill.sold_tranches:
        if not isinstance(allocation, dict):
            raise RuntimeError("fill sold-lot attribution must contain objects")
        attributed_shares += _nonnegative_integer(
            allocation.get("shares"),
            field="fill sold-lot shares",
            positive=True,
        )
        _required_text(allocation.get("tranche_id"), field="fill sold-lot tranche_id")
        lifecycle = allocation.get("lifecycle")
        if not isinstance(lifecycle, str) or lifecycle not in {item.value for item in Lifecycle}:
            raise RuntimeError("fill sold-lot attribution has invalid lifecycle")
        if "entry_date" in allocation:
            _required_iso_date(allocation["entry_date"], field="fill sold-lot entry_date")
        numeric_fields = (
            "cost",
            "unit_cost",
            "avg_cost",
            "cost_basis",
            "commission",
            "stamp_duty",
            "transfer_fee",
            "slippage_cost",
            "fees",
            "transaction_costs",
        )
        for name in numeric_fields:
            if name in allocation:
                _finite_number(
                    allocation[name],
                    field=f"fill sold-lot {name}",
                    minimum=0.0,
                )
        unit_cost_aliases = {
            name: _finite_number(
                allocation[name],
                field=f"fill sold-lot {name}",
                minimum=0.0,
            )
            for name in ("cost", "unit_cost", "avg_cost")
            if name in allocation
        }
        if unit_cost_aliases:
            reference_cost = next(iter(unit_cost_aliases.values()))
            if any(
                not math.isclose(value, reference_cost, rel_tol=1e-12, abs_tol=1e-8)
                for value in unit_cost_aliases.values()
            ):
                raise RuntimeError("fill sold-lot unit-cost aliases differ")
            if "cost_basis" in allocation:
                cost_basis = _finite_number(
                    allocation["cost_basis"],
                    field="fill sold-lot cost_basis",
                    minimum=0.0,
                )
                expected_basis = int(allocation["shares"]) * reference_cost
                if not math.isclose(
                    cost_basis,
                    expected_basis,
                    rel_tol=1e-12,
                    abs_tol=0.01,
                ):
                    raise RuntimeError("fill sold-lot cost_basis does not reconcile to shares * unit_cost")
        elif "cost_basis" in allocation:
            raise RuntimeError("fill sold-lot cost_basis requires a unit-cost alias")

        if "mfe" in allocation:
            _finite_number(
                allocation["mfe"],
                field="fill sold-lot mfe",
                minimum=0.0,
            )
        if "mae" in allocation:
            _finite_number(
                allocation["mae"],
                field="fill sold-lot mae",
                maximum=0.0,
            )
        if "entry_score" in allocation:
            _finite_number(
                allocation["entry_score"],
                field="fill sold-lot entry_score",
            )
        if "entry_confidence" in allocation:
            _finite_number(
                allocation["entry_confidence"],
                field="fill sold-lot entry_confidence",
                minimum=0.0,
                maximum=1.0,
            )
        if "entry_regime" in allocation:
            entry_regime = allocation["entry_regime"]
            if not isinstance(entry_regime, str) or entry_regime not in {item.value for item in Opportunity}:
                raise RuntimeError("fill sold-lot attribution has invalid entry_regime")
        if "entry_industry_strength" in allocation:
            _finite_number(
                allocation["entry_industry_strength"],
                field="fill sold-lot entry_industry_strength",
            )
        if validate_attribution:
            allocation_identity = SimpleNamespace(**allocation)
            _validate_attribution_identity(
                allocation_identity,
                label="fill sold-lot attribution",
            )

        fee_components = tuple(allocated_fee_totals)
        has_fee_detail = any(name in allocation for name in (*fee_components, "fees", "transaction_costs"))
        if has_fee_detail:
            if any(name not in allocation for name in fee_components):
                raise RuntimeError("fill sold-lot fee detail is incomplete")
            allocations_with_fee_detail += 1
            component_values = {
                name: _finite_number(
                    allocation[name],
                    field=f"fill sold-lot {name}",
                    minimum=0.0,
                )
                for name in fee_components
            }
            for name, value in component_values.items():
                allocated_fee_totals[name] += value
            if "fees" in allocation:
                expected_fees = sum(
                    component_values[name] for name in ("commission", "stamp_duty", "transfer_fee")
                )
                if not math.isclose(
                    float(allocation["fees"]),
                    expected_fees,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise RuntimeError("fill sold-lot fees alias does not reconcile")
            if "transaction_costs" in allocation:
                expected_costs = sum(component_values.values())
                if not math.isclose(
                    float(allocation["transaction_costs"]),
                    expected_costs,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise RuntimeError("fill sold-lot transaction_costs alias does not reconcile")
    if allocations_with_fee_detail:
        if allocations_with_fee_detail != len(fill.sold_tranches):
            raise RuntimeError("fill sold-lot fee detail must cover every allocation")
        for name, allocated in allocated_fee_totals.items():
            if not math.isclose(
                allocated,
                float(getattr(fill, name)),
                rel_tol=1e-12,
                abs_tol=1e-8,
            ):
                raise RuntimeError(f"fill sold-lot {name} does not reconcile to fill")
    schema_v2_missing_sell_attribution = bool(
        allow_schema_v2_missing_sell_attribution
        and fill.side == Side.SELL.value
        and fill.order_id
        and not fill.sold_tranches
    )
    if (
        fill.side == Side.SELL.value
        and fill.order_id
        and attributed_shares != shares
        and not schema_v2_missing_sell_attribution
    ):
        raise RuntimeError("linked sell fill sold-lot attribution does not reconcile")
    if fill.side == Side.SELL.value and not fill.order_id and attributed_shares not in {0, shares}:
        raise RuntimeError("unlinked sell fill sold-lot attribution does not reconcile")
    if fill.side == Side.BUY.value and attributed_shares:
        raise RuntimeError("buy fill cannot contain sold-lot attribution")


_SHOCK_STATES = {
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
_SHOCK_SEVERITIES = {
    "NORMAL",
    "MARKET",
    "CONCENTRATED",
    "SEVERE",
    "ANCHOR_BREAK",  # Accepted when normalizing compatible durable accounts.
    "COHORT_BREAK",
    "INCOMPLETE_UNIVERSE",
    "INCOMPLETE_UNIVERSE_UNBACKED",
}


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


def _validate_risk_streaks(values: Any) -> None:
    """Validate streak counters plus the signed opportunity evidence sentinel."""

    if not isinstance(values, dict):
        raise RuntimeError("risk_streaks must be an object")
    for key, value in values.items():
        _required_text(key, field="risk_streaks key")
        if key == "opportunity_evidence":
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value not in {-1, 0, 1}
            ):
                raise RuntimeError(
                    "risk_streaks[opportunity_evidence] must be -1, 0, or 1"
                )
            continue
        _nonnegative_integer(value, field=f"risk_streaks[{key}]")


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


def _validate_audit_events(state: AccountState) -> None:
    """Validate structured strategy and lifecycle audit events in an account."""

    lifecycles = {item.value for item in Lifecycle}
    risks = {item.value for item in Risk}

    for event in _validate_event_array(
        state.replacement_events,
        field="replacement_events",
    ):
        _required_iso_date(event.get("signal_date"), field="replacement event signal_date")
        _required_text(event.get("old_symbol"), field="replacement event old_symbol")
        _required_text(event.get("new_symbol"), field="replacement event new_symbol")
        for name in ("old_close", "new_close"):
            value = _finite_number(
                event.get(name),
                field=f"replacement event {name}",
                minimum=0.0,
            )
            if value == 0.0:
                raise RuntimeError(f"replacement event {name} must be positive")
        _finite_number(event.get("edge"), field="replacement event edge")
        if "industry_handoff" in event and type(event["industry_handoff"]) is not bool:
            raise RuntimeError("replacement event industry_handoff must be boolean")
        if "route" in event:
            _required_text(event["route"], field="replacement event route")

    for event in _validate_event_array(
        state.lifecycle_events,
        field="lifecycle_events",
    ):
        _required_iso_date(event.get("date"), field="lifecycle event date")
        _required_text(event.get("symbol"), field="lifecycle event symbol")
        if event.get("from") not in {*lifecycles, "NONE"}:
            raise RuntimeError("lifecycle event has invalid from lifecycle")
        if event.get("to") not in lifecycles:
            raise RuntimeError("lifecycle event has invalid to lifecycle")
        _nonnegative_integer(
            event.get("shares"),
            field="lifecycle event shares",
            positive=True,
        )
        _required_text(event.get("reason"), field="lifecycle event reason")

    for event in _validate_event_array(state.risk_events, field="risk_events"):
        _required_iso_date(event.get("date"), field="risk event date")
        event_name = event.get("event")
        has_transition = "from" in event or "to" in event
        if event_name is not None and event_name not in {
            "sector_guard_on",
            "sector_guard_off",
        }:
            raise RuntimeError("risk event has invalid event type")
        if has_transition and (event.get("from") not in risks or event.get("to") not in risks):
            raise RuntimeError("risk event has invalid risk transition")
        for name in ("votes", "shock_count", "active_sessions"):
            if name in event:
                _nonnegative_integer(event[name], field=f"risk event {name}")
        if "reasons" in event:
            reasons = event["reasons"]
            if not isinstance(reasons, list) or any(
                not isinstance(reason, str) or not reason.strip() for reason in reasons
            ):
                raise RuntimeError("risk event reasons must be an array of text")
        if "severity" in event and event["severity"] not in _SHOCK_SEVERITIES:
            raise RuntimeError("risk event has invalid severity")
        if "route" in event:
            _required_text(event["route"], field="risk event route")
        for name in (
            "leadership_divergence",
            "equal_weight_return",
            "exposure_weighted_return",
        ):
            _optional_finite_event_number(event, name, field="risk event")

    reconciliation_event_types = {
        "sell_lot_attribution_incomplete",
        "broker_share_deficit_reconciled",
        "economic_lot_degraded",
    }
    for event in _validate_event_array(
        state.reconciliation_events,
        field="reconciliation_events",
    ):
        _required_iso_date(event.get("date"), field="reconciliation event date")
        _required_text(event.get("symbol"), field="reconciliation event symbol")
        if event.get("event") not in reconciliation_event_types:
            raise RuntimeError("reconciliation event has invalid event type")
        for name in (
            "shares",
            "broker_shares",
            "attributed_shares",
            "degraded_shares",
            "unmatched_shares",
        ):
            if name in event:
                _nonnegative_integer(event[name], field=f"reconciliation event {name}")
        if "reason" in event:
            _required_text(event["reason"], field="reconciliation event reason")
        if "quality" in event and event["quality"] != "degraded_external_inventory":
            raise RuntimeError("reconciliation event has invalid quality")
        if "default_lifecycle" in event and event["default_lifecycle"] not in lifecycles:
            raise RuntimeError("reconciliation event has invalid default_lifecycle")
        if "default_entry_date" in event:
            _required_iso_date(
                event["default_entry_date"],
                field="reconciliation event default_entry_date",
            )
        if "default_highest_close" in event:
            default_highest = _finite_number(
                event["default_highest_close"],
                field="reconciliation event default_highest_close",
                minimum=0.0,
            )
            if default_highest == 0.0:
                raise RuntimeError("reconciliation event default_highest_close must be positive")


def _validate_strategy_risk_state(state: AccountState) -> None:
    """Validate durable strategy/risk state shared by save, load, and broker sync.

    These checks deliberately constrain only durable invariants.  They do not
    require anchor symbols to be held because a causal next-open buy or a
    broker-authoritative exit can temporarily separate targets from positions.
    """
    _finite_number(state.operating_peak, field="operating_peak", minimum=0.0)
    _finite_number(state.capital_peak, field="capital_peak", minimum=0.0)
    if not isinstance(state.opportunity, str) or state.opportunity not in {
        item.value for item in Opportunity
    }:
        raise RuntimeError("account state has invalid opportunity")
    if not isinstance(state.risk, str) or state.risk not in {item.value for item in Risk}:
        raise RuntimeError("account state has invalid risk")
    if not isinstance(state.shock_state, str) or state.shock_state not in _SHOCK_STATES:
        raise RuntimeError("account state has invalid shock_state")
    if not isinstance(state.shock_severity, str) or state.shock_severity not in _SHOCK_SEVERITIES:
        raise RuntimeError("account state has invalid shock_severity")
    if not isinstance(state.data_hash, str) or not isinstance(state.code_hash, str):
        raise RuntimeError("account validation hashes must be text")

    _validate_weight_map(state.anchor_weights, field="anchor_weights")
    if not isinstance(state.recovery_conviction_symbol, str):
        raise RuntimeError("account state has invalid recovery_conviction_symbol")
    _validate_weight_map(state.protected_weights, field="protected_weights")
    cohort_keys = _validate_symbol_list(
        state.strategic_cohort_symbols,
        field="strategic_cohort_symbols",
    )
    target_keys = _validate_weight_map(
        state.strategic_cohort_targets,
        field="strategic_cohort_targets",
    )
    restore_keys = _validate_weight_map(
        state.strategic_restore_weights,
        field="strategic_restore_weights",
    )
    if not target_keys <= cohort_keys or not restore_keys <= cohort_keys:
        raise RuntimeError("strategic weights reference symbols outside the cohort")
    if not isinstance(state.strategic_exit_bands, dict) or not isinstance(
        state.strategic_active_bands,
        dict,
    ):
        raise RuntimeError("strategic band state must be objects")
    band_keys = set(state.strategic_exit_bands)
    if band_keys != set(state.strategic_active_bands):
        raise RuntimeError("strategic exit/active band keys differ")
    if not band_keys <= cohort_keys:
        raise RuntimeError("strategic bands reference symbols outside the cohort")
    total_band_weight = 0.0
    for symbol, bands in state.strategic_exit_bands.items():
        _required_text(symbol, field="strategic band symbol")
        active = state.strategic_active_bands[symbol]
        if not isinstance(bands, list) or not bands:
            raise RuntimeError("strategic exit bands must be non-empty arrays")
        if not isinstance(active, list) or len(active) != len(bands):
            raise RuntimeError("strategic exit/active band lengths differ")
        if any(type(item) is not bool for item in active):
            raise RuntimeError("strategic active bands must contain booleans")
        for index, weight in enumerate(bands):
            total_band_weight += _finite_number(
                weight,
                field=f"strategic_exit_bands[{symbol}][{index}]",
                minimum=0.0,
                maximum=1.0,
            )
    if total_band_weight > 1.0 + 1e-6:
        raise RuntimeError("strategic exit band total weight exceeds one")

    _validate_nonnegative_integer_map(state.leader_tenure, field="leader_tenure")
    _validate_nonnegative_integer_map(state.candidate_tenure, field="candidate_tenure")
    _validate_nonnegative_integer_map(state.replacement_tenure, field="replacement_tenure")
    _validate_risk_streaks(state.risk_streaks)
    for field, value in (
        ("sector_recovery_streak", state.sector_recovery_streak),
        ("dynamic_k", state.dynamic_k),
        ("strategic_epoch", state.strategic_epoch),
        ("strategic_epochs_completed", state.strategic_epochs_completed),
        ("risk_anchor_candidate_streak", state.risk_anchor_candidate_streak),
        ("capital_budget_level", state.capital_budget_level),
        ("capital_budget_repair_streak", state.capital_budget_repair_streak),
        ("chronic_level", state.chronic_level),
        ("chronic_streak", state.chronic_streak),
        ("chronic_repair_streak", state.chronic_repair_streak),
    ):
        _nonnegative_integer(value, field=field)
    if state.capital_budget_level > 4:
        raise RuntimeError("capital_budget_level exceeds its supported ladder")
    if state.chronic_level > 3:
        raise RuntimeError("chronic_level exceeds its supported ladder")
    if not isinstance(state.risk_signal_state, dict):
        raise RuntimeError("risk_signal_state must be an object")
    for key, signal_value in state.risk_signal_state.items():
        _required_text(key, field="risk_signal_state key")
        lower_bound = -1.000001 if key == "correlation" else 0.0
        upper_bound = (
            1.000001
            if key
            in {
                "breadth20",
                "breadth60",
                "leader_failure",
                "correlation",
                "transition_damage",
                "trend_health",
            }
            else None
        )
        _finite_number(
            signal_value,
            field=f"risk_signal_state[{key}]",
            minimum=lower_bound,
            maximum=upper_bound,
        )

    _validate_symbol_list(state.strategic_previous_symbols, field="strategic_previous_symbols")
    _validate_symbol_list(state.risk_anchor_symbols, field="risk_anchor_symbols")
    _validate_symbol_list(state.sector_guard_symbols, field="sector_guard_symbols")
    _validate_symbol_list(state.active_leaders, field="active_leaders")
    _validate_symbol_list(state.data_hash_symbols, field="data_hash_symbols")
    if not isinstance(state.sector_guard_active, bool):
        raise RuntimeError("sector_guard_active must be boolean")
    if state.sector_guard_active and not state.sector_guard_started:
        raise RuntimeError("active sector guard requires sector_guard_started")
    if not isinstance(state.sector_shock_dates, list):
        raise RuntimeError("sector_shock_dates must be an array")
    if not isinstance(state.rotation_dates, list):
        raise RuntimeError("rotation_dates must be an array")
    if not isinstance(state.satellite_entry_dates, dict):
        raise RuntimeError("satellite_entry_dates must be an object")
    for shock_date in state.sector_shock_dates:
        _required_iso_date(shock_date, field="sector_shock_dates")
    for rotation_date in state.rotation_dates:
        _required_iso_date(rotation_date, field="rotation_dates")
    for symbol, entry_date in state.satellite_entry_dates.items():
        _required_text(symbol, field="satellite_entry_dates key")
        _required_iso_date(entry_date, field=f"satellite_entry_dates[{symbol}]")
    for date_field, optional_date in (
        ("sector_guard_started", state.sector_guard_started),
        ("cooldown_until", state.cooldown_until),
        ("last_k_change_date", state.last_k_change_date),
        ("recovery_anchor_date", state.recovery_anchor_date),
        ("strategic_last_exit_date", state.strategic_last_exit_date),
        ("strategic_rearm_date", state.strategic_rearm_date),
        ("scout_entry_date", state.scout_entry_date),
        ("shock_start_date", state.shock_start_date),
        ("last_shock_date", state.last_shock_date),
        ("last_successful_run", state.last_successful_run),
        ("data_hash_as_of", state.data_hash_as_of),
    ):
        _optional_iso_date(optional_date, field=date_field)
    _validate_audit_events(state)


def _tranche(payload: dict[str, Any], *, schema_version: int) -> Tranche:
    """Load a tranche while deriving safe current-schema economic metadata."""
    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    if native_schema:
        avg_cost = payload.get("avg_cost", 0.0)
        highest = payload.get("highest_close", avg_cost)
        lowest = payload.get("lowest_close", avg_cost)
    else:
        avg_cost = float(payload.get("avg_cost", 0.0))
        highest = float(payload.get("highest_close", avg_cost))
        lowest = float(payload.get("lowest_close", avg_cost))
        if lowest <= 0:
            lowest = avg_cost
    convert_text = (lambda value: value) if native_schema else str
    convert_int = (lambda value: value) if native_schema else int
    convert_float = (lambda value: value) if native_schema else float
    return Tranche(
        tranche_id=convert_text(payload["tranche_id"]),
        lifecycle=convert_text(payload.get("lifecycle", "CORE")),
        shares=convert_int(payload.get("shares", 0)),
        avg_cost=avg_cost,
        entry_date=convert_text(payload.get("entry_date", "")),
        sellable_date=convert_text(payload.get("sellable_date", "")),
        highest_close=highest,
        lowest_close=lowest,
        mfe=convert_float(
            payload.get(
                "mfe",
                max(
                    0.0,
                    float(highest) / max(float(avg_cost), 1e-12) - 1.0,
                ),
            )
        ),
        mae=convert_float(
            payload.get(
                "mae",
                min(
                    0.0,
                    float(lowest) / max(float(avg_cost), 1e-12) - 1.0,
                ),
            )
        ),
        entry_score=convert_float(payload.get("entry_score", 0.0)),
        entry_confidence=convert_float(payload.get("entry_confidence", 0.0)),
        entry_regime=convert_text(payload.get("entry_regime", "CHOPPY")),
        entry_industry_strength=convert_float(payload.get("entry_industry_strength", 0.0)),
        event_id=convert_text(payload.get("event_id", "")),
        origin_subsystem=convert_text(payload.get("origin_subsystem", "")),
        mechanism=convert_text(payload.get("mechanism", "")),
        origin_lifecycle=convert_text(payload.get("origin_lifecycle", "")),
        replaces_symbol=payload.get("replaces_symbol"),
        industry_at_entry=convert_text(payload.get("industry_at_entry", "")),
        industry_manifest_sha256=convert_text(
            payload.get("industry_manifest_sha256", "")
        ),
    )


def _position(payload: dict[str, Any], *, schema_version: int) -> Position:
    """Decode a position and reconcile aggregate shares with its tranche lots."""

    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    convert_text = (lambda value: value) if native_schema else str
    convert_int = (lambda value: value) if native_schema else int
    convert_float = (lambda value: value) if native_schema else float
    position = Position(
        symbol=convert_text(payload["symbol"]),
        shares=convert_int(payload.get("shares", 0)),
        avg_cost=convert_float(payload.get("avg_cost", 0.0)),
        entry_date=convert_text(payload.get("entry_date", "")),
        highest_close=convert_float(payload.get("highest_close", 0.0)),
        lifecycle=convert_text(payload.get("lifecycle", "CORE")),
        tranches=[_tranche(item, schema_version=schema_version) for item in payload.get("tranches", [])],
    )
    if schema_version < _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION and position.shares > 0:
        known_shares = sum(item.shares for item in position.tranches)
        if known_shares > position.shares:
            raise ValueError("compatible position tranches exceed aggregate shares")
        residual = position.shares - known_shares
        if residual:
            entry_date = position.entry_date or "0001-01-01"
            highest_close = (
                position.highest_close
                if math.isfinite(position.highest_close) and position.highest_close > 0
                else position.avg_cost
            )
            position.entry_date = entry_date
            position.highest_close = highest_close
            position.tranches.append(
                Tranche(
                    tranche_id=f"legacy:{position.symbol}:{len(position.tranches) + 1}",
                    lifecycle=position.lifecycle,
                    shares=residual,
                    avg_cost=position.avg_cost,
                    entry_date=entry_date,
                    # Equality preserves "already sellable" semantics while
                    # keeping the current causal date invariant.
                    sellable_date=entry_date,
                    highest_close=highest_close,
                    lowest_close=position.avg_cost,
                )
            )
    return position


def _validate_position_state(
    state: AccountState,
    *,
    validate_attribution: bool = False,
) -> None:
    """Reject durable positions whose aggregate and lot inventories diverge."""
    lifecycles = {item.value for item in Lifecycle}
    try:
        _optional_iso_date(state.broker_as_of, field="broker_as_of")
    except RuntimeError as exc:
        raise RuntimeError("account state has invalid broker_as_of") from exc
    for key, position in state.positions.items():
        _required_text(key, field="account position key")
        _required_text(position.symbol, field="account position symbol")
        if key != position.symbol:
            raise RuntimeError("account position key differs from its symbol")
        _nonnegative_integer(position.shares, field="account position shares", positive=True)
        position_cost = _finite_number(
            position.avg_cost,
            field="account position cost",
            minimum=0.0,
        )
        if position_cost == 0.0:
            raise RuntimeError("account position cost must be positive")
        highest_close = _finite_number(
            position.highest_close,
            field="account position highest close",
            minimum=0.0,
        )
        if highest_close == 0.0:
            raise RuntimeError("account position highest close must be positive")
        if position.lifecycle not in lifecycles:
            raise RuntimeError("account position has invalid lifecycle")
        _required_iso_date(position.entry_date, field="account position entry date")
        if not position.tranches:
            raise RuntimeError("account position requires tranche inventory")
        tranche_ids = [item.tranche_id for item in position.tranches]
        for tranche_id in tranche_ids:
            _required_text(tranche_id, field="account tranche id")
        if not all(tranche_ids) or len(tranche_ids) != len(set(tranche_ids)):
            raise RuntimeError("account position has invalid tranche ids")
        for tranche in position.tranches:
            _nonnegative_integer(tranche.shares, field="account tranche shares", positive=True)
            tranche_cost = _finite_number(
                tranche.avg_cost,
                field="account tranche cost",
                minimum=0.0,
            )
            if tranche_cost == 0.0:
                raise RuntimeError("account tranche cost must be positive")
            tranche_high = _finite_number(
                tranche.highest_close,
                field="account tranche highest close",
                minimum=0.0,
            )
            tranche_low = _finite_number(
                tranche.lowest_close,
                field="account tranche lowest close",
                minimum=0.0,
            )
            if tranche_high == 0.0 or tranche_low == 0.0:
                raise RuntimeError("account tranche prices must be positive")
            if tranche.lifecycle not in lifecycles:
                raise RuntimeError("account tranche has invalid lifecycle")
            if not tranche.entry_date or not tranche.sellable_date:
                raise RuntimeError("account tranche requires entry and sellable dates")
            entry_date = _required_iso_date(
                tranche.entry_date,
                field="account tranche entry date",
            )
            sellable_date = _required_iso_date(
                tranche.sellable_date,
                field="account tranche sellable date",
            )
            if sellable_date < entry_date:
                raise RuntimeError("account tranche sellable date predates entry date")
            _finite_number(tranche.mfe, field="account tranche mfe", minimum=0.0)
            _finite_number(tranche.mae, field="account tranche mae", maximum=0.0)
            _finite_number(tranche.entry_score, field="account tranche entry_score")
            _finite_number(
                tranche.entry_confidence,
                field="account tranche entry_confidence",
                minimum=0.0,
                maximum=1.0,
            )
            if not isinstance(tranche.entry_regime, str) or tranche.entry_regime not in {
                item.value for item in Opportunity
            }:
                raise RuntimeError("account tranche has invalid entry_regime")
            _finite_number(
                tranche.entry_industry_strength,
                field="account tranche entry_industry_strength",
            )
            if validate_attribution:
                _validate_attribution_identity(
                    tranche,
                    label="account tranche",
                )
        if position.shares != sum(item.shares for item in position.tranches):
            raise RuntimeError("account position shares do not reconcile to tranches")


def _order_sequence(order_id: str) -> int:
    if (
        not isinstance(order_id, str)
        or len(order_id) != 10
        or not order_id.startswith("O")
        or not order_id[1:].isdigit()
    ):
        raise RuntimeError(f"account state has invalid order id: {order_id!r}")
    sequence = int(order_id[1:])
    if sequence <= 0:
        raise RuntimeError(f"account state has invalid order id: {order_id!r}")
    return sequence


def _validate_order_state(
    state: AccountState,
    *,
    sequence_was_explicit: bool,
    allow_schema_v2_missing_sell_attribution: bool = False,
    validate_attribution: bool = False,
    event_schema_version: int = ACCOUNT_SCHEMA_VERSION,
) -> None:
    """Validate order identifiers, lifecycle transitions, fills, and references."""

    _nonnegative_integer(
        state.next_order_sequence,
        field="account state next order sequence",
        positive=True,
    )
    identifiers = [item.order_id for item in state.order_ledger]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("account state has duplicate order ids")
    sequences = [_order_sequence(order_id) for order_id in identifiers]
    required_next = max(sequences, default=0) + 1
    if state.next_order_sequence > 999_999_999:
        raise RuntimeError("account state next order sequence exceeds the canonical ID space")
    if sequence_was_explicit and event_schema_version == ACCOUNT_SCHEMA_VERSION:
        if state.next_order_sequence < required_next:
            raise RuntimeError("account state next order sequence would reuse an order id")
        if state.next_order_sequence > required_next:
            raise RuntimeError(
                "account state next order sequence does not exactly follow the durable ledger"
            )
    elif sequence_was_explicit and state.next_order_sequence < required_next:
        raise RuntimeError("account state next order sequence would reuse an order id")
    state.next_order_sequence = max(state.next_order_sequence, required_next)
    if state.next_order_sequence <= 0:
        raise RuntimeError("account state has invalid next order sequence")

    statuses = {item.value for item in OrderStatus}
    reduction_policies = {item.value for item in ReductionPolicy}
    ledger = {ledger_item.order_id: ledger_item for ledger_item in state.order_ledger}
    for ledger_item in state.order_ledger:
        signal_date = _validate_order_intent(
            ledger_item,
            label="account order",
            validate_attribution=validate_attribution,
            event_schema_version=event_schema_version,
        )
        submitted_date = _required_iso_date(
            ledger_item.submitted_date,
            field="account order submitted_date",
        )
        if submitted_date < signal_date:
            raise RuntimeError("account order submission predates its signal")
        if ledger_item.last_update_date:
            last_update = _required_iso_date(
                ledger_item.last_update_date,
                field="account order last_update_date",
            )
            if last_update < submitted_date:
                raise RuntimeError("account order update predates its submission")
        if not isinstance(ledger_item.last_event, str) or not ledger_item.last_event.strip():
            raise RuntimeError("account order last_event must be non-empty text")
        if not isinstance(ledger_item.replaced_by, str) or not isinstance(
            ledger_item.cancel_reason,
            str,
        ):
            raise RuntimeError("account order replacement metadata must be text")
        if ledger_item.replaced_by:
            _order_sequence(ledger_item.replaced_by)
        if ledger_item.status not in statuses:
            raise RuntimeError(f"account state has invalid order status: {ledger_item.status!r}")
        requested = _nonnegative_integer(
            ledger_item.requested_shares,
            field="account order requested_shares",
        )
        filled = _nonnegative_integer(
            ledger_item.filled_shares,
            field="account order filled_shares",
        )
        remaining = _nonnegative_integer(
            ledger_item.remaining_shares,
            field="account order remaining_shares",
        )
        _nonnegative_integer(ledger_item.attempts, field="account order attempts")
        if filled + remaining != requested:
            raise RuntimeError("account order requested shares do not equal filled plus remaining")
        if ledger_item.status == OrderStatus.PARTIALLY_FILLED.value and (filled == 0 or remaining == 0):
            raise RuntimeError("partially filled order requires filled and remaining shares")
        if ledger_item.status == OrderStatus.FILLED.value and remaining:
            raise RuntimeError("filled order cannot retain remaining shares")
        if ledger_item.reduction_policy not in reduction_policies:
            raise RuntimeError("account state has invalid reduction policy")
        if not ledger_item.exit_kind or not ledger_item.reason_code:
            raise RuntimeError("account state has invalid exit attribution")

    pending_ids = [item.order_id for item in state.pending_orders if item.order_id]
    if len(pending_ids) != len(set(pending_ids)):
        raise RuntimeError("account state has duplicate pending order ids")
    terminal = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    for order_id in pending_ids:
        pending_account_order = ledger.get(order_id)
        if pending_account_order is None:
            raise RuntimeError("pending order references an unknown account order")
        if pending_account_order.status in terminal:
            raise RuntimeError("pending order references a terminal account order")
        pending = next(item for item in state.pending_orders if item.order_id == order_id)
        pending_metadata = order_intent_metadata(pending)
        ledger_metadata = order_intent_metadata(pending_account_order)
        if pending_metadata != ledger_metadata:
            changed = [
                name
                for name, pending_value, ledger_value in zip(
                    ORDER_INTENT_IMMUTABLE_FIELDS,
                    pending_metadata,
                    ledger_metadata,
                    strict=True,
                )
                if pending_value != ledger_value
            ]
            raise RuntimeError(
                "pending order immutable metadata differs from account order: " + ", ".join(changed)
            )
        if pending.remaining_shares != pending_account_order.remaining_shares:
            raise RuntimeError("pending order remaining shares differ from account order")
        if pending.attempts != pending_account_order.attempts:
            raise RuntimeError("pending order attempts differ from account order")
    for pending_item in state.pending_orders:
        _validate_order_intent(
            pending_item,
            label="pending order",
            validate_attribution=validate_attribution,
            event_schema_version=event_schema_version,
        )
        _nonnegative_integer(
            pending_item.remaining_shares,
            field="pending order remaining_shares",
        )
        _nonnegative_integer(pending_item.attempts, field="pending order attempts")
        if not isinstance(pending_item.order_id, str):
            raise RuntimeError("pending order id must be text")
        if pending_item.order_id:
            _order_sequence(pending_item.order_id)
        if pending_item.reduction_policy not in reduction_policies:
            raise RuntimeError("pending order has invalid reduction policy")
        if not pending_item.exit_kind or not pending_item.reason_code:
            raise RuntimeError("pending order has invalid exit attribution")
    for ledger_item in state.order_ledger:
        if ledger_item.replaced_by and ledger_item.replaced_by not in ledger:
            raise RuntimeError("replaced order references an unknown replacement")
    for fill in state.fills:
        _validate_fill(
            fill,
            ledger=ledger,
            allow_schema_v2_missing_sell_attribution=(allow_schema_v2_missing_sell_attribution),
            validate_attribution=validate_attribution,
        )
    fill_ids = [fill.fill_id for fill in state.fills if fill.fill_id]
    if len(fill_ids) != len(set(fill_ids)):
        raise RuntimeError("account state has duplicate broker fill ids")

    linked_fills: dict[str, list[Fill]] = {order_id: [] for order_id in ledger}
    unlinked_fills = [fill for fill in state.fills if not fill.order_id]

    def unlinked_identity_matches(fill: Fill, order: AccountOrder) -> bool:
        return _unlinked_fill_matches_order(
            fill,
            order,
            native=validate_attribution,
        )

    for fill in unlinked_fills:
        structured_matches = sum(
            unlinked_identity_matches(fill, candidate)
            for candidate in state.order_ledger
        )
        if validate_attribution and structured_matches != 1:
            raise RuntimeError(
                "native unlinked fill must match exactly one structured account order"
            )
        if not validate_attribution and structured_matches > 1:
            raise RuntimeError("unlinked fill has ambiguous structured order identity")

    for fill in state.fills:
        if fill.order_id:
            linked_fills[fill.order_id].append(fill)
    for ledger_item in state.order_ledger:
        order_fills = linked_fills[ledger_item.order_id]
        accounted_fill_shares = sum(fill.shares for fill in order_fills)
        # Some accepted account payloads contain fills without broker-visible
        # order IDs. Only a unique immutable-identity match may link them.
        unlinked_matches = [
            fill for fill in unlinked_fills if unlinked_identity_matches(fill, ledger_item)
        ]
        unlinked_match_shares = sum(fill.shares for fill in unlinked_matches)
        unlinked_exempt = (
            not order_fills
            and bool(unlinked_matches)
            and unlinked_match_shares == ledger_item.filled_shares
            and all(
                sum(
                    unlinked_identity_matches(fill, candidate)
                    for candidate in state.order_ledger
                )
                == 1
                for fill in unlinked_matches
            )
        )
        if accounted_fill_shares != ledger_item.filled_shares and not unlinked_exempt:
            raise RuntimeError("account order filled shares do not reconcile to fills")
        if (
            ledger_item.status
            in {
                OrderStatus.SUBMITTED.value,
                OrderStatus.OPEN.value,
            }
            and ledger_item.filled_shares
        ):
            raise RuntimeError("unfilled order status cannot retain filled shares")
        if ledger_item.status == OrderStatus.FILLED.value and ledger_item.filled_shares == 0:
            raise RuntimeError("filled order requires at least one executed share")
        relevant_fills = order_fills or unlinked_matches
        if relevant_fills:
            if not ledger_item.last_update_date:
                raise RuntimeError("filled account order requires last_update_date")
            last_update = _required_iso_date(
                ledger_item.last_update_date,
                field="account order last_update_date",
            )
            if last_update < max(
                _required_iso_date(fill.fill_date, field="fill fill_date") for fill in relevant_fills
            ):
                raise RuntimeError("account order update predates its latest fill")


def _validate_lot_origin_chains(
    state: AccountState,
    *,
    schema_version: int = ACCOUNT_SCHEMA_VERSION,
) -> None:
    """Bind every native live/sold lot to a validated originating BUY."""

    legacy_migration_boundary = any(
        isinstance(event.get("from_schema"), int)
        and not isinstance(event.get("from_schema"), bool)
        and int(event["from_schema"]) < schema_version
        and event.get("to_schema") == schema_version
        and isinstance(event.get("migrated_at_utc"), str)
        and bool(str(event["migrated_at_utc"]).strip())
        and isinstance(event.get("from_code_hash"), str)
        and bool(str(event["from_code_hash"]).strip())
        and isinstance(event.get("to_code_hash"), str)
        and bool(str(event["to_code_hash"]).strip())
        for event in state.account_migrations
    )
    ledger = {order.order_id: order for order in state.order_ledger}

    def originating_buy_order(fill: Fill) -> AccountOrder | None:
        if fill.side != Side.BUY.value:
            return None
        if fill.order_id:
            candidate = ledger.get(fill.order_id)
            return candidate if candidate is not None and candidate.side == Side.BUY.value else None
        candidates = [
            order
            for order in state.order_ledger
            if order.side == Side.BUY.value
            and _unlinked_fill_matches_order(fill, order, native=True)
        ]
        return candidates[0] if len(candidates) == 1 else None

    buy_fills: dict[tuple[str, str], list[Fill]] = {}
    acquired_shares: dict[tuple[str, str], int] = {}
    for fill in state.fills:
        if originating_buy_order(fill) is None:
            continue
        key = (fill.symbol, fill.event_id)
        buy_fills.setdefault(key, []).append(fill)
        acquired_shares[key] = acquired_shares.get(key, 0) + fill.shares

    attributed_lot_shares: dict[tuple[str, str], int] = {}

    def validate_lot(
        lot: Any,
        *,
        symbol: str,
        shares: int,
        entry_date: str,
        label: str,
        sold_allocation: dict[str, Any] | None = None,
    ) -> None:
        legacy = bool(
            lot.origin_subsystem == OriginSubsystem.LEGACY_MIGRATION.value
            and lot.mechanism == AttributionMechanism.LEGACY_MIGRATION.value
        )
        if legacy:
            if not legacy_migration_boundary:
                raise RuntimeError(f"{label} legacy identity lacks an explicit migration boundary")
            return
        broker_degraded = bool(
            lot.origin_subsystem == OriginSubsystem.BROKER_RECONCILIATION.value
            and lot.mechanism == AttributionMechanism.BROKER_RECONCILIATION.value
        )
        if broker_degraded:
            if (
                sold_allocation is None
                or sold_allocation.get("degraded") is not True
                or not isinstance(sold_allocation.get("degradation_reason"), str)
                or not str(sold_allocation["degradation_reason"]).strip()
            ):
                raise RuntimeError(f"{label} broker reconciliation identity is not a degraded SELL")
            return

        key = (symbol, lot.event_id)
        candidates = buy_fills.get(key, [])
        matches = [
            fill
            for fill in candidates
            if fill.fill_date == entry_date
            and all(
                getattr(fill, field) == getattr(lot, field)
                for field in ATTRIBUTION_IDENTITY_FIELDS
            )
        ]
        if not matches:
            raise RuntimeError(f"{label} does not chain to an originating BUY")
        attributed_lot_shares[key] = attributed_lot_shares.get(key, 0) + shares

    for symbol, position in state.positions.items():
        for tranche in position.tranches:
            validate_lot(
                tranche,
                symbol=symbol,
                shares=tranche.shares,
                entry_date=tranche.entry_date,
                label="account tranche",
            )
    for fill in state.fills:
        for allocation in fill.sold_tranches:
            validate_lot(
                SimpleNamespace(**allocation),
                symbol=fill.symbol,
                shares=int(allocation["shares"]),
                entry_date=str(allocation.get("entry_date", "")),
                label="fill sold lot",
                sold_allocation=allocation,
            )

    for key, attributed in attributed_lot_shares.items():
        if attributed > acquired_shares.get(key, 0):
            raise RuntimeError(
                "native lot shares exceed originating BUY fill shares for "
                f"{key[0]} {key[1]}"
            )


def load_account(
    path: str | Path,
    *,
    require_hashes: bool = True,
    allow_legacy_schema: bool = False,
) -> AccountState:
    """Load and validate the durable account state from a JSON file.

    Validation rejects malformed order lifecycles, duplicate identifiers,
    negative balances, and missing provenance hashes when fail-closed operation
    is expected.
    """
    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"account state is missing or corrupt: {source}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("account state must be a JSON object")
    return account_from_dict(
        payload,
        require_hashes=require_hashes,
        allow_legacy_schema=allow_legacy_schema,
    )


def account_from_dict(
    value: Mapping[str, Any],
    *,
    require_hashes: bool = True,
    allow_legacy_schema: bool = False,
) -> AccountState:
    """Decode and fully validate an in-memory durable account payload."""

    payload = dict(value)
    raw_schema_version = payload.get("schema_version", 1)
    if isinstance(raw_schema_version, bool):
        raise RuntimeError("account state has an invalid schema version")
    if isinstance(raw_schema_version, int):
        schema_version = raw_schema_version
    else:
        try:
            schema_version = int(raw_schema_version)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("account state has an invalid schema version") from exc
        if (
            not allow_legacy_schema
            or schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
        ):
            raise RuntimeError("native account schema_version must be an integer")
    if schema_version > ACCOUNT_SCHEMA_VERSION or schema_version < 1:
        raise RuntimeError(f"unsupported account schema {schema_version}; expected {ACCOUNT_SCHEMA_VERSION}")
    if schema_version != ACCOUNT_SCHEMA_VERSION and not allow_legacy_schema:
        raise RuntimeError(
            f"account schema {schema_version} requires explicit migration; "
            "run `uquant account-migrate --help`"
        )
    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    sequence_was_explicit = "next_order_sequence" in payload
    operating_peak = payload.get("operating_peak")
    capital_peak = payload.get("capital_peak")
    if operating_peak is None:
        operating_peak = payload["initial_cash"]
    if capital_peak is None:
        capital_peak = payload["initial_cash"]
    try:
        state = AccountState(
            initial_cash=(payload["initial_cash"] if native_schema else float(payload["initial_cash"])),
            cash=(payload["cash"] if native_schema else float(payload["cash"])),
            schema_version=schema_version,
            positions={
                symbol: _position(item, schema_version=schema_version)
                for symbol, item in payload.get("positions", {}).items()
            },
            pending_orders=[PendingOrder(**item) for item in payload.get("pending_orders", [])],
            order_ledger=[AccountOrder(**item) for item in payload.get("order_ledger", [])],
            next_order_sequence=(
                payload.get("next_order_sequence", 1)
                if native_schema
                else int(payload.get("next_order_sequence", 1))
            ),
            fills=[Fill(**item) for item in payload.get("fills", [])],
            broker_as_of=payload.get("broker_as_of", ""),
            opportunity=(
                payload.get("opportunity", "CHOPPY")
                if native_schema
                else str(payload.get("opportunity", "CHOPPY"))
            ),
            risk=(payload.get("risk", "NORMAL") if native_schema else str(payload.get("risk", "NORMAL"))),
            shock_state=(
                payload.get("shock_state", "NONE")
                if native_schema
                else str(payload.get("shock_state", "NONE"))
            ),
            sector_shock_dates=payload.get("sector_shock_dates", []),
            sector_guard_active=payload.get("sector_guard_active", False),
            sector_guard_started=payload.get("sector_guard_started", ""),
            sector_guard_symbols=(
                payload.get("sector_guard_symbols", [])
                if native_schema
                else [str(item) for item in payload.get("sector_guard_symbols", [])]
            ),
            sector_recovery_streak=payload.get("sector_recovery_streak", 0),
            cooldown_until=payload.get("cooldown_until", ""),
            operating_peak=operating_peak,
            capital_peak=capital_peak,
            leader_tenure={str(k): v for k, v in payload.get("leader_tenure", {}).items()},
            candidate_tenure={str(k): v for k, v in payload.get("candidate_tenure", {}).items()},
            replacement_tenure={str(k): v for k, v in payload.get("replacement_tenure", {}).items()},
            active_leaders=(
                payload.get("active_leaders", [])
                if native_schema
                else [str(item) for item in payload.get("active_leaders", [])]
            ),
            dynamic_k=payload.get("dynamic_k", 0),
            last_k_change_date=payload.get("last_k_change_date", ""),
            satellite_entry_dates={str(k): v for k, v in payload.get("satellite_entry_dates", {}).items()},
            risk_streaks={str(k): v for k, v in payload.get("risk_streaks", {}).items()},
            rotation_dates=payload.get("rotation_dates", []),
            replacement_events=(
                payload.get("replacement_events", [])
                if native_schema
                else list(payload.get("replacement_events", []))
            ),
            lifecycle_events=(
                payload.get("lifecycle_events", [])
                if native_schema
                else list(payload.get("lifecycle_events", []))
            ),
            risk_events=(
                payload.get("risk_events", [])
                if native_schema
                else list(payload.get("risk_events", []))
            ),
            account_migrations=list(payload.get("account_migrations", [])),
            anchor_weights={str(k): v for k, v in payload.get("anchor_weights", {}).items()},
            recovery_anchor_date=payload.get("recovery_anchor_date", ""),
            recovery_conviction_symbol=(
                payload.get("recovery_conviction_symbol", "")
                if native_schema
                else str(payload.get("recovery_conviction_symbol", ""))
            ),
            tactical_anchor_symbol=(
                payload.get("tactical_anchor_symbol", "")
                if native_schema
                else str(payload.get("tactical_anchor_symbol", ""))
            ),
            protected_weights={str(k): v for k, v in payload.get("protected_weights", {}).items()},
            strategic_cohort_symbols=payload.get("strategic_cohort_symbols", []),
            strategic_cohort_targets={
                str(k): v for k, v in payload.get("strategic_cohort_targets", {}).items()
            },
            strategic_exit_bands={
                str(k): list(values) for k, values in payload.get("strategic_exit_bands", {}).items()
            },
            strategic_active_bands={
                str(k): list(values) for k, values in payload.get("strategic_active_bands", {}).items()
            },
            strategic_restore_weights={
                str(k): v for k, v in payload.get("strategic_restore_weights", {}).items()
            },
            strategic_epoch=payload.get("strategic_epoch", 0),
            strategic_epochs_completed=(
                payload.get(
                    "strategic_epochs_completed",
                    payload.get("candidate_tenure", {}).get("strategic_cohort_completed", 0),
                )
            ),
            strategic_last_exit_date=payload.get("strategic_last_exit_date", ""),
            strategic_rearm_date=payload.get("strategic_rearm_date", ""),
            strategic_candidate_signature=(
                payload.get("strategic_candidate_signature", "")
                if native_schema
                else str(payload.get("strategic_candidate_signature", ""))
            ),
            strategic_previous_symbols=payload.get("strategic_previous_symbols", []),
            risk_anchor_symbols=payload.get("risk_anchor_symbols", []),
            risk_anchor_signature=(
                payload.get("risk_anchor_signature", "")
                if native_schema
                else str(payload.get("risk_anchor_signature", ""))
            ),
            risk_anchor_candidate_signature=(
                payload.get("risk_anchor_candidate_signature", "")
                if native_schema
                else str(payload.get("risk_anchor_candidate_signature", ""))
            ),
            risk_anchor_candidate_streak=payload.get("risk_anchor_candidate_streak", 0),
            risk_signal_state={str(k): v for k, v in payload.get("risk_signal_state", {}).items()},
            capital_budget_level=payload.get("capital_budget_level", 0),
            capital_budget_repair_streak=payload.get("capital_budget_repair_streak", 0),
            chronic_level=payload.get("chronic_level", 0),
            chronic_streak=payload.get("chronic_streak", 0),
            chronic_repair_streak=payload.get("chronic_repair_streak", 0),
            scout_signature=(
                payload.get("scout_signature", "")
                if native_schema
                else str(payload.get("scout_signature", ""))
            ),
            scout_entry_date=payload.get("scout_entry_date", ""),
            reconciliation_events=(
                payload.get("reconciliation_events", [])
                if native_schema
                else list(payload.get("reconciliation_events", []))
            ),
            shock_start_date=payload.get("shock_start_date", ""),
            shock_severity=(
                payload.get("shock_severity", "NORMAL")
                if native_schema
                else str(payload.get("shock_severity", "NORMAL"))
            ),
            last_shock_date=payload.get("last_shock_date", ""),
            last_successful_run=payload.get("last_successful_run", ""),
            data_hash=(
                payload.get("data_hash", "")
                if native_schema
                else str(payload.get("data_hash", ""))
            ),
            data_hash_as_of=payload.get("data_hash_as_of", ""),
            data_hash_symbols=(
                payload.get("data_hash_symbols", [])
                if native_schema
                else [str(item) for item in payload.get("data_hash_symbols", [])]
            ),
            code_hash=(
                payload.get("code_hash", "")
                if native_schema
                else str(payload.get("code_hash", ""))
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("account state violates schema") from exc
    initial_cash = _finite_number(
        state.initial_cash,
        field="account state initial_cash",
        minimum=0.0,
    )
    cash = _finite_number(state.cash, field="account state cash", minimum=-1e-6)
    if initial_cash == 0.0 or cash < -1e-6:
        raise RuntimeError("account state violates cash invariants")
    validate_attribution = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    _validate_position_state(
        state,
        validate_attribution=validate_attribution,
    )
    _validate_order_state(
        state,
        sequence_was_explicit=sequence_was_explicit,
        allow_schema_v2_missing_sell_attribution=(allow_legacy_schema and schema_version == 2),
        validate_attribution=validate_attribution,
        event_schema_version=schema_version,
    )
    _validate_strategy_risk_state(state)
    if validate_attribution:
        _validate_lot_origin_chains(state, schema_version=schema_version)
    if require_hashes and (not state.data_hash or not state.code_hash):
        raise RuntimeError("account state missing validation hashes")
    return state


def _legacy_attribution_owner(
    reason_code: str,
    exit_kind: str,
    *,
    side: str,
) -> tuple[str, str, bool]:
    """Classify legacy stable codes without inspecting human-readable reason."""

    exact: dict[str, tuple[OriginSubsystem, AttributionMechanism]] = {
        "strategy_target": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_SELECTION,
        ),
        "rotation": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_ROTATION,
        ),
        "lifecycle_exit": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_LIFECYCLE_EXIT,
        ),
        "challenger_scout": (
            OriginSubsystem.LEADER,
            AttributionMechanism.CHALLENGER_SCOUT,
        ),
        "satellite_expiry": (
            OriginSubsystem.LEADER,
            AttributionMechanism.SATELLITE_EXPIRY,
        ),
        "recovery_cohort": (
            OriginSubsystem.RECOVERY,
            AttributionMechanism.RECOVERY_COHORT,
        ),
        "recovery_exit": (
            OriginSubsystem.RECOVERY,
            AttributionMechanism.TACTICAL_REBOUND,
        ),
        "strategic_cohort": (
            OriginSubsystem.STRATEGIC,
            AttributionMechanism.STRATEGIC_COHORT,
        ),
        "strategic_tail": (
            OriginSubsystem.STRATEGIC,
            AttributionMechanism.STRATEGIC_TRAILING_EXIT,
        ),
        "risk_gross_cap": (
            OriginSubsystem.RISK,
            AttributionMechanism.RISK_GROSS_CAP,
        ),
        "sector_guard": (
            OriginSubsystem.RISK,
            AttributionMechanism.SECTOR_GUARD,
        ),
        "strategic_damage_guard": (
            OriginSubsystem.RISK,
            AttributionMechanism.STRATEGIC_DAMAGE_GUARD,
        ),
        "risk_off": (OriginSubsystem.RISK, AttributionMechanism.RISK_OFF),
        "crisis": (OriginSubsystem.RISK, AttributionMechanism.CRISIS),
        "capital_budget": (
            OriginSubsystem.RISK,
            AttributionMechanism.CAPITAL_BUDGET,
        ),
        "risk_freeze_hold": (
            OriginSubsystem.RISK,
            AttributionMechanism.RISK_FREEZE,
        ),
    }
    selected = exact.get(reason_code)
    if selected is None and exit_kind in exact:
        selected = exact[exit_kind]
    unclassified_buy = selected is None and side == Side.BUY.value
    if unclassified_buy:
        # Preserve uncertainty honestly. This closed degraded category is
        # machine-valid but is never emitted by production Target call sites.
        selected = (
            OriginSubsystem.UNATTRIBUTED_LEGACY,
            AttributionMechanism.LEGACY_UNCLASSIFIED,
        )
    elif selected is None:
        selected = (
            OriginSubsystem.LEGACY_MIGRATION,
            AttributionMechanism.LEGACY_MIGRATION,
        )
    return selected[0].value, selected[1].value, unclassified_buy


def _legacy_industry(symbol: str, entry_date: str) -> tuple[str, str]:
    """Resolve the best deterministic PIT industry available during migration."""

    try:
        industry = default_ai_universe().industry_of(symbol, entry_date)
    except (TypeError, ValueError):
        industry = "unknown"
    if industry == "unknown":
        return _LEGACY_INDUSTRY, _LEGACY_MANIFEST_SHA256
    return industry, REQUIRED_AI_UNIVERSE_SHA256


def _populate_legacy_attribution(state: AccountState) -> list[dict[str, str]]:
    """Populate v1-v3 identity from stable structured fields only."""

    unknown_buy_reclassifications: dict[str, dict[str, str]] = {}

    replacements = {
        (str(event.get("signal_date", "")), str(event.get("new_symbol", ""))): str(
            event.get("old_symbol", "")
        )
        for event in state.replacement_events
        if event.get("signal_date") and event.get("new_symbol") and event.get("old_symbol")
    }

    def populate_order(order: PendingOrder | AccountOrder) -> None:
        origin, mechanism, unclassified_buy = _legacy_attribution_owner(
            order.reason_code,
            order.exit_kind,
            side=order.side,
        )
        industry, manifest = _legacy_industry(order.symbol, order.signal_date)
        replaces_symbol = replacements.get((order.signal_date, order.symbol))
        order.origin_subsystem = origin
        order.mechanism = mechanism
        order.origin_lifecycle = order.lifecycle
        order.replaces_symbol = replaces_symbol
        order.industry_at_entry = industry
        order.industry_manifest_sha256 = manifest
        order.event_id = derive_attribution_event_id(
            signal_date=order.signal_date,
            symbol=order.symbol,
            target_weight=order.target_weight,
            lifecycle=order.lifecycle,
            origin_lifecycle=order.origin_lifecycle,
            origin_subsystem=order.origin_subsystem,
            mechanism=order.mechanism,
            replaces_symbol=order.replaces_symbol,
            industry_at_entry=order.industry_at_entry,
            industry_manifest_sha256=order.industry_manifest_sha256,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )
        if unclassified_buy:
            unknown_buy_reclassifications.setdefault(
                order.event_id,
                {
                    "event_id": order.event_id,
                    "signal_date": order.signal_date,
                    "symbol": order.symbol,
                },
            )

    for ledger_order in state.order_ledger:
        populate_order(ledger_order)
    ledger = {order.order_id: order for order in state.order_ledger}
    for pending_order in state.pending_orders:
        linked = ledger.get(pending_order.order_id) if pending_order.order_id else None
        if linked is None:
            populate_order(pending_order)
            continue
        for field in (
            "event_id",
            "origin_subsystem",
            "mechanism",
            "origin_lifecycle",
            "replaces_symbol",
            "industry_at_entry",
            "industry_manifest_sha256",
        ):
            setattr(pending_order, field, getattr(linked, field))

    for symbol, position in state.positions.items():
        for tranche in position.tranches:
            industry, manifest = _legacy_industry(symbol, tranche.entry_date)
            tranche.origin_subsystem = OriginSubsystem.LEGACY_MIGRATION.value
            tranche.mechanism = AttributionMechanism.LEGACY_MIGRATION.value
            tranche.origin_lifecycle = tranche.lifecycle
            tranche.replaces_symbol = None
            tranche.industry_at_entry = industry
            tranche.industry_manifest_sha256 = manifest
            tranche.event_id = derive_attribution_event_id(
                signal_date=tranche.entry_date,
                symbol=symbol,
                target_weight=0.0,
                lifecycle=tranche.lifecycle,
                origin_lifecycle=tranche.origin_lifecycle,
                origin_subsystem=tranche.origin_subsystem,
                mechanism=tranche.mechanism,
                replaces_symbol=None,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
                reduction_policy=ReductionPolicy.FIFO.value,
                reason_code=f"legacy_tranche:{tranche.tranche_id}",
                exit_kind="legacy_migration",
            )

    identity_fields = (
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
    )
    for fill_index, fill in enumerate(state.fills, start=1):
        linked = ledger.get(fill.order_id) if fill.order_id else None
        if not fill.order_id:
            candidates = [
                order
                for order in state.order_ledger
                if _unlinked_fill_matches_order(fill, order, native=False)
            ]
            if len(candidates) > 1:
                raise RuntimeError(
                    "legacy unlinked fill has ambiguous structured order identity"
                )
            linked = candidates[0] if candidates else None
        if linked is not None:
            for field in identity_fields:
                setattr(fill, field, getattr(linked, field))
        else:
            origin, mechanism, unclassified_buy = _legacy_attribution_owner(
                fill.reason_code,
                fill.exit_kind,
                side=fill.side,
            )
            industry, manifest = _legacy_industry(fill.symbol, fill.signal_date)
            fill.origin_subsystem = origin
            fill.mechanism = mechanism
            fill.origin_lifecycle = fill.lifecycle
            fill.replaces_symbol = replacements.get((fill.signal_date, fill.symbol))
            fill.industry_at_entry = industry
            fill.industry_manifest_sha256 = manifest
            fill.event_id = derive_attribution_event_id(
                signal_date=fill.signal_date,
                symbol=fill.symbol,
                target_weight=0.0,
                lifecycle=fill.lifecycle,
                origin_lifecycle=fill.origin_lifecycle,
                origin_subsystem=fill.origin_subsystem,
                mechanism=fill.mechanism,
                replaces_symbol=fill.replaces_symbol,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
                reduction_policy=fill.reduction_policy,
                reason_code=f"{fill.reason_code}:legacy_fill:{fill.fill_id or fill_index}",
                exit_kind=fill.exit_kind,
            )
            if unclassified_buy:
                unknown_buy_reclassifications.setdefault(
                    fill.event_id,
                    {
                        "event_id": fill.event_id,
                        "signal_date": fill.signal_date,
                        "symbol": fill.symbol,
                    },
                )
        for allocation_index, allocation in enumerate(fill.sold_tranches, start=1):
            entry_date = str(allocation.get("entry_date") or fill.fill_date)
            lifecycle = str(allocation.get("lifecycle") or fill.lifecycle)
            industry, manifest = _legacy_industry(fill.symbol, entry_date)
            allocation.update(
                origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
                mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
                origin_lifecycle=lifecycle,
                replaces_symbol=None,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
            )
            allocation["event_id"] = derive_attribution_event_id(
                signal_date=entry_date,
                symbol=fill.symbol,
                target_weight=0.0,
                lifecycle=lifecycle,
                origin_lifecycle=lifecycle,
                origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
                mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
                replaces_symbol=None,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
                reduction_policy=ReductionPolicy.FIFO.value,
                reason_code=(
                    "legacy_sold_tranche:"
                    + str(allocation.get("tranche_id") or allocation_index)
                ),
                exit_kind="legacy_migration",
            )
    return [
        unknown_buy_reclassifications[event_id]
        for event_id in sorted(unknown_buy_reclassifications)
    ]


def _migrate_v4_attribution_event_ids(state: AccountState) -> dict[str, Any]:
    """Map validated schema-v4 events to the machine-only schema-v5 format."""

    event_id_map: dict[str, str] = {}
    reverse_event_id_map: dict[str, str] = {}
    object_assignments: list[tuple[Any, str]] = []
    allocation_assignments: list[tuple[dict[str, Any], str]] = []

    def record_mapping(old_event_id: str, new_event_id: str) -> None:
        existing = event_id_map.get(old_event_id)
        if existing is not None and existing != new_event_id:
            raise RuntimeError("v4 event_id maps to conflicting machine identities")
        reverse_existing = reverse_event_id_map.get(new_event_id)
        if reverse_existing is not None and reverse_existing != old_event_id:
            raise RuntimeError("v4 event_id migration has a reverse-map collision")
        event_id_map[old_event_id] = new_event_id
        reverse_event_id_map[new_event_id] = old_event_id

    def current_event_id(
        item: Any,
        *,
        signal_date: str,
        symbol: str,
        target_weight: float,
        lifecycle: str,
        reduction_policy: str,
        reason_code: str,
        exit_kind: str,
    ) -> str:
        return derive_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=target_weight,
            lifecycle=lifecycle,
            origin_lifecycle=item.origin_lifecycle,
            origin_subsystem=item.origin_subsystem,
            mechanism=item.mechanism,
            replaces_symbol=item.replaces_symbol,
            industry_at_entry=item.industry_at_entry,
            industry_manifest_sha256=item.industry_manifest_sha256,
            reduction_policy=reduction_policy,
            reason_code=reason_code,
            exit_kind=exit_kind,
        )

    durable_orders: list[AccountOrder | PendingOrder] = [
        *state.order_ledger,
        *state.pending_orders,
    ]
    for order in durable_orders:
        old_event_id = order.event_id
        new_event_id = current_event_id(
            order,
            signal_date=order.signal_date,
            symbol=order.symbol,
            target_weight=order.target_weight,
            lifecycle=order.lifecycle,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )
        record_mapping(old_event_id, new_event_id)
        object_assignments.append((order, new_event_id))

    for fill in state.fills:
        old_event_id = fill.event_id
        mapped_fill_event_id = event_id_map.get(old_event_id)
        if mapped_fill_event_id is None:
            raise RuntimeError("v4 fill event_id lacks a validated originating order")
        object_assignments.append((fill, mapped_fill_event_id))

    def migrate_detached_lot(
        lot: Any,
        *,
        signal_date: str,
        symbol: str,
        lifecycle: str,
        reason_code: str,
        exit_kind: str,
        label: str,
    ) -> str:
        old_event_id = lot.event_id
        expected_old = _derive_v4_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            origin_lifecycle=lot.origin_lifecycle,
            origin_subsystem=lot.origin_subsystem,
            mechanism=lot.mechanism,
            replaces_symbol=lot.replaces_symbol,
            industry_at_entry=lot.industry_at_entry,
            industry_manifest_sha256=lot.industry_manifest_sha256,
            reduction_policy=ReductionPolicy.FIFO.value,
            reason_code=reason_code,
            exit_kind=exit_kind,
        )
        if old_event_id != expected_old:
            raise RuntimeError(f"{label} v4 event_id differs from canonical derivation")
        new_event_id = current_event_id(
            lot,
            signal_date=signal_date,
            symbol=symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            reduction_policy=ReductionPolicy.FIFO.value,
            reason_code=reason_code,
            exit_kind=exit_kind,
        )
        record_mapping(old_event_id, new_event_id)
        return new_event_id

    for symbol, position in state.positions.items():
        for tranche in position.tranches:
            mapped_event_id = event_id_map.get(tranche.event_id)
            if mapped_event_id is not None:
                object_assignments.append((tranche, mapped_event_id))
                continue
            if (
                tranche.origin_subsystem != OriginSubsystem.LEGACY_MIGRATION.value
                or tranche.mechanism != AttributionMechanism.LEGACY_MIGRATION.value
            ):
                raise RuntimeError("v4 tranche event_id lacks a validated originating BUY")
            migrated_event_id = migrate_detached_lot(
                tranche,
                signal_date=tranche.entry_date,
                symbol=symbol,
                lifecycle=tranche.lifecycle,
                reason_code=f"legacy_tranche:{tranche.tranche_id}",
                exit_kind="legacy_migration",
                label="account tranche",
            )
            object_assignments.append((tranche, migrated_event_id))

    for fill in state.fills:
        for allocation_index, allocation in enumerate(fill.sold_tranches, start=1):
            old_event_id = str(allocation["event_id"])
            mapped_event_id = event_id_map.get(old_event_id)
            if mapped_event_id is not None:
                allocation_assignments.append((allocation, mapped_event_id))
                continue
            lot = SimpleNamespace(**allocation)
            if (
                lot.origin_subsystem == OriginSubsystem.LEGACY_MIGRATION.value
                and lot.mechanism == AttributionMechanism.LEGACY_MIGRATION.value
            ):
                reason_code = "legacy_sold_tranche:" + str(
                    allocation.get("tranche_id") or allocation_index
                )
                exit_kind = "legacy_migration"
            elif (
                lot.origin_subsystem == OriginSubsystem.BROKER_RECONCILIATION.value
                and lot.mechanism == AttributionMechanism.BROKER_RECONCILIATION.value
                and allocation.get("degraded") is True
            ):
                reason_code = f"broker_reconciliation:degraded-sale:{fill.fill_id}"
                exit_kind = "broker_reconciliation"
            else:
                raise RuntimeError("v4 sold lot event_id lacks a validated originating BUY")
            migrated_event_id = migrate_detached_lot(
                lot,
                signal_date=str(allocation["entry_date"]),
                symbol=fill.symbol,
                lifecycle=str(allocation["lifecycle"]),
                reason_code=reason_code,
                exit_kind=exit_kind,
                label="fill sold lot",
            )
            allocation_assignments.append((allocation, migrated_event_id))

    # Apply only after every old identity, chain, and bidirectional mapping has
    # been validated. A collision therefore cannot leave even the in-memory
    # migration candidate partially resealed.
    for item, event_id in object_assignments:
        item.event_id = event_id
    for allocation, event_id in allocation_assignments:
        allocation["event_id"] = event_id

    return {
        "policy": "validated_v4_to_v5_machine_identity",
        "event_id_map": [
            {
                "from_event_id": old_event_id,
                "to_event_id": event_id_map[old_event_id],
            }
            for old_event_id in sorted(event_id_map)
        ],
    }


def migrate_account(
    source: str | Path,
    destination: str | Path,
    *,
    new_code_hash: str,
    acknowledge_code_change: bool,
) -> AccountState:
    """Normalize one durable account and bind it to reviewed production code.

    The caller explicitly acknowledges the target code fingerprint. Market-data
    provenance, broker state, orders, fills, and strategy state remain intact.
    """
    if not acknowledge_code_change:
        raise RuntimeError("account migration requires --acknowledge-code-change")
    if not new_code_hash:
        raise RuntimeError("account migration requires a non-empty code hash")
    state = load_account(source, allow_legacy_schema=True)
    previous_schema = state.schema_version
    previous_code_hash = state.code_hash
    degraded_sell_attributions: list[dict[str, Any]] = []
    if previous_schema == 2:
        for index, fill in enumerate(state.fills, start=1):
            if fill.side != Side.SELL.value or not fill.order_id or fill.sold_tranches:
                continue
            attribution_id = fill.fill_id or (f"{fill.order_id}:{fill.fill_date}:{index}")
            fill.sold_tranches = [
                {
                    "tranche_id": f"legacy-v2-unattributed:{attribution_id}",
                    "lifecycle": fill.lifecycle,
                    "shares": fill.shares,
                    "attribution_quality": "degraded_schema_v2_missing_sold_tranches",
                    "source_schema": 2,
                }
            ]
            degraded_sell_attributions.append(
                {
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "symbol": fill.symbol,
                    "fill_date": fill.fill_date,
                    "shares": fill.shares,
                }
            )
    attribution_event_id_migration: dict[str, Any] | None = None
    legacy_unknown_buy_classifications: list[dict[str, str]] = []
    if previous_schema < _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
        legacy_unknown_buy_classifications = _populate_legacy_attribution(state)
    elif previous_schema == _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
        attribution_event_id_migration = _migrate_v4_attribution_event_ids(state)
    state.schema_version = ACCOUNT_SCHEMA_VERSION
    state.code_hash = new_code_hash
    migration_event: dict[str, Any] = {
        "migrated_at_utc": datetime.now(UTC).isoformat(),
        "from_schema": previous_schema,
        "to_schema": ACCOUNT_SCHEMA_VERSION,
        "from_code_hash": previous_code_hash,
        "to_code_hash": new_code_hash,
    }
    if degraded_sell_attributions:
        migration_event["degraded_sell_attribution"] = {
            "policy": "synthetic_single_lot_exact_share_backfill",
            "fills": degraded_sell_attributions,
        }
    if attribution_event_id_migration is not None:
        migration_event["attribution_event_id_migration"] = (
            attribution_event_id_migration
        )
    if legacy_unknown_buy_classifications:
        migration_event["legacy_unknown_buy_classification"] = {
            "policy": "pre_v4_unknown_buy_to_unattributed_legacy",
            "events": legacy_unknown_buy_classifications,
        }
    state.account_migrations.append(migration_event)
    save_account(state, destination)
    return state


def _fsync_directory(path: Path) -> None:
    """Flush directory metadata where the platform exposes directory descriptors."""

    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_account(state: AccountState, path: str | Path) -> None:
    """Atomically persist an account state after flushing it to stable storage."""
    if state.schema_version != ACCOUNT_SCHEMA_VERSION:
        raise RuntimeError(
            f"account schema {state.schema_version} requires explicit migration before save"
        )
    _validate_position_state(state, validate_attribution=True)
    _validate_order_state(
        state,
        sequence_was_explicit=True,
        validate_attribution=True,
    )
    _validate_strategy_risk_state(state)
    _validate_lot_origin_chains(state)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=destination.name, dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                state.to_dict(),
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
