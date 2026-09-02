"""Strict current-schema account decoding for absolute validation evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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


def _normalize_attribution_item(
    value: object,
    *,
    epochs: Mapping[object, object],
    symbol: object = None,
) -> None:
    if type(value) is not dict or value.get("grant_id") != "":
        return
    epoch = epochs.get(value.get("epoch_id"))
    observed_symbol = value.get("symbol", symbol)
    if (
        type(epoch) is dict
        and value.get("origin_subsystem") in {"RISK", "STRATEGIC"}
        and type(observed_symbol) is str
        and observed_symbol
        and observed_symbol != epoch.get("owner_symbol")
        and type(epoch.get("grant_id")) is str
        and epoch.get("grant_id")
    ):
        value["grant_id"] = epoch["grant_id"]


def _normalize_execution_attribution(
    account_raw: Mapping[str, object],
    epochs: Mapping[object, object],
) -> None:
    for name in ("pending_orders", "order_ledger", "fills"):
        values = account_raw.get(name)
        if type(values) is not list:
            continue
        for item in values:
            _normalize_attribution_item(item, epochs=epochs)
            if name == "fills" and type(item) is dict:
                sold = item.get("sold_tranches")
                if type(sold) is list:
                    for tranche in sold:
                        _normalize_attribution_item(
                            tranche,
                            epochs=epochs,
                            symbol=item.get("symbol"),
                        )


def _normalize_position_attribution(
    account_raw: Mapping[str, object],
    epochs: Mapping[object, object],
) -> None:
    positions = account_raw.get("positions")
    if type(positions) is not dict:
        return
    for symbol, position in positions.items():
        if type(position) is not dict:
            continue
        tranches = position.get("tranches")
        if type(tranches) is list:
            for tranche in tranches:
                _normalize_attribution_item(
                    tranche,
                    epochs=epochs,
                    symbol=symbol,
                )
        epoch = epochs.get(position.get("epoch_id"))
        if (
            position.get("grant_id") == ""
            and type(epoch) is dict
            and symbol != epoch.get("owner_symbol")
            and type(tranches) is list
            and tranches
            and all(
                type(tranche) is dict
                and tranche.get("epoch_id") == position.get("epoch_id")
                and tranche.get("grant_id") == epoch.get("grant_id")
                for tranche in tranches
            )
        ):
            position["grant_id"] = epoch["grant_id"]


def normalize_epoch_only_cohort_attribution(
    account_raw: Mapping[str, object],
) -> None:
    """Restore cohort attribution accepted by the current account codec."""

    epochs_value = account_raw.get("strategic_epochs")
    if type(epochs_value) is not list:
        return
    epochs = {
        item.get("epoch_id"): item
        for item in epochs_value
        if type(item) is dict and type(item.get("epoch_id")) is str
    }
    _normalize_execution_attribution(account_raw, epochs)
    _normalize_position_attribution(account_raw, epochs)


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
    normalize_epoch_only_cohort_attribution(account_raw)
    try:
        account = account_from_dict(account_raw, require_hashes=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("absolute reachability account payload is invalid") from exc
    _validate_account_runtime(account)
    return account


__all__ = (
    "normalize_epoch_only_cohort_attribution",
    "validate_account_payload",
)
