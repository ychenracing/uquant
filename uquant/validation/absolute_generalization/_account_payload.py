"""Strict current-schema account decoding for absolute validation evidence."""

from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import cast

from uquant.account.codec import account_from_dict
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.types import AccountState

from .replay import AbsoluteGeneralizationReplayPayload

_ORDER_STATUS_EVENTS = MappingProxyType({
    "SUBMITTED": frozenset(
        {
            "AWAITING_HANDOFF_SELL",
            "CANCEL_REQUESTED",
            "CAPACITY_OR_CASH_BLOCKED",
            "INSUFFICIENT_HISTORY",
            "LIMIT_BLOCKED",
            "MISSING_OR_SUSPENDED",
            "POSITION_CAP_BLOCKED",
            "SUBMITTED",
            "WAITING_NEXT_OPEN",
        }
    ),
    "OPEN": frozenset(
        {
            "AWAITING_HANDOFF_SELL",
            "CANCEL_REQUESTED",
            "CAPACITY_OR_CASH_BLOCKED",
            "INSUFFICIENT_HISTORY",
            "LIMIT_BLOCKED",
            "MISSING_OR_SUSPENDED",
            "POSITION_CAP_BLOCKED",
            "WAITING_NEXT_OPEN",
        }
    ),
    "PARTIALLY_FILLED": frozenset(
        {"BROKER_FILL", "CANCEL_REQUESTED", "FILL", "PARTIAL_REMAINDER_RELEASED"}
    ),
    "FILLED": frozenset({"BROKER_FILL", "FILL", "FILLED"}),
    "CANCELLED": frozenset(
        {
            "BROKER_CANCELLED",
            "CANCELLED",
            "LATE_FILL_SUPPRESSED_RETRY",
            "PARTIAL_REMAINDER_RELEASED",
            "ZERO_REQUEST",
        }
    ),
    "REPLACED": frozenset({"REPLACED"}),
})


def _validate_account_runtime(account: AccountState) -> None:
    ledger_ids = {order.order_id for order in account.order_ledger}
    grant = account.strategic_grant
    if grant is not None:
        submitted = set(grant.submitted_order_ids)
        acknowledged = set(grant.acknowledged_order_ids)
        if not submitted.issubset(ledger_ids):
            raise ValueError("absolute reachability grant submitted order is absent from ledger")
        if not acknowledged.issubset(submitted):
            raise ValueError("absolute reachability grant acknowledged order was not submitted")
    for order in account.order_ledger:
        allowed = _ORDER_STATUS_EVENTS.get(order.status)
        if allowed is None or order.last_event not in allowed:
            raise ValueError("absolute reachability order status/event pair is impossible")


def validate_account_payload(value: object) -> AccountState:
    """Decode and validate one canonical current-schema replay account."""

    if type(value) is not AbsoluteGeneralizationReplayPayload:
        raise ValueError("absolute reachability account payload type differs")
    payload = value
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise ValueError("absolute reachability account payload digest differs")
    raw = strict_json_loads(payload.canonical_json)
    if canonical_json_bytes(raw) != payload.canonical_json:
        raise ValueError("absolute reachability account payload is not canonical")
    if type(raw) is not dict or any(type(key) is not str for key in raw):
        raise ValueError("absolute reachability account payload is malformed")
    account_raw = cast(dict[str, object], raw)
    required_containers = {
        "fills": list,
        "flat_book_capital_repair": dict,
        "order_ledger": list,
        "pending_orders": list,
        "positions": dict,
        "strategic_epochs": list,
    }
    if any(
        type(account_raw.get(field)) is not expected
        for field, expected in required_containers.items()
    ):
        raise ValueError("absolute reachability account payload is invalid")
    try:
        account = account_from_dict(account_raw, require_hashes=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("absolute reachability account payload is invalid") from exc
    _validate_account_runtime(account)
    return account


__all__ = ("validate_account_payload",)
