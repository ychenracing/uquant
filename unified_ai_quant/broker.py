"""Authoritative broker snapshot and manual-fill reconciliation."""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
from typing import Any

from .config import DEFAULT_CONFIG, SystemConfig
from .data import normalize_symbol
from .types import (
    AccountState,
    Fill,
    Lifecycle,
    OrderStatus,
    Position,
    Side,
    Tranche,
)


def _nonnegative(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = float(payload.get(key, default))
    if value < 0:
        raise ValueError(f"broker field {key} cannot be negative")
    return value


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
    as_of = str(payload.get("as_of", ""))
    try:
        snapshot_date = date_type.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("broker snapshot requires ISO as_of date") from exc
    if account.last_successful_run and as_of < account.last_successful_run:
        raise ValueError("broker snapshot predates the last successful decision")

    cash = _nonnegative(payload, "cash")
    raw_positions = payload.get("positions")
    raw_fills = payload.get("fills", [])
    if not isinstance(raw_positions, list) or not isinstance(raw_fills, list):
        raise ValueError("broker positions and fills must be arrays")

    ledger = {order.order_id: order for order in account.order_ledger}
    known_fill_ids = {fill.fill_id for fill in account.fills if fill.fill_id}
    imported = 0
    completed_order_ids: set[str] = set()
    for raw in raw_fills:
        if not isinstance(raw, dict):
            raise ValueError("each broker fill must be an object")
        fill_id = str(raw.get("fill_id", "")).strip()
        if not fill_id:
            raise ValueError("each broker fill requires a stable fill_id")
        if fill_id in known_fill_ids:
            continue
        order_id = str(raw.get("order_id", "")).strip()
        order = ledger.get(order_id)
        if order is None:
            raise ValueError(f"broker fill references unknown order {order_id!r}")
        symbol = normalize_symbol(str(raw.get("symbol", "")))
        side = str(raw.get("side", "")).upper()
        if symbol != order.symbol or side != order.side:
            raise ValueError("broker fill symbol/side differs from account order")
        if side not in {Side.BUY.value, Side.SELL.value}:
            raise ValueError("broker fill has invalid side")
        shares = int(raw.get("shares", 0))
        price = float(raw.get("price", 0.0))
        if shares <= 0 or price <= 0:
            raise ValueError("broker fill shares and price must be positive")
        fill_date = str(raw.get("fill_date", as_of))
        if date_type.fromisoformat(fill_date) > snapshot_date:
            raise ValueError("broker fill date is after snapshot as_of")
        commission = _nonnegative(raw, "commission")
        stamp_duty = _nonnegative(raw, "stamp_duty")
        transfer_fee = _nonnegative(raw, "transfer_fee")
        slippage_cost = _nonnegative(raw, "slippage_cost")
        gross = _nonnegative(raw, "gross_value", shares * price)
        account.fills.append(
            Fill(
                signal_date=order.signal_date,
                fill_date=fill_date,
                symbol=symbol,
                side=side,
                shares=shares,
                price=price,
                gross_value=gross,
                commission=commission,
                stamp_duty=stamp_duty,
                transfer_fee=transfer_fee,
                slippage_cost=slippage_cost,
                reason=order.reason,
                lifecycle=order.lifecycle,
                order_id=order_id,
                fill_id=fill_id,
            )
        )
        known_fill_ids.add(fill_id)
        imported += 1
        order.filled_shares += shares
        final = bool(raw.get("final", True))
        remaining = int(raw.get("remaining_shares", 0 if final else shares))
        if remaining < 0:
            raise ValueError("broker remaining_shares cannot be negative")
        order.requested_shares = max(
            order.requested_shares,
            order.filled_shares + remaining,
        )
        order.remaining_shares = remaining
        order.status = (
            OrderStatus.FILLED.value
            if final and remaining == 0
            else OrderStatus.PARTIALLY_FILLED.value
        )
        order.last_update_date = fill_date
        order.last_event = "BROKER_FILL"
        if order.status == OrderStatus.FILLED.value:
            completed_order_ids.add(order_id)

    reconciled_positions: dict[str, Position] = {}
    for raw in raw_positions:
        if not isinstance(raw, dict):
            raise ValueError("each broker position must be an object")
        symbol = normalize_symbol(str(raw.get("symbol", "")))
        if symbol in reconciled_positions:
            raise ValueError(f"duplicate broker position {symbol}")
        shares = int(raw.get("shares", 0))
        sellable = int(raw.get("sellable_shares", -1))
        avg_cost = float(raw.get("avg_cost", 0.0))
        if shares <= 0 or avg_cost <= 0 or not 0 <= sellable <= shares:
            raise ValueError(
                "broker position requires positive shares/cost and bounded sellable_shares"
            )
        existing = account.positions.get(symbol)
        lifecycle = str(
            raw.get(
                "lifecycle",
                existing.lifecycle if existing is not None else Lifecycle.CORE.value,
            )
        )
        if lifecycle not in {item.value for item in Lifecycle}:
            raise ValueError("broker position has invalid lifecycle")
        entry_date = str(
            raw.get(
                "entry_date",
                existing.entry_date if existing is not None else as_of,
            )
        )
        highest_close = float(
            raw.get(
                "highest_close",
                max(existing.highest_close if existing is not None else 0.0, avg_cost),
            )
        )
        tranches: list[Tranche] = []
        if sellable:
            tranches.append(
                Tranche(
                    tranche_id=f"broker:{as_of}:{symbol}:sellable",
                    lifecycle=lifecycle,
                    shares=sellable,
                    avg_cost=avg_cost,
                    entry_date=entry_date,
                    sellable_date=as_of,
                    highest_close=highest_close,
                )
            )
        if shares > sellable:
            tranches.append(
                Tranche(
                    tranche_id=f"broker:{as_of}:{symbol}:t1",
                    lifecycle=lifecycle,
                    shares=shares - sellable,
                    avg_cost=avg_cost,
                    entry_date=as_of,
                    sellable_date=str(snapshot_date + timedelta(days=1)),
                    highest_close=highest_close,
                )
            )
        reconciled_positions[symbol] = Position(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            entry_date=entry_date,
            highest_close=highest_close,
            lifecycle=lifecycle,
            tranches=tranches,
        )
    if len(reconciled_positions) > cfg.max_positions:
        raise ValueError("broker snapshot exceeds the production position cap")

    account.cash = cash
    account.positions = reconciled_positions
    account.pending_orders = [
        order
        for order in account.pending_orders
        if order.order_id not in completed_order_ids
    ]
    pending_by_id = {
        order.order_id: order for order in account.pending_orders if order.order_id
    }
    for order_id, pending in pending_by_id.items():
        entry = ledger.get(order_id)
        if entry is not None:
            pending.remaining_shares = entry.remaining_shares
    return {
        "as_of": as_of,
        "fills_imported": imported,
        "positions_reconciled": len(reconciled_positions),
        "pending_orders": len(account.pending_orders),
    }
