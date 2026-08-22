"""Order, fill, and lot-origin validation for durable accounts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date as date_type
from types import SimpleNamespace
from typing import Any

from ..contracts.universe import (
    CANONICAL_INDUSTRIES,
    REQUIRED_AI_UNIVERSE_SHA256,
    default_ai_universe,
)
from ..types import (
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
    ReductionPolicy,
    Side,
    derive_attribution_event_id,
    order_intent_metadata,
    validate_attribution_compatibility,
)
from .validation_common import (
    _EVENT_ID,
    _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
    _LEGACY_INDUSTRY,
    _LEGACY_MANIFEST_SHA256,
    _ORDER_ID,
    _finite_number,
    _nonnegative_integer,
    _required_iso_date,
    _required_text,
    _unlinked_fill_matches_order,
)


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
        if getattr(item, "side", None) == Side.BUY.value and origin is OriginSubsystem.LEGACY_MIGRATION:
            raise RuntimeError(f"{label} legacy migration identity cannot create a BUY") from exc
        elif (
            getattr(item, "side", None) == Side.BUY.value and origin is OriginSubsystem.BROKER_RECONCILIATION
        ):
            raise RuntimeError(f"{label} broker reconciliation identity cannot create a BUY") from exc
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
        origin is OriginSubsystem.LEGACY_MIGRATION and mechanism is AttributionMechanism.LEGACY_MIGRATION
    )
    broker_degraded_identity = bool(
        origin is OriginSubsystem.BROKER_RECONCILIATION
        and mechanism is AttributionMechanism.BROKER_RECONCILIATION
    )
    migrated_inventory_sale = bool(getattr(item, "side", None) == Side.SELL.value)
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
                raise RuntimeError(f"{label} v4 event_id differs from canonical derivation")
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
    if fill.order_id:
        _order_sequence(fill.order_id)
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


def _order_sequence(order_id: str) -> int:
    if not isinstance(order_id, str) or _ORDER_ID.fullmatch(order_id) is None:
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
            raise RuntimeError("account state next order sequence does not exactly follow the durable ledger")
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
    for order_id in pending_ids:
        _order_sequence(order_id)
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
            unlinked_identity_matches(fill, candidate) for candidate in state.order_ledger
        )
        if validate_attribution and structured_matches != 1:
            raise RuntimeError("native unlinked fill must match exactly one structured account order")
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
        unlinked_matches = [fill for fill in unlinked_fills if unlinked_identity_matches(fill, ledger_item)]
        unlinked_match_shares = sum(fill.shares for fill in unlinked_matches)
        unlinked_exempt = (
            not order_fills
            and bool(unlinked_matches)
            and unlinked_match_shares == ledger_item.filled_shares
            and all(
                sum(unlinked_identity_matches(fill, candidate) for candidate in state.order_ledger) == 1
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
            if order.side == Side.BUY.value and _unlinked_fill_matches_order(fill, order, native=True)
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
            and all(getattr(fill, field) == getattr(lot, field) for field in ATTRIBUTION_IDENTITY_FIELDS)
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
            raise RuntimeError(f"native lot shares exceed originating BUY fill shares for {key[0]} {key[1]}")
