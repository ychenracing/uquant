"""Strict persistence for the single real-account state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .types import (
    AccountOrder,
    AccountState,
    Fill,
    OrderStatus,
    PendingOrder,
    Position,
    Tranche,
)


def _position(payload: dict[str, Any]) -> Position:
    return Position(
        symbol=str(payload["symbol"]),
        shares=int(payload.get("shares", 0)),
        avg_cost=float(payload.get("avg_cost", 0.0)),
        entry_date=str(payload.get("entry_date", "")),
        highest_close=float(payload.get("highest_close", 0.0)),
        lifecycle=str(payload.get("lifecycle", "CORE")),
        tranches=[Tranche(**item) for item in payload.get("tranches", [])],
    )


def _order_sequence(order_id: str) -> int:
    if len(order_id) != 10 or not order_id.startswith("O") or not order_id[1:].isdigit():
        raise RuntimeError(f"account state has invalid order id: {order_id!r}")
    return int(order_id[1:])


def _validate_order_state(
    state: AccountState,
    *,
    sequence_was_explicit: bool,
) -> None:
    identifiers = [item.order_id for item in state.order_ledger]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("account state has duplicate order ids")
    sequences = [_order_sequence(order_id) for order_id in identifiers]
    required_next = max(sequences, default=0) + 1
    if sequence_was_explicit and state.next_order_sequence < required_next:
        raise RuntimeError("account state next order sequence would reuse an order id")
    state.next_order_sequence = max(state.next_order_sequence, required_next)
    if state.next_order_sequence <= 0:
        raise RuntimeError("account state has invalid next order sequence")

    statuses = {item.value for item in OrderStatus}
    ledger = {item.order_id: item for item in state.order_ledger}
    for item in state.order_ledger:
        if item.status not in statuses:
            raise RuntimeError(f"account state has invalid order status: {item.status!r}")
        if min(
            item.requested_shares,
            item.filled_shares,
            item.remaining_shares,
            item.attempts,
        ) < 0:
            raise RuntimeError("account state has negative order lifecycle values")
        if item.requested_shares and item.filled_shares > item.requested_shares:
            raise RuntimeError("account state has overfilled order")

    pending_ids = [item.order_id for item in state.pending_orders if item.order_id]
    if len(pending_ids) != len(set(pending_ids)):
        raise RuntimeError("account state has duplicate pending order ids")
    terminal = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    for order_id in pending_ids:
        item = ledger.get(order_id)
        if item is None:
            raise RuntimeError("pending order references an unknown account order")
        if item.status in terminal:
            raise RuntimeError("pending order references a terminal account order")
    for fill in state.fills:
        if fill.order_id and fill.order_id not in ledger:
            raise RuntimeError("fill references an unknown account order")
    fill_ids = [fill.fill_id for fill in state.fills if fill.fill_id]
    if len(fill_ids) != len(set(fill_ids)):
        raise RuntimeError("account state has duplicate broker fill ids")


def load_account(path: str | Path, *, require_hashes: bool = True) -> AccountState:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"account state is missing or corrupt: {source}") from exc
    sequence_was_explicit = "next_order_sequence" in payload
    try:
        state = AccountState(
            initial_cash=float(payload["initial_cash"]),
            cash=float(payload["cash"]),
            positions={symbol: _position(item) for symbol, item in payload.get("positions", {}).items()},
            pending_orders=[PendingOrder(**item) for item in payload.get("pending_orders", [])],
            order_ledger=[AccountOrder(**item) for item in payload.get("order_ledger", [])],
            next_order_sequence=int(payload.get("next_order_sequence", 1)),
            fills=[Fill(**item) for item in payload.get("fills", [])],
            opportunity=str(payload.get("opportunity", "CHOPPY")),
            risk=str(payload.get("risk", "NORMAL")),
            shock_state=str(payload.get("shock_state", "NONE")),
            cooldown_until=str(payload.get("cooldown_until", "")),
            operating_peak=float(payload.get("operating_peak", payload["initial_cash"])),
            capital_peak=float(payload.get("capital_peak", payload["initial_cash"])),
            leader_tenure={str(k): int(v) for k, v in payload.get("leader_tenure", {}).items()},
            candidate_tenure={str(k): int(v) for k, v in payload.get("candidate_tenure", {}).items()},
            replacement_tenure={str(k): int(v) for k, v in payload.get("replacement_tenure", {}).items()},
            active_leaders=[str(item) for item in payload.get("active_leaders", [])],
            dynamic_k=int(payload.get("dynamic_k", 0)),
            last_k_change_date=str(payload.get("last_k_change_date", "")),
            satellite_entry_dates={
                str(k): str(v) for k, v in payload.get("satellite_entry_dates", {}).items()
            },
            risk_streaks={str(k): int(v) for k, v in payload.get("risk_streaks", {}).items()},
            rotation_dates=[str(item) for item in payload.get("rotation_dates", [])],
            replacement_events=list(payload.get("replacement_events", [])),
            lifecycle_events=list(payload.get("lifecycle_events", [])),
            risk_events=list(payload.get("risk_events", [])),
            anchor_weights={str(k): float(v) for k, v in payload.get("anchor_weights", {}).items()},
            recovery_anchor_date=str(payload.get("recovery_anchor_date", "")),
            tactical_anchor_symbol=str(payload.get("tactical_anchor_symbol", "")),
            protected_weights={
                str(k): float(v) for k, v in payload.get("protected_weights", {}).items()
            },
            strategic_cohort_symbols=[
                str(item) for item in payload.get("strategic_cohort_symbols", [])
            ],
            strategic_cohort_targets={
                str(k): float(v)
                for k, v in payload.get("strategic_cohort_targets", {}).items()
            },
            strategic_exit_bands={
                str(k): [float(item) for item in values]
                for k, values in payload.get("strategic_exit_bands", {}).items()
            },
            strategic_active_bands={
                str(k): [bool(item) for item in values]
                for k, values in payload.get("strategic_active_bands", {}).items()
            },
            strategic_restore_weights={
                str(k): float(v)
                for k, v in payload.get("strategic_restore_weights", {}).items()
            },
            shock_start_date=str(payload.get("shock_start_date", "")),
            shock_severity=str(payload.get("shock_severity", "NORMAL")),
            last_shock_date=str(payload.get("last_shock_date", "")),
            last_successful_run=str(payload.get("last_successful_run", "")),
            data_hash=str(payload.get("data_hash", "")),
            data_hash_as_of=str(payload.get("data_hash_as_of", "")),
            data_hash_symbols=[
                str(item) for item in payload.get("data_hash_symbols", [])
            ],
            code_hash=str(payload.get("code_hash", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("account state violates schema") from exc
    if state.initial_cash <= 0 or state.cash < -1e-6:
        raise RuntimeError("account state violates cash invariants")
    _validate_order_state(state, sequence_was_explicit=sequence_was_explicit)
    if require_hashes and (not state.data_hash or not state.code_hash):
        raise RuntimeError("account state missing validation hashes")
    return state


def save_account(state: AccountState, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=destination.name, dir=destination.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state.to_dict(), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
