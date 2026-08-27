"""Broker payload parsing and deterministic fill-order contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

from .types import AccountOrder, AccountState, Fill, OrderStatus


@dataclass(frozen=True, slots=True)
class BrokerFillValues:
    """Validated economic fields for one broker fill."""

    fill_id: str
    order_id: str
    order: AccountOrder
    symbol: str
    side: str
    shares: int
    price: float
    fill_date: str
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    gross: float
    final: bool
    remaining: int
    cumulative_filled: int
    reported_request: int
    existing_fill: Fill | None


def broker_nonnegative(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Return a finite nonnegative broker numeric field."""

    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"broker field {key} must be a finite number")
    value = float(raw_value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"broker field {key} must be finite and nonnegative")
    return value


def broker_integer(
    payload: dict[str, Any],
    key: str,
    *,
    default: int | None = None,
    positive: bool = False,
) -> int:
    """Return a broker integer field under its sign contract."""

    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"broker field {key} must be an integer")
    if raw_value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"broker field {key} must be {qualifier}")
    return raw_value


def broker_date(value: Any, *, field: str) -> date_type:
    """Return a broker ISO date with the established error contract."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"broker {field} requires an ISO date")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"broker {field} requires an ISO date") from exc


type _PreparedBrokerFill = tuple[date_type, int | None, str, str, dict[str, Any]]


def _late_strategic_fill_allowed(order: AccountOrder) -> bool:
    return bool(
        order.status == OrderStatus.CANCELLED.value
        and order.grant_id
        and order.cancel_reason == "strategic partial remainder replaced"
        and order.filled_shares > 0
        and order.remaining_shares > 0
    )


def _prepare_broker_fills(raw_fills: list[Any], *, as_of: str) -> list[_PreparedBrokerFill]:
    prepared: list[_PreparedBrokerFill] = []
    seen_fill_ids: set[str] = set()
    for raw in raw_fills:
        if not isinstance(raw, dict):
            raise ValueError("each broker fill must be an object")
        fill_id = str(raw.get("fill_id", "")).strip()
        if not fill_id:
            raise ValueError("each broker fill requires a stable fill_id")
        if fill_id in seen_fill_ids:
            raise ValueError(f"broker snapshot repeats fill_id {fill_id!r}")
        seen_fill_ids.add(fill_id)
        order_id = str(raw.get("order_id", "")).strip()
        fill_date = broker_date(raw.get("fill_date", as_of), field="fill_date")
        if "final" not in raw:
            raise ValueError("broker fill requires explicit boolean final")
        if not isinstance(raw["final"], bool):
            raise ValueError("broker fill final must be boolean")
        if "remaining_shares" not in raw:
            raise ValueError("broker fill requires explicit remaining_shares")
        broker_integer(raw, "remaining_shares")
        sequence: int | None = None
        if "execution_sequence" in raw:
            sequence = broker_integer(raw, "execution_sequence", positive=True)
        prepared.append((fill_date, sequence, order_id, fill_id, raw))
    return prepared


def _validate_novel_fill_sequences(novel: list[_PreparedBrokerFill]) -> None:
    novel_by_date: dict[date_type, list[_PreparedBrokerFill]] = {}
    for item in novel:
        novel_by_date.setdefault(item[0], []).append(item)
    for fill_date, same_date in novel_by_date.items():
        if len(same_date) <= 1:
            continue
        sequences = [item[1] for item in same_date]
        if any(sequence is None for sequence in sequences):
            raise ValueError(
                f"multiple new broker fills on {fill_date.isoformat()} require explicit execution_sequence"
            )
        concrete_sequences = [int(sequence) for sequence in sequences if sequence is not None]
        if len(concrete_sequences) != len(set(concrete_sequences)):
            raise ValueError(f"broker execution_sequence must be unique on {fill_date.isoformat()}")


def _validate_same_day_fill_continuation(
    *,
    order_id: str,
    ordered: list[_PreparedBrokerFill],
    last_update: date_type,
    account_fills_by_order_date: dict[tuple[str, date_type], list[Fill]],
    prepared_by_id: dict[str, _PreparedBrokerFill],
) -> None:
    known_same_day = account_fills_by_order_date.get((order_id, last_update), [])
    known_items = [prepared_by_id.get(fill.fill_id) for fill in known_same_day]
    if (
        not known_same_day
        or any(item is None or item[1] is None for item in known_items)
        or any(item[1] is None for item in ordered if item[0] == last_update)
    ):
        raise ValueError(
            "same-day broker fill continuation requires all previously imported "
            "order fills and explicit execution_sequence"
        )
    prior_sequences = [int(item[1]) for item in known_items if item is not None and item[1] is not None]
    if len(prior_sequences) != len(set(prior_sequences)):
        raise ValueError("same-day imported broker fills require unique execution_sequence")
    continuation_sequences = [
        int(item[1]) for item in ordered if item[0] == last_update and item[1] is not None
    ]
    if continuation_sequences and min(continuation_sequences) <= max(prior_sequences):
        raise ValueError("same-day broker fill continuation sequence must follow imported fills")


def _validate_order_fill_continuations(
    *,
    prepared: list[_PreparedBrokerFill],
    novel: list[_PreparedBrokerFill],
    account: AccountState,
) -> None:
    prepared_by_id = {item[3]: item for item in prepared}
    account_fills_by_order_date: dict[tuple[str, date_type], list[Fill]] = {}
    for fill in account.fills:
        if not fill.order_id:
            continue
        key = (fill.order_id, broker_date(fill.fill_date, field="stored fill_date"))
        account_fills_by_order_date.setdefault(key, []).append(fill)

    novel_by_order: dict[str, list[_PreparedBrokerFill]] = {}
    for item in novel:
        novel_by_order.setdefault(item[2], []).append(item)
    for order_id, order_fills in novel_by_order.items():
        ordered = sorted(
            order_fills,
            key=lambda item: (item[0], item[1] if item[1] is not None else 0, item[3]),
        )
        order = next((item for item in account.order_ledger if item.order_id == order_id), None)
        if order is not None and order.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        } and not _late_strategic_fill_allowed(order):
            raise ValueError("broker cannot append a fill to a terminal account order")
        if order is not None and order.last_update_date and order.filled_shares:
            last_update = broker_date(order.last_update_date, field="order last_update_date")
            first_new_date = ordered[0][0]
            if first_new_date < last_update:
                raise ValueError(f"broker fill for order {order_id!r} predates its latest imported fill")
            if first_new_date == last_update:
                _validate_same_day_fill_continuation(
                    order_id=order_id,
                    ordered=ordered,
                    last_update=last_update,
                    account_fills_by_order_date=account_fills_by_order_date,
                    prepared_by_id=prepared_by_id,
                )
        for item in ordered[:-1]:
            if item[4]["final"]:
                raise ValueError(f"final broker fill for order {order_id!r} must be its last reported fill")


def ordered_broker_fills(
    raw_fills: list[Any],
    *,
    as_of: str,
    account: AccountState,
    known_fills: dict[str, Fill],
) -> list[dict[str, Any]]:
    """Return broker fills in a deterministic, causally valid order."""

    prepared = _prepare_broker_fills(raw_fills, as_of=as_of)
    novel = [item for item in prepared if item[3] not in known_fills]
    _validate_novel_fill_sequences(novel)
    _validate_order_fill_continuations(prepared=prepared, novel=novel, account=account)
    return [
        item[4]
        for item in sorted(
            prepared,
            key=lambda item: (item[0], item[1] if item[1] is not None else 0, item[2], item[3]),
        )
    ]
