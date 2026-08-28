"""Authoritative broker snapshot and manual-fill reconciliation."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, fields, replace
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from .account import validate_lot_origin_chains as _validate_lot_origin_chains
from .account import validate_order_state as _validate_order_state
from .account import validate_position_state as _validate_position_state
from .account import validate_strategy_risk_state as _validate_strategy_risk_state
from .broker_contract import BrokerFillValues as _BrokerFillValues
from .broker_contract import broker_date as _broker_date
from .broker_contract import broker_integer as _broker_integer
from .broker_contract import broker_nonnegative as _nonnegative
from .broker_contract import ordered_broker_fills as _ordered_broker_fills
from .config import DEFAULT_CONFIG, SystemConfig
from .contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe
from .data import normalize_symbol
from .execution import allocate_sell_costs as _allocate_sell_costs
from .execution import risk_priority_tranche_key
from .models.strategic_epoch import (
    bind_account_strategic_ownership,
    record_account_strategic_epoch_fill,
)
from .models.strategic_grant import record_strategic_grant_fill
from .models.trading import late_strategic_fill_allowed as _late_strategic_fill_allowed
from .types import (
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    AttributionIdentity,
    AttributionMechanism,
    Fill,
    Lifecycle,
    OrderStatus,
    OriginSubsystem,
    Position,
    ReductionPolicy,
    Side,
    Tranche,
    derive_attribution_event_id,
    order_intent_metadata,
)


def _broker_reconciliation_identity(
    *,
    symbol: str,
    signal_date: str,
    lifecycle: str,
    token: str,
) -> AttributionIdentity:
    """Create explicit identity for inventory not backed by a planned order."""

    industry = default_ai_universe().industry_of(symbol, signal_date)
    if industry == "unknown":
        industry = "legacy_unmapped"
        manifest = "0" * 64
    else:
        manifest = REQUIRED_AI_UNIVERSE_SHA256
    origin = OriginSubsystem.BROKER_RECONCILIATION.value
    mechanism = AttributionMechanism.BROKER_RECONCILIATION.value
    reason_code = f"broker_reconciliation:{token}"
    return {
        "event_id": derive_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            origin_lifecycle=lifecycle,
            origin_subsystem=origin,
            mechanism=mechanism,
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
            reduction_policy=ReductionPolicy.FIFO.value,
            reason_code=reason_code,
            exit_kind="broker_reconciliation",
        ),
        "origin_subsystem": origin,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": manifest,
        "grant_id": "",
        "epoch_id": "",
    }


def _allocate_broker_sale(
    tranches: list[Tranche],
    *,
    shares: int,
    fill_date: str,
    policy: str,
) -> list[dict[str, Any]]:
    """Mutate a reconciliation inventory and return authoritative lot exits."""
    if policy == ReductionPolicy.RISK_PRIORITY.value:
        ordered = sorted(tranches, key=risk_priority_tranche_key)
    else:
        ordered = sorted(
            tranches,
            key=lambda item: (item.sellable_date, item.entry_date, item.tranche_id),
        )
    remaining = shares
    allocations: list[dict[str, Any]] = []
    for tranche in ordered:
        if remaining <= 0 or tranche.sellable_date > fill_date:
            continue
        sold = min(tranche.shares, remaining)
        if sold <= 0:
            continue
        allocations.append(
            {
                "tranche_id": tranche.tranche_id,
                "lifecycle": tranche.lifecycle,
                "shares": sold,
                "cost": tranche.avg_cost,
                "unit_cost": tranche.avg_cost,
                "avg_cost": tranche.avg_cost,
                "cost_basis": sold * tranche.avg_cost,
                "entry_date": tranche.entry_date,
                "mfe": tranche.mfe,
                "mae": tranche.mae,
                "event_id": tranche.event_id,
                "origin_subsystem": tranche.origin_subsystem,
                "mechanism": tranche.mechanism,
                "origin_lifecycle": tranche.origin_lifecycle,
                "replaces_symbol": tranche.replaces_symbol,
                "industry_at_entry": tranche.industry_at_entry,
                "industry_manifest_sha256": tranche.industry_manifest_sha256,
                "grant_id": tranche.grant_id,
                "epoch_id": tranche.epoch_id,
            }
        )
        tranche.shares -= sold
        remaining -= sold
    tranches[:] = [item for item in tranches if item.shares > 0]
    return allocations


def _align_sellability(
    tranches: list[Tranche],
    *,
    sellable: int,
    as_of: str,
    next_date: str,
) -> list[Tranche]:
    """Preserve economic lots while matching broker aggregate availability."""
    remaining_sellable = sellable
    aligned: list[Tranche] = []
    for tranche in sorted(tranches, key=lambda item: (item.entry_date, item.tranche_id)):
        available = min(tranche.shares, remaining_sellable)
        unavailable = tranche.shares - available
        if available:
            aligned.append(
                replace(
                    tranche,
                    tranche_id=(tranche.tranche_id if unavailable == 0 else f"{tranche.tranche_id}:sellable"),
                    shares=available,
                    sellable_date=as_of,
                )
            )
            remaining_sellable -= available
        if unavailable:
            aligned.append(
                replace(
                    tranche,
                    tranche_id=(tranche.tranche_id if available == 0 else f"{tranche.tranche_id}:t1"),
                    shares=unavailable,
                    sellable_date=next_date,
                )
            )
    return aligned


@dataclass(slots=True)
class _BrokerSyncState:
    account: AccountState
    as_of: str
    snapshot_date: date_type
    cash: float
    raw_positions: list[Any]
    raw_orders: list[Any]
    ledger: dict[str, AccountOrder]
    ordered_fills: list[dict[str, Any]]
    known_fills: dict[str, Fill]
    economic_tranches: dict[str, list[Tranche]]
    imported_buy_lifecycle: dict[str, str]
    imported: int
    completed_order_ids: set[str]


def _validate_late_strategic_fill_capacity(
    state: _BrokerSyncState,
    *,
    order: AccountOrder,
    shares: int,
) -> None:
    if not _late_strategic_fill_allowed(order):
        return
    matching_orders = [
        candidate
        for candidate in state.ledger.values()
        if candidate.grant_id == order.grant_id
        and candidate.event_id == order.event_id
        and candidate.symbol == order.symbol
        and candidate.side == order.side
    ]
    intended_shares = max(
        (candidate.requested_shares for candidate in matching_orders),
        default=0,
    )
    confirmed_shares = sum(
        fill.shares
        for fill in state.account.fills
        if fill.grant_id == order.grant_id
        and fill.event_id == order.event_id
        and fill.symbol == order.symbol
        and fill.side == order.side
    )
    remaining_shares = max(0, intended_shares - confirmed_shares)
    if remaining_shares == 0:
        raise ValueError("strategic economic order is already satisfied")
    if shares > remaining_shares:
        raise ValueError("broker late fill exceeds remaining strategic economic order")


def _prepare_broker_sync(account: AccountState, payload: dict[str, Any]) -> _BrokerSyncState:
    as_of_value = payload.get("as_of", "")
    snapshot_date = _broker_date(as_of_value, field="snapshot as_of")
    as_of = str(as_of_value)
    if account.broker_as_of and as_of < account.broker_as_of:
        raise ValueError("broker snapshot predates the latest broker snapshot")
    if account.last_successful_run and as_of < account.last_successful_run:
        raise ValueError("broker snapshot predates the last successful decision")

    cash = _nonnegative(payload, "cash")
    raw_positions = payload.get("positions")
    raw_fills = payload.get("fills", [])
    raw_orders = payload.get("orders", [])
    if (
        not isinstance(raw_positions, list)
        or not isinstance(raw_fills, list)
        or not isinstance(raw_orders, list)
    ):
        raise ValueError("broker positions, fills, and orders must be arrays")

    working = copy.deepcopy(account)
    _validate_order_state(
        working,
        sequence_was_explicit=False,
        validate_attribution=True,
    )
    _validate_strategy_risk_state(working)
    ledger = {order.order_id: order for order in working.order_ledger}
    for pending in working.pending_orders:
        if not pending.order_id:
            continue
        order = ledger.get(pending.order_id)
        if order is None:
            raise ValueError("pending order references an unknown account order")
        pending_metadata = order_intent_metadata(pending)
        ledger_metadata = order_intent_metadata(order)
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
            raise ValueError(
                "pending order immutable metadata differs from account order: " + ", ".join(changed)
            )
    known_fills = {fill.fill_id: fill for fill in working.fills if fill.fill_id}
    ordered_fills = _ordered_broker_fills(
        raw_fills,
        as_of=as_of,
        account=working,
        known_fills=known_fills,
    )
    economic_tranches = {
        symbol: copy.deepcopy(position.tranches) for symbol, position in working.positions.items()
    }
    return _BrokerSyncState(
        account=working,
        as_of=as_of,
        snapshot_date=snapshot_date,
        cash=cash,
        raw_positions=raw_positions,
        raw_orders=raw_orders,
        ledger=ledger,
        ordered_fills=ordered_fills,
        known_fills=known_fills,
        economic_tranches=economic_tranches,
        imported_buy_lifecycle={},
        imported=0,
        completed_order_ids=set(),
    )


def _broker_fill_identity_matches(
    existing: Fill,
    *,
    order_id: str,
    fill_date: str,
    symbol: str,
    side: str,
    shares: int,
    price: float,
    gross: float,
    commission: float,
    stamp_duty: float,
    transfer_fee: float,
    slippage_cost: float,
) -> bool:
    return (
        existing.order_id == order_id
        and existing.fill_date == fill_date
        and existing.symbol == symbol
        and existing.side == side
        and existing.shares == shares
        and math.isclose(existing.price, price, rel_tol=1e-12, abs_tol=1e-12)
        and math.isclose(existing.gross_value, gross, rel_tol=1e-12, abs_tol=0.01)
        and math.isclose(existing.commission, commission, abs_tol=1e-12)
        and math.isclose(existing.stamp_duty, stamp_duty, abs_tol=1e-12)
        and math.isclose(existing.transfer_fee, transfer_fee, abs_tol=1e-12)
        and math.isclose(existing.slippage_cost, slippage_cost, abs_tol=1e-12)
    )


def _validated_fill_order_progress(
    *,
    order: AccountOrder,
    existing_fill: Fill | None,
    shares: int,
    remaining: int,
    final: bool,
) -> tuple[int, int]:
    if existing_fill is None and order.status in {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    } and not _late_strategic_fill_allowed(order):
        raise ValueError("broker cannot append a fill to a terminal account order")
    cumulative_filled = order.filled_shares + shares
    reported_request = cumulative_filled + remaining
    if existing_fill is None and order.requested_shares > 0 and reported_request != order.requested_shares:
        raise ValueError("broker fill exceeds or contradicts requested order shares")
    if (
        existing_fill is None
        and final
        and order.requested_shares > 0
        and cumulative_filled != order.requested_shares
    ):
        raise ValueError("final broker fill does not complete requested order shares")
    return cumulative_filled, reported_request


def _validated_broker_fill(state: _BrokerSyncState, raw: dict[str, Any]) -> _BrokerFillValues:
    fill_id = str(raw.get("fill_id", "")).strip()
    if not fill_id:
        raise ValueError("each broker fill requires a stable fill_id")
    order_id = str(raw.get("order_id", "")).strip()
    order = state.ledger.get(order_id)
    if order is None:
        raise ValueError(f"broker fill references unknown order {order_id!r}")
    symbol = normalize_symbol(str(raw.get("symbol", "")))
    side = str(raw.get("side", "")).upper()
    if symbol != order.symbol or side != order.side:
        raise ValueError("broker fill symbol/side differs from account order")
    if side not in {Side.BUY.value, Side.SELL.value}:
        raise ValueError("broker fill has invalid side")
    shares = _broker_integer(raw, "shares", default=0, positive=True)
    raw_price = raw.get("price", 0.0)
    if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
        raise ValueError("broker fill price must be a finite number")
    price = float(raw_price)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("broker fill shares and price must be positive")
    fill_date_value = raw.get("fill_date", state.as_of)
    parsed_fill_date = _broker_date(fill_date_value, field="fill_date")
    fill_date = str(fill_date_value)
    signal_date = _broker_date(order.signal_date, field="order signal_date")
    submitted_date = _broker_date(order.submitted_date, field="order submitted_date")
    if parsed_fill_date <= max(signal_date, submitted_date):
        raise ValueError("broker fill date must be after order signal/submission")
    if parsed_fill_date > state.snapshot_date:
        raise ValueError("broker fill date is after snapshot as_of")
    commission = _nonnegative(raw, "commission")
    stamp_duty = _nonnegative(raw, "stamp_duty")
    transfer_fee = _nonnegative(raw, "transfer_fee")
    slippage_cost = _nonnegative(raw, "slippage_cost")
    gross = _nonnegative(raw, "gross_value", shares * price)
    expected_gross = shares * price
    if not math.isclose(gross, expected_gross, rel_tol=1e-12, abs_tol=0.01):
        raise ValueError("broker fill gross_value does not reconcile to shares * price")
    final = raw["final"]
    remaining = _broker_integer(raw, "remaining_shares")
    if (final and remaining != 0) or (not final and remaining == 0):
        raise ValueError("broker fill final and remaining_shares are inconsistent")
    existing_fill = state.known_fills.get(fill_id)
    if existing_fill is not None:
        identity_matches = _broker_fill_identity_matches(
            existing_fill,
            order_id=order_id,
            fill_date=fill_date,
            symbol=symbol,
            side=side,
            shares=shares,
            price=price,
            gross=gross,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            slippage_cost=slippage_cost,
        )
        if not identity_matches:
            raise ValueError("broker fill_id was reused with different economics")
    if existing_fill is None:
        _validate_late_strategic_fill_capacity(state, order=order, shares=shares)
    cumulative_filled, reported_request = _validated_fill_order_progress(
        order=order,
        existing_fill=existing_fill,
        shares=shares,
        remaining=remaining,
        final=final,
    )
    return _BrokerFillValues(
        fill_id=fill_id,
        order_id=order_id,
        order=order,
        symbol=symbol,
        side=side,
        shares=shares,
        price=price,
        fill_date=fill_date,
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        slippage_cost=slippage_cost,
        gross=gross,
        final=final,
        remaining=remaining,
        cumulative_filled=cumulative_filled,
        reported_request=reported_request,
        existing_fill=existing_fill,
    )


def _broker_fill_allocations(
    state: _BrokerSyncState,
    values: _BrokerFillValues,
) -> list[dict[str, Any]]:
    if values.side == Side.SELL.value:
        sold_tranches = _allocate_broker_sale(
            state.economic_tranches.setdefault(values.symbol, []),
            shares=values.shares,
            fill_date=values.fill_date,
            policy=values.order.reduction_policy,
        )
        attributed_shares = sum(int(item["shares"]) for item in sold_tranches)
        if attributed_shares != values.shares:
            missing_shares = values.shares - attributed_shares
            existing = state.account.positions.get(values.symbol)
            fallback_cost = (
                existing.avg_cost
                if existing is not None and math.isfinite(existing.avg_cost) and existing.avg_cost > 0
                else values.price
            )
            fallback_entry_date = (
                existing.entry_date if existing is not None and existing.entry_date else values.fill_date
            )
            fallback_lifecycle = existing.lifecycle if existing is not None else values.order.lifecycle
            sold_tranches.append(
                {
                    "tranche_id": f"broker-degraded-sale:{values.fill_id}",
                    "lifecycle": fallback_lifecycle,
                    "shares": missing_shares,
                    "cost": fallback_cost,
                    "unit_cost": fallback_cost,
                    "avg_cost": fallback_cost,
                    "cost_basis": missing_shares * fallback_cost,
                    "entry_date": fallback_entry_date,
                    "mfe": 0.0,
                    "mae": 0.0,
                    "degraded": True,
                    "degradation_reason": "broker sale exceeded known eligible lot inventory",
                    **_broker_reconciliation_identity(
                        symbol=values.symbol,
                        signal_date=fallback_entry_date,
                        lifecycle=fallback_lifecycle,
                        token=f"degraded-sale:{values.fill_id}",
                    ),
                }
            )
            state.account.reconciliation_events.append(
                {
                    "date": state.as_of,
                    "symbol": values.symbol,
                    "event": "sell_lot_attribution_incomplete",
                    "broker_shares": values.shares,
                    "attributed_shares": attributed_shares,
                    "degraded_shares": missing_shares,
                }
            )
        _allocate_sell_costs(
            sold_tranches,
            commission=values.commission,
            stamp_duty=values.stamp_duty,
            transfer_fee=values.transfer_fee,
            slippage_cost=values.slippage_cost,
        )
        return sold_tranches
    full_unit_cost = (values.gross + values.commission + values.transfer_fee) / values.shares
    state.economic_tranches.setdefault(values.symbol, []).append(
        Tranche(
            tranche_id=f"broker-fill:{values.fill_id}",
            lifecycle=values.order.lifecycle,
            shares=values.shares,
            avg_cost=full_unit_cost,
            entry_date=values.fill_date,
            sellable_date=str(date_type.fromisoformat(values.fill_date) + timedelta(days=1)),
            highest_close=values.price,
            lowest_close=values.price,
            entry_score=values.order.entry_score,
            entry_confidence=values.order.entry_confidence,
            entry_regime=values.order.entry_regime,
            entry_industry_strength=values.order.entry_industry_strength,
            event_id=values.order.event_id,
            origin_subsystem=values.order.origin_subsystem,
            mechanism=values.order.mechanism,
            origin_lifecycle=values.order.origin_lifecycle,
            replaces_symbol=values.order.replaces_symbol,
            industry_at_entry=values.order.industry_at_entry,
            industry_manifest_sha256=values.order.industry_manifest_sha256,
            grant_id=values.order.grant_id,
            epoch_id=values.order.epoch_id,
        )
    )
    state.imported_buy_lifecycle[values.symbol] = values.order.lifecycle
    return []


def _commit_imported_broker_fill(
    state: _BrokerSyncState,
    values: _BrokerFillValues,
    sold_tranches: list[dict[str, Any]],
) -> None:
    order = values.order
    state.account.fills.append(
        Fill(
            signal_date=order.signal_date,
            fill_date=values.fill_date,
            symbol=values.symbol,
            side=values.side,
            shares=values.shares,
            price=values.price,
            gross_value=values.gross,
            commission=values.commission,
            stamp_duty=values.stamp_duty,
            transfer_fee=values.transfer_fee,
            slippage_cost=values.slippage_cost,
            reason=order.reason,
            lifecycle=order.lifecycle,
            order_id=values.order_id,
            fill_id=values.fill_id,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
            sold_tranches=sold_tranches,
            event_id=order.event_id,
            origin_subsystem=order.origin_subsystem,
            mechanism=order.mechanism,
            origin_lifecycle=order.origin_lifecycle,
            replaces_symbol=order.replaces_symbol,
            industry_at_entry=order.industry_at_entry,
            industry_manifest_sha256=order.industry_manifest_sha256,
            grant_id=order.grant_id,
            epoch_id=order.epoch_id,
        )
    )
    state.known_fills[values.fill_id] = state.account.fills[-1]
    state.imported += 1
    order.filled_shares = values.cumulative_filled
    if order.requested_shares == 0:
        order.requested_shares = values.reported_request
    order.remaining_shares = values.remaining
    order.status = (
        OrderStatus.FILLED.value
        if values.final and values.remaining == 0
        else OrderStatus.PARTIALLY_FILLED.value
    )
    order.last_update_date = values.fill_date
    order.last_event = "BROKER_FILL"
    if order.status == OrderStatus.FILLED.value:
        state.completed_order_ids.add(values.order_id)
    if values.side == Side.BUY.value and order.grant_id:
        if (
            state.account.strategic_grant is not None
            and state.account.strategic_grant.candidate_symbol == values.symbol
        ):
            record_strategic_grant_fill(
                state.account.strategic_grant,
                grant_id=order.grant_id,
                shares=values.shares,
                completed=values.final and values.remaining == 0,
            )
        record_account_strategic_epoch_fill(
            state.account,
            epoch_id=order.epoch_id,
            grant_id=order.grant_id,
            symbol=values.symbol,
            fill_session=values.fill_date,
            filled_shares=values.shares,
        )
        if values.final and values.remaining == 0:
            for pending in state.account.pending_orders:
                if pending.grant_id != order.grant_id or pending.order_id == values.order_id:
                    continue
                replacement = state.ledger.get(pending.order_id)
                if replacement is not None and replacement.status not in {
                    OrderStatus.FILLED.value,
                    OrderStatus.CANCELLED.value,
                    OrderStatus.REPLACED.value,
                }:
                    replacement.status = OrderStatus.CANCELLED.value
                    replacement.cancel_reason = "late fill satisfied strategic grant"
                    replacement.last_update_date = values.fill_date
                    replacement.last_event = "LATE_FILL_SUPPRESSED_RETRY"
                state.completed_order_ids.add(pending.order_id)


def _import_broker_fills(state: _BrokerSyncState) -> None:
    for raw in state.ordered_fills:
        values = _validated_broker_fill(state, raw)
        if values.existing_fill is not None:
            continue
        sold_tranches = _broker_fill_allocations(state, values)
        _commit_imported_broker_fill(state, values, sold_tranches)


def _apply_broker_order_updates(state: _BrokerSyncState) -> None:
    seen_broker_orders: set[str] = set()
    for raw in state.raw_orders:
        if not isinstance(raw, dict):
            raise ValueError("each broker order must be an object")
        order_id = str(raw.get("order_id", "")).strip()
        if not order_id or order_id in seen_broker_orders:
            raise ValueError("broker orders require unique stable order_id values")
        seen_broker_orders.add(order_id)
        order = state.ledger.get(order_id)
        if order is None:
            raise ValueError(f"broker order references unknown order {order_id!r}")
        status = str(raw.get("status", "")).upper()
        if status != OrderStatus.CANCELLED.value:
            raise ValueError("broker order updates only confirm cancellation")
        remaining = _broker_integer(raw, "remaining_shares")
        if remaining != 0:
            raise ValueError("broker cancellation must report zero live remaining shares")
        if order.status in {OrderStatus.FILLED.value, OrderStatus.REPLACED.value}:
            raise ValueError("broker cannot cancel a filled or replaced order")
        order.status = OrderStatus.CANCELLED.value
        order.last_update_date = state.as_of
        order.last_event = "BROKER_CANCELLED"
        state.completed_order_ids.add(order_id)


def _reconciled_broker_position(
    state: _BrokerSyncState,
    raw: dict[str, Any],
    *,
    reconciled_positions: dict[str, Position],
) -> tuple[str, Position]:
    symbol, shares, sellable, avg_cost = _validated_broker_position_fields(
        raw,
        reconciled_positions=reconciled_positions,
    )
    existing = state.account.positions.get(symbol)
    tranches = _reconciled_broker_tranches(
        state,
        symbol=symbol,
        shares=shares,
        sellable=sellable,
    )
    lifecycle = state.imported_buy_lifecycle.get(
        symbol,
        existing.lifecycle if existing is not None else Lifecycle.CORE.value,
    )
    strategy_highest = (
        existing.highest_close
        if existing is not None
        else max((item.highest_close for item in tranches), default=avg_cost)
    )
    derived_entry_date = min(
        (item.entry_date for item in tranches if item.entry_date),
        default=state.as_of,
    )
    derived_highest = max([strategy_highest, *(item.highest_close for item in tranches)])
    return symbol, Position(
        symbol=symbol,
        shares=shares,
        avg_cost=avg_cost,
        entry_date=derived_entry_date,
        highest_close=derived_highest,
        lifecycle=lifecycle,
        tranches=tranches,
        grant_id=next(iter({item.grant_id for item in tranches if item.grant_id}), ""),
        epoch_id=next(iter({item.epoch_id for item in tranches if item.epoch_id}), ""),
    )


def _validated_broker_position_fields(
    raw: dict[str, Any],
    *,
    reconciled_positions: dict[str, Position],
) -> tuple[str, int, int, float]:
    symbol = normalize_symbol(str(raw.get("symbol", "")))
    if symbol in reconciled_positions:
        raise ValueError(f"duplicate broker position {symbol}")
    shares = _broker_integer(raw, "shares", default=0, positive=True)
    sellable = _broker_integer(raw, "sellable_shares", default=-1)
    avg_cost = _nonnegative(raw, "avg_cost")
    if avg_cost <= 0 or sellable > shares:
        raise ValueError("broker position requires positive shares/cost and bounded sellable_shares")
    if "lifecycle" in raw and str(raw["lifecycle"]) not in {item.value for item in Lifecycle}:
        raise ValueError("broker position has invalid lifecycle")
    if "entry_date" in raw:
        _broker_date(raw["entry_date"], field="position entry_date")
    if "highest_close" in raw and _nonnegative(raw, "highest_close") <= 0:
        raise ValueError("broker position highest_close must be finite and positive")
    return symbol, shares, sellable, avg_cost


def _reconciled_broker_tranches(
    state: _BrokerSyncState,
    *,
    symbol: str,
    shares: int,
    sellable: int,
) -> list[Tranche]:
    tranches = state.economic_tranches.get(symbol, [])
    economic_shares = sum(item.shares for item in tranches)
    if economic_shares > shares:
        _allocate_broker_sale(
            tranches,
            shares=economic_shares - shares,
            fill_date="9999-12-31",
            policy=ReductionPolicy.FIFO.value,
        )
        state.account.reconciliation_events.append(
            {
                "date": state.as_of,
                "symbol": symbol,
                "event": "broker_share_deficit_reconciled",
                "shares": economic_shares - shares,
            }
        )
    elif economic_shares < shares:
        raise ValueError(f"broker position {symbol} exceeds known BUY lot inventory")
    tranches = _align_sellability(
        tranches,
        sellable=sellable,
        as_of=state.as_of,
        next_date=str(state.snapshot_date + timedelta(days=1)),
    )
    if sum(item.shares for item in tranches) != shares:
        raise RuntimeError("broker reconciliation lost tranche shares")
    return tranches


def _reconcile_broker_positions(
    state: _BrokerSyncState,
    *,
    cfg: SystemConfig,
) -> dict[str, Position]:
    reconciled_positions: dict[str, Position] = {}
    for raw in state.raw_positions:
        if not isinstance(raw, dict):
            raise ValueError("each broker position must be an object")
        symbol, position = _reconciled_broker_position(
            state,
            raw,
            reconciled_positions=reconciled_positions,
        )
        reconciled_positions[symbol] = position
    if len(reconciled_positions) > cfg.max_positions:
        raise ValueError("broker snapshot exceeds the production position cap")
    return reconciled_positions


def _settle_broker_strategy_state(
    state: _BrokerSyncState,
    *,
    reconciled_positions: dict[str, Position],
    cfg: SystemConfig,
) -> None:
    account = state.account
    settled_strategic = set(account.strategic_exit_bands) - set(reconciled_positions)
    for symbol in settled_strategic:
        account.strategic_cohort_targets.pop(symbol, None)
        account.strategic_exit_bands.pop(symbol, None)
        account.strategic_active_bands.pop(symbol, None)
        account.strategic_restore_weights.pop(symbol, None)
        account.protected_weights.pop(symbol, None)

    tactical_symbols = (
        {account.tactical_anchor_symbol}
        if account.tactical_anchor_symbol
        else {
            symbol
            for symbol, position in account.positions.items()
            if account.candidate_tenure.get("tactical_active", 0) == 1
            and position.shares > 0
            and position.lifecycle == Lifecycle.RECOVERY.value
        }
    )
    tactical_buy_open = bool(
        tactical_symbols
        and any(
            order.symbol in tactical_symbols
            and order.side == Side.BUY.value
            and order.order_id not in state.completed_order_ids
            for order in account.pending_orders
        )
    )
    if (
        tactical_symbols
        and tactical_symbols.isdisjoint(reconciled_positions)
        and tactical_symbols.isdisjoint(account.protected_weights)
        and tactical_symbols.isdisjoint(account.anchor_weights)
        and not tactical_buy_open
    ):
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promotable"] = 0
        account.candidate_tenure["tactical_cooldown"] = max(
            account.candidate_tenure.get("tactical_cooldown", 0),
            cfg.tactical_rebound_cooldown_days,
        )
        account.tactical_anchor_symbol = ""


def _commit_broker_sync(
    original_account: AccountState,
    state: _BrokerSyncState,
    *,
    reconciled_positions: dict[str, Position],
) -> dict[str, int | str]:
    account = state.account
    account.cash = state.cash
    account.positions = reconciled_positions
    bind_account_strategic_ownership(account)
    account.pending_orders = [
        order for order in account.pending_orders if order.order_id not in state.completed_order_ids
    ]
    pending_by_id = {order.order_id: order for order in account.pending_orders if order.order_id}
    for order_id, pending in pending_by_id.items():
        entry = state.ledger.get(order_id)
        if entry is not None:
            pending.remaining_shares = entry.remaining_shares
    account.broker_as_of = state.as_of
    _validate_position_state(account, validate_attribution=True)
    _validate_order_state(
        account,
        sequence_was_explicit=False,
        validate_attribution=True,
    )
    _validate_strategy_risk_state(account)
    _validate_lot_origin_chains(account)
    for state_field in fields(AccountState):
        setattr(original_account, state_field.name, getattr(account, state_field.name))
    return {
        "as_of": state.as_of,
        "fills_imported": state.imported,
        "positions_reconciled": len(reconciled_positions),
        "pending_orders": len(account.pending_orders),
    }


def sync_broker_snapshot(
    account: AccountState,
    payload: dict[str, Any],
    *,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, int | str]:
    """Apply one idempotent, broker-authoritative account snapshot.

    The snapshot owns cash, positions, available shares, and reported fills.
    Strategy state remains intact so the next decision continues the same
    lifecycle.  Every fill must reference the engine's broker-visible order ID.
    """
    original_account = account
    state = _prepare_broker_sync(account, payload)
    _import_broker_fills(state)
    _apply_broker_order_updates(state)
    reconciled_positions = _reconcile_broker_positions(state, cfg=cfg)
    _settle_broker_strategy_state(state, reconciled_positions=reconciled_positions, cfg=cfg)
    return _commit_broker_sync(
        original_account,
        state,
        reconciled_positions=reconciled_positions,
    )
