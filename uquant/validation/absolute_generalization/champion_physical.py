"""Reconcile immutable target, order and fill links in a literal champion path."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from uquant.types import AccountOrder, AccountState

from .evidence_codec import evidence_mapping, evidence_number, evidence_sequence, evidence_text


def _validate_order_target(
    order: AccountOrder, *, account: AccountState,
    traced_orders: list[tuple[str, Mapping[str, object]]],
    targets: list[tuple[str, Mapping[str, object]]],
) -> None:
    matching = [(session, item) for session, item in traced_orders if item.get("order_id") == order.order_id]
    if not matching or not any(
        (item.get("symbol"), item.get("side"), item.get("event_id"), item.get("grant_id"),
         item.get("epoch_id"), item.get("signal_date"), item.get("target_weight"))
        == (order.symbol, order.side, order.event_id, order.grant_id, order.epoch_id,
            order.signal_date, round(order.target_weight, 12)) for _session, item in matching
    ):
        raise ValueError("champion runtime physical order lacks its traced economic identity")
    if not any(session <= min(date for date, _item in matching)
               and (target.get("symbol"), target.get("event_id", ""), target.get("grant_id", ""), target.get("epoch_id", ""))
               == (order.symbol, order.event_id, order.grant_id, order.epoch_id)
               and (order.side != "BUY" or evidence_number(target.get("weight"), label="target weight") > 0)
               for session, target in targets):
        raise ValueError("champion runtime physical order lacks its causal target")
    if sum(fill.shares for fill in account.fills if fill.order_id == order.order_id) != order.filled_shares:
        raise ValueError("champion runtime physical filled quantity differs")


def validate_champion_physical_links(account: AccountState, trace: Sequence[Mapping[str, object]]) -> None:
    orders = {order.order_id: order for order in account.order_ledger}
    for row in trace:
        if "session" in row and row["session"] != row.get("date"):
            raise ValueError("champion runtime date/session conflict")
        symbols = [item["symbol"] for item in cast(Sequence[Mapping[str, object]], row["targets"])]
        if len(symbols) != len(set(symbols)):
            raise ValueError("champion runtime duplicate symbol target")
    targets = [(str(row["date"]), evidence_mapping(target, label="champion target"))
               for row in trace for target in evidence_sequence(row.get("targets"), label="targets")]
    event_owners: dict[str, tuple[object, object, object]] = {}
    for _session, target in targets:
        event = evidence_text(target.get("event_id", ""), label="target event", empty=True)
        if event:
            owner = (target.get("symbol"), target.get("grant_id", ""), target.get("epoch_id", ""))
            if event_owners.setdefault(event, owner) != owner:
                raise ValueError("champion runtime event aliases distinct economic owners")
    traced_orders = [(str(row["date"]), evidence_mapping(order, label="champion order"))
                     for row in trace for order in evidence_sequence(row.get("orders"), label="orders")]
    for order in account.order_ledger:
        _validate_order_target(order, account=account, traced_orders=traced_orders, targets=targets)
    for fill in account.fills:
        filled_order = orders.get(fill.order_id)
        if (filled_order is None or
                (fill.symbol, fill.side, fill.event_id, fill.grant_id, fill.epoch_id, fill.signal_date)
                != (filled_order.symbol, filled_order.side, filled_order.event_id, filled_order.grant_id,
                    filled_order.epoch_id, filled_order.signal_date)
                or not filled_order.signal_date < fill.fill_date
                or fill.shares <= 0):
            raise ValueError("champion runtime fill lacks a causal matching physical order")

