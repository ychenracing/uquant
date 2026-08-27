"""Execution-state reconciliation and account-order closure."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

from ..account import validate_pending_order_for_account_write
from ..models.strategic_grant import record_strategic_grant_submissions
from ..types import (
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    OrderStatus,
    PendingOrder,
    Side,
    order_intent_metadata,
)


def _register_account_order(
    account: AccountState,
    order: PendingOrder,
    *,
    submitted_date: str,
) -> AccountOrder:
    """Reuse a matching ledger order or allocate a stable new order identifier."""

    validate_pending_order_for_account_write(account, order)

    if order.order_id:
        existing = next(
            (item for item in account.order_ledger if item.order_id == order.order_id),
            None,
        )
        if existing is not None:
            pending_metadata = order_intent_metadata(order)
            ledger_metadata = order_intent_metadata(existing)
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
                    f"pending order {order.order_id} immutable metadata differs from account order: "
                    + ", ".join(changed)
                )
            return existing
        raise RuntimeError(f"pending order references unknown account order {order.order_id}")
    order.order_id = f"O{account.next_order_sequence:09d}"
    account.next_order_sequence += 1
    entry = AccountOrder(
        order_id=order.order_id,
        signal_date=order.signal_date,
        submitted_date=submitted_date,
        symbol=order.symbol,
        side=order.side,
        target_weight=order.target_weight,
        reason=order.reason,
        lifecycle=order.lifecycle,
        requested_shares=order.remaining_shares,
        remaining_shares=order.remaining_shares,
        attempts=order.attempts,
        last_update_date=submitted_date,
        reduction_policy=order.reduction_policy,
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        entry_score=order.entry_score,
        entry_confidence=order.entry_confidence,
        entry_regime=order.entry_regime,
        entry_industry_strength=order.entry_industry_strength,
        event_id=order.event_id,
        origin_subsystem=order.origin_subsystem,
        mechanism=order.mechanism,
        origin_lifecycle=order.origin_lifecycle,
        replaces_symbol=order.replaces_symbol,
        industry_at_entry=order.industry_at_entry,
        industry_manifest_sha256=order.industry_manifest_sha256,
        grant_id=order.grant_id,
    )
    account.order_ledger.append(entry)
    return entry


def _active_order_status(order: AccountOrder) -> str:
    """Never regress an already-partial broker order back to merely open."""
    return str(OrderStatus.PARTIALLY_FILLED.value if order.filled_shares > 0 else OrderStatus.OPEN.value)


def _reconcile_removed_orders(
    *,
    previous: list[PendingOrder],
    current_ids: set[str],
    current_by_symbol: dict[str, PendingOrder],
    ledger: dict[str, AccountOrder],
    preexisting_ids: set[str],
    terminal_statuses: set[str],
    removed_buy_reason: str | None,
    submitted_date: str,
) -> None:
    """Record replacement and cancellation transitions in prior-order sequence."""

    for order in previous:
        if order.order_id in current_ids:
            continue
        entry = ledger[order.order_id]
        if entry.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        }:
            continue
        replacement = current_by_symbol.get(order.symbol)
        sentinel_cancel_request = bool(
            removed_buy_reason is not None
            and order.side == Side.BUY.value
            and (replacement is None or replacement.side == Side.SELL.value)
        )
        existing_cancel_request = bool(
            entry.cancel_reason == "sentinel_freeze_new_risk" and entry.status not in terminal_statuses
        )
        if (sentinel_cancel_request or existing_cancel_request) and order.order_id in preexisting_ids:
            # A broker-visible order remains authoritative until the next
            # snapshot confirms cancellation or reports a final fill. Removing
            # the local continuation intent prevents further risk while this
            # audit marker records the outstanding external action.
            if sentinel_cancel_request:
                if removed_buy_reason is None:
                    raise RuntimeError("Sentinel cancellation reason disappeared")
                entry.cancel_reason = removed_buy_reason
            entry.last_update_date = submitted_date
            entry.last_event = "CANCEL_REQUESTED"
            continue
        entry.status = OrderStatus.REPLACED.value if replacement is not None else OrderStatus.CANCELLED.value
        entry.replaced_by = replacement.order_id if replacement is not None else ""
        if replacement is not None:
            entry.cancel_reason = "daily target changed"
        elif sentinel_cancel_request:
            if removed_buy_reason is None:
                raise RuntimeError("Sentinel cancellation reason disappeared")
            entry.cancel_reason = removed_buy_reason
        else:
            entry.cancel_reason = "daily target removed"
        entry.last_update_date = submitted_date
        entry.last_event = entry.status


def _reconcile_account_orders_mutating(
    *,
    account: AccountState,
    previous: list[PendingOrder],
    current: tuple[PendingOrder, ...],
    submitted_date: str,
    removed_buy_reason: str | None = None,
) -> tuple[PendingOrder, ...]:
    """Persist submissions and cancel/replace transitions without counting fills."""
    preexisting_ids = {order.order_id for order in account.order_ledger}
    preexisting_ledger = {order.order_id: order for order in account.order_ledger}
    terminal_statuses = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    cancel_pending_symbols = {
        entry.symbol
        for entry in preexisting_ledger.values()
        if entry.side == Side.BUY.value
        and entry.cancel_reason == "sentinel_freeze_new_risk"
        and entry.status not in terminal_statuses
    }
    newly_frozen_buy_symbols = {
        order.symbol for order in previous if removed_buy_reason is not None and order.side == Side.BUY.value
    }
    blocked_buy_symbols = cancel_pending_symbols | newly_frozen_buy_symbols
    effective_current = tuple(
        order for order in current if order.side != Side.BUY.value or order.symbol not in blocked_buy_symbols
    )
    for order in previous:
        _register_account_order(
            account,
            order,
            submitted_date=order.signal_date or submitted_date,
        )
    for order in effective_current:
        _register_account_order(account, order, submitted_date=submitted_date)

    reconciled_current = list(effective_current)
    current_ids = {order.order_id for order in effective_current}
    current_by_symbol = {order.symbol: order for order in effective_current}
    ledger = {item.order_id: item for item in account.order_ledger}
    _reconcile_removed_orders(
        previous=previous,
        current_ids=current_ids,
        current_by_symbol=current_by_symbol,
        ledger=ledger,
        preexisting_ids=preexisting_ids,
        terminal_statuses=terminal_statuses,
        removed_buy_reason=removed_buy_reason,
        submitted_date=submitted_date,
    )
    return tuple(
        sorted(
            reconciled_current,
            key=lambda item: (item.side != Side.SELL.value, item.symbol),
        )
    )


def _preflight_reconciliation_batch(
    *,
    previous: list[PendingOrder],
    current: tuple[PendingOrder, ...],
) -> None:
    """Reject active-order cardinality ambiguity before shadow reconciliation."""

    def unique_orders(
        orders: list[PendingOrder] | tuple[PendingOrder, ...],
        *,
        batch: str,
    ) -> dict[str, PendingOrder]:
        seen_symbols: set[str] = set()
        seen_ids: dict[str, PendingOrder] = {}
        for order in orders:
            if order.symbol in seen_symbols:
                raise RuntimeError(f"duplicate {batch} symbol {order.symbol}")
            seen_symbols.add(order.symbol)
            if order.order_id:
                if order.order_id in seen_ids:
                    raise RuntimeError(f"duplicate {batch} order_id {order.order_id}")
                seen_ids[order.order_id] = order
        return seen_ids

    previous_by_id = unique_orders(previous, batch="previous")
    current_by_id = unique_orders(current, batch="current")
    for order_id in sorted(previous_by_id.keys() & current_by_id.keys()):
        if order_intent_metadata(previous_by_id[order_id]) != order_intent_metadata(current_by_id[order_id]):
            raise RuntimeError(f"conflicting previous/current order_id {order_id}")


def reconcile_account_orders(
    *,
    account: AccountState,
    previous: list[PendingOrder],
    current: tuple[PendingOrder, ...],
    submitted_date: str,
    removed_buy_reason: str | None = None,
) -> tuple[PendingOrder, ...]:
    """Reconcile one all-or-nothing batch against a shadow ledger."""

    if removed_buy_reason is not None and not removed_buy_reason:
        raise ValueError("removed buy reason must be non-empty")

    _preflight_reconciliation_batch(previous=previous, current=current)
    shadow_account = deepcopy(account)
    shadow_previous, shadow_current = deepcopy((previous, current))
    shadow_result = _reconcile_account_orders_mutating(
        account=shadow_account,
        previous=shadow_previous,
        current=shadow_current,
        submitted_date=submitted_date,
        removed_buy_reason=removed_buy_reason,
    )

    original_ledger = {order.order_id: order for order in account.order_ledger}
    committed_ledger: list[AccountOrder] = []
    for shadow_order in shadow_account.order_ledger:
        original_order = original_ledger.get(shadow_order.order_id)
        if original_order is None:
            committed_ledger.append(shadow_order)
            continue
        for field in fields(AccountOrder):
            setattr(original_order, field.name, getattr(shadow_order, field.name))
        committed_ledger.append(original_order)
    account.order_ledger = committed_ledger
    account.next_order_sequence = shadow_account.next_order_sequence
    for original, shadow in zip(previous, shadow_previous, strict=True):
        original.order_id = shadow.order_id
    for original, shadow in zip(current, shadow_current, strict=True):
        original.order_id = shadow.order_id
    originals_by_id = {order.order_id: order for order in (*previous, *current) if order.order_id}
    result = tuple(originals_by_id.get(order.order_id, order) for order in shadow_result)
    grant = account.strategic_grant
    if grant is not None:
        record_strategic_grant_submissions(
            grant,
            order_ids=[
                (order.order_id, submitted_date)
                for order in result
                if order.grant_id == grant.grant_id
            ],
        )
    return result


active_order_status = _active_order_status
register_account_order = _register_account_order
