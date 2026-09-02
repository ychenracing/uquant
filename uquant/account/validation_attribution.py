"""Canonical attribution identity and lot-chain validation."""

from __future__ import annotations

import re
from datetime import date as date_type
from types import SimpleNamespace
from typing import Any

from ..contracts.universe import (
    CANONICAL_INDUSTRIES,
    REQUIRED_AI_UNIVERSE_SHA256,
    default_ai_universe,
)
from ..types import (
    ATTRIBUTION_IDENTITY_FIELDS,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Fill,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    ReductionPolicy,
    Side,
    derive_attribution_event_id,
    validate_attribution_compatibility,
)
from .validation_common import (
    EVENT_ID_PATTERN as _EVENT_ID,
)
from .validation_common import (
    LEGACY_INDUSTRY as _LEGACY_INDUSTRY,
)
from .validation_common import (
    LEGACY_MANIFEST_SHA256 as _LEGACY_MANIFEST_SHA256,
)
from .validation_common import finite_number as _finite_number
from .validation_common import required_iso_date as _required_iso_date
from .validation_common import required_text as _required_text
from .validation_common import (
    unlinked_fill_matches_order as _unlinked_fill_matches_order,
)

_GRANT_ID = re.compile(r"^grant_[0-9a-f]{64}$")
_EPOCH_ID = re.compile(r"^epoch_[0-9a-f]{64}$")


def _validate_attribution_industry_and_event_id(
    *,
    item: Any,
    label: Any,
    mechanism: Any,
    origin: Any,
    verify_event_derivation: Any,
) -> None:
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
            expected = derive_attribution_event_id(
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
            raise RuntimeError(f"{label} event_id differs from canonical derivation")


def validate_attribution_identity(
    item: Any,
    *,
    label: str,
    verify_event_derivation: bool = False,
) -> None:
    """Validate one canonical attribution identity."""

    grant_id = getattr(item, "grant_id", "")
    if not isinstance(grant_id, str) or (grant_id and _GRANT_ID.fullmatch(grant_id) is None):
        raise RuntimeError(f"{label} has invalid strategic grant_id")
    epoch_id = getattr(item, "epoch_id", "")
    if not isinstance(epoch_id, str) or (epoch_id and _EPOCH_ID.fullmatch(epoch_id) is None):
        raise RuntimeError(f"{label} has invalid strategic epoch_id")

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
    _validate_attribution_industry_and_event_id(
        item=item,
        label=label,
        mechanism=mechanism,
        origin=origin,
        verify_event_derivation=verify_event_derivation,
    )


def validate_lot_origin_chains(state: AccountState) -> None:
    """Bind every native live/sold lot to a validated originating BUY."""

    legacy_migration_boundary = any(
        isinstance(event.get("from_schema"), int)
        and not isinstance(event.get("from_schema"), bool)
        and int(event["from_schema"]) < state.schema_version
        and event.get("to_schema") == state.schema_version
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
            if order.side == Side.BUY.value and _unlinked_fill_matches_order(fill, order)
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
                getattr(fill, field, "") == getattr(lot, field, "")
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
            raise RuntimeError(f"native lot shares exceed originating BUY fill shares for {key[0]} {key[1]}")


def validate_order_intent(
    order: PendingOrder | AccountOrder,
    *,
    label: str,
    validate_attribution: bool = False,
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
        validate_attribution_identity(
            order,
            label=label,
            verify_event_derivation=True,
        )
        if order.side == Side.BUY.value:
            expected_industry = default_ai_universe().industry_of(order.symbol, signal_date)
            if expected_industry == "unknown":
                raise RuntimeError(f"{label} BUY has no point-in-time AI-universe membership")
            if order.industry_at_entry != expected_industry:
                raise RuntimeError(f"{label} BUY industry_at_entry differs from point-in-time membership")
    return signal_date
