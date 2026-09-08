"""Immutable ordinary entry and graduation proofs, linked to real execution."""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import date as calendar_date
from typing import TYPE_CHECKING, Any

from ..contracts.strict_json import canonical_json_sha256

if TYPE_CHECKING:
    from ..config import SystemConfig
    from ..types import AccountState, PendingOrder, RiskAssessment

ENTRY = "ORDINARY_PULLBACK_ENTRY"
GRADUATION = "ORDINARY_PULLBACK_GRADUATION"
REASON = "ordinary_pullback_entry"


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def pullback_order_entry(account: AccountState, order: Any) -> dict[str, Any] | None:
    return next((event for event in account.lifecycle_events
                 if event.get("event") == ENTRY and event["order_id"] == order.order_id
                 and event["event_id"] == order.event_id and event["symbol"] == order.symbol), None)


def holding_pullback_entry(account: AccountState, symbol: str) -> dict[str, Any] | None:
    """Resolve the actual last zero boundary, including same-day SELL then BUY."""
    position = account.positions.get(symbol)
    if position is None or position.shares <= 0:
        return None
    remaining = position.shares
    for fill in reversed(account.fills):
        if fill.symbol != symbol:
            continue
        remaining += fill.shares if fill.side == "SELL" else -fill.shares
        if remaining < 0:
            return None
        if remaining == 0:
            return pullback_order_entry(account, fill) if fill.side == "BUY" else None
    return None  # Imported or incomplete quantity history supplies no new basis.


def pullback_graduated(account: AccountState, entry: dict[str, Any]) -> bool:
    return any(event.get("event") == GRADUATION and event["entry_sha256"] == entry["canonical_sha256"]
               for event in account.lifecycle_events)


def _new_entry_event(*, order: PendingOrder, ledger: dict[str, Any], permission: dict[str, Any],
                     account: AccountState, date: str, cfg: SystemConfig,
                     code_hash: str, data_hash: str) -> dict[str, Any]:
    from ..config import config_fingerprint
    maximum = permission["maximum_weight"]
    recorded = ledger.get(order.order_id)
    proof = permission["proofs"].get(order.symbol)
    if (recorded is None or recorded.event_id != order.event_id or recorded.filled_shares
            or recorded.submitted_date != date or order.signal_date != date
            or recorded.grant_id or recorded.epoch_id or not proof or proof["block"] != "READY"):
        raise RuntimeError("pullback entry must bind a new unfilled ordinary order")
    return _sealed({
        "event": ENTRY, "date": date, "symbol": order.symbol,
        "order_id": order.order_id, "event_id": order.event_id,
        "account_identity": account.account_identity, "code_hash": code_hash,
        "data_hash": data_hash, "config_sha256": config_fingerprint(cfg),
        "maximum_weight": maximum, "risk_state": permission["risk_state"],
        "proof": deepcopy(proof),
    })


def bind_pullback_orders(
    *, account: AccountState, orders: tuple[PendingOrder, ...], risk: RiskAssessment,
    date: str, cfg: SystemConfig, code_hash: str, data_hash: str,
) -> None:
    """Bind only actual newly reconciled orders; never create permission from fills."""
    selected = [order for order in orders if order.side == "BUY" and order.reason_code == REASON
                and pullback_order_entry(account, order) is None]
    if not selected:
        return
    permission = risk.evidence.get("ordinary_pullback_permission")
    if (not isinstance(permission, dict) or permission.get("as_of") != date
            or risk.evidence.get("sentinel_freeze_new_risk", False)):
        raise RuntimeError("new pullback order lacks current final BaseRisk permission")
    maximum = permission["maximum_weight"]
    if (not 0 < maximum <= cfg.core_admission_weight
            or sum(o.target_weight for o in selected) > maximum + 1e-12):
        raise RuntimeError("pullback orders exceed their shared capital permission")
    ledger = {o.order_id: o for o in account.order_ledger}
    additions = [_new_entry_event(
        order=order, ledger=ledger, permission=permission, account=account,
        date=date, cfg=cfg, code_hash=code_hash, data_hash=data_hash) for order in selected]
    account.lifecycle_events.extend(additions)


def record_pullback_graduation(
    account: AccountState, entry: dict[str, Any], *, date: str, proof: dict[str, Any],
) -> None:
    if not pullback_graduated(account, entry):
        account.lifecycle_events.append(_sealed({
            "event": GRADUATION, "date": date, "symbol": entry["symbol"],
            "entry_sha256": entry["canonical_sha256"], "proof": deepcopy(proof),
            "observed_fill_count": len(account.fills),
        }))


def _finite_tree(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and _finite_tree(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_finite_tree(v) for v in value)
    return isinstance(value, (str, bool, int)) or value is None or (
        isinstance(value, float) and math.isfinite(value))


def _validate_event_envelope(event: dict[str, Any], kind: str) -> None:
    common = {"event", "date", "symbol", "proof", "canonical_sha256"}
    extra = ({"order_id", "event_id", "account_identity", "code_hash", "data_hash", "config_sha256",
              "maximum_weight", "risk_state"} if kind == ENTRY else {"entry_sha256", "observed_fill_count"})
    if set(event) != common | extra or not _finite_tree(event):
        raise RuntimeError("ordinary entry audit event schema differs")
    try:
        calendar_date.fromisoformat(event["date"])
    except (ValueError, TypeError) as exc:
        raise RuntimeError("ordinary entry audit event date is invalid") from exc
    if canonical_json_sha256({k: v for k, v in event.items() if k != "canonical_sha256"}) != event["canonical_sha256"]:
        raise RuntimeError("ordinary entry audit event seal differs")
    if not isinstance(event["proof"], dict):
        raise RuntimeError("ordinary entry proof must be an object")


def _validate_graduation_holding(account: AccountState, event: dict[str, Any],
                                 entry: dict[str, Any]) -> None:
    shares = 0
    original_episode = False
    count = event["observed_fill_count"]
    if (type(count) is not int or not 0 < count <= len(account.fills)
            or any(fill.fill_date > event["date"] for fill in account.fills[:count])):
        raise RuntimeError("ordinary graduation fill boundary differs")
    for fill in account.fills[:count]:
        if fill.symbol != event["symbol"]:
            continue
        if shares == 0 and fill.side == "BUY":
            original_episode = fill.order_id == entry["order_id"] and fill.event_id == entry["event_id"]
        shares += fill.shares if fill.side == "BUY" else -fill.shares
        if shares <= 0:
            original_episode = False
    if not original_episode or shares <= 0:
        raise RuntimeError("ordinary graduation lacks its actual continuous holding")


def _validate_graduation(account: AccountState, event: dict[str, Any],
                         entries: dict[str, dict[str, Any]], graduated: set[str]) -> None:
    entry = entries.get(event["entry_sha256"])
    if (entry is None or entry["symbol"] != event["symbol"] or entry["date"] > event["date"]
            or event["entry_sha256"] in graduated):
        raise RuntimeError("ordinary graduation lacks one original entry")
    _validate_graduation_holding(account, event, entry)
    proof = event["proof"]
    if (set(proof) != {"close", "ma60", "ret60", "mature", "liquidity_confirmed"}
            or proof["mature"] is not True or proof["liquidity_confirmed"] is not True
            or any(isinstance(proof[k], bool) or not isinstance(proof[k], (int, float))
                   for k in ("close", "ma60", "ret60"))
            or not proof["close"] >= proof["ma60"] > 0 or proof["ret60"] <= 0):
        raise RuntimeError("ordinary graduation proof differs")
    graduated.add(event["entry_sha256"])


def _validate_entry_proof(event: dict[str, Any], order: Any) -> None:
    proof = event["proof"]
    if (set(proof) != {"as_of", "symbol", "values", "block"} or proof["as_of"] != event["date"]
            or proof["symbol"] != order.symbol or proof["block"] != "READY"
            or not isinstance(proof["values"], dict)):
        raise RuntimeError("ordinary entry audit stock proof differs")
    values = proof["values"]
    value_keys = {"close", "ma120", "ret20", "ret60", "ret120", "secular_score", "secular_confidence",
                  "momentum60", "momentum120", "relative_strength", "leader_confidence",
                  "positive_amount_sessions", "median_amount"}
    if (set(values) != value_keys or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                       for v in values.values())
            or not values["close"] >= values["ma120"] > 0
            or not 10 <= values["positive_amount_sessions"] <= 20 or values["median_amount"] <= 0):
        raise RuntimeError("ordinary entry audit values differ")


def _validate_entry_identity(event: dict[str, Any]) -> None:
    for key in ("code_hash", "data_hash", "config_sha256"):
        value = event[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise RuntimeError("ordinary entry audit identity is invalid")


def _validate_entry_order(account: AccountState, event: dict[str, Any],
                          ledger: dict[str, Any], order_ids: set[str]) -> Any:
    order = ledger.get(event["order_id"])
    if (order is None or order.order_id in order_ids or order.event_id != event["event_id"]
            or order.symbol != event["symbol"] or order.side != "BUY" or order.reason_code != REASON
            or order.grant_id or order.epoch_id or order.submitted_date != event["date"]
            or order.signal_date != event["date"] or event["account_identity"] != account.account_identity):
        raise RuntimeError("ordinary entry audit event order binding differs")
    _validate_entry_identity(event)
    maximum = event["maximum_weight"]
    if (isinstance(maximum, bool) or not isinstance(maximum, (int, float))
            or not 0 < maximum <= 1 or not 0 < order.target_weight <= maximum + 1e-12
            or event["risk_state"] not in {"NORMAL", "CAUTION"}):
        raise RuntimeError("ordinary entry audit budget differs")
    return order


def validate_pullback_events(account: AccountState) -> None:
    """Validate the special pre-fill union before ordinary filled lifecycle events."""
    entries: dict[str, dict[str, Any]] = {}
    order_ids: set[str] = set()
    graduated: set[str] = set()
    ledger = {o.order_id: o for o in account.order_ledger}
    batches: dict[tuple[str, str], float] = {}
    if not isinstance(account.lifecycle_events, list):
        raise RuntimeError("lifecycle_events must be an array")
    for event in account.lifecycle_events:
        if not isinstance(event, dict):
            raise RuntimeError("lifecycle event must be an object")
        kind = event.get("event")
        if kind not in {ENTRY, GRADUATION}:
            continue
        _validate_event_envelope(event, kind)
        if kind == GRADUATION:
            _validate_graduation(account, event, entries, graduated)
            continue
        order = _validate_entry_order(account, event, ledger, order_ids)
        maximum = event["maximum_weight"]
        _validate_entry_proof(event, order)
        batch = (event["date"], event["code_hash"])
        batches[batch] = batches.get(batch, 0.0) + order.target_weight
        if batches[batch] > maximum + 1e-12:
            raise RuntimeError("ordinary entry audit shared budget exceeded")
        entries[event["canonical_sha256"]] = event
        order_ids.add(order.order_id)
