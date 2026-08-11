"""Authoritative broker snapshot and manual-fill reconciliation."""

from __future__ import annotations

import copy
import math
from dataclasses import fields, replace
from datetime import date as date_type
from datetime import timedelta
from typing import Any

from .account import (
    _validate_order_state,
    _validate_position_state,
    _validate_strategy_risk_state,
)
from .config import DEFAULT_CONFIG, SystemConfig
from .data import normalize_symbol
from .execution import _allocate_sell_costs, risk_priority_tranche_key
from .types import (
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountState,
    Fill,
    Lifecycle,
    OrderStatus,
    Position,
    ReductionPolicy,
    Side,
    Tranche,
    order_intent_metadata,
)


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


def _nonnegative(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError(f"broker field {key} must be a finite number")
    value = float(raw_value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"broker field {key} must be finite and nonnegative")
    return value


def _broker_integer(
    payload: dict[str, Any],
    key: str,
    *,
    default: int | None = None,
    positive: bool = False,
) -> int:
    raw_value = payload.get(key, default)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"broker field {key} must be an integer")
    if raw_value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"broker field {key} must be {qualifier}")
    return raw_value


def _broker_date(value: Any, *, field: str) -> date_type:
    if not isinstance(value, str) or not value:
        raise ValueError(f"broker {field} requires an ISO date")
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"broker {field} requires an ISO date") from exc


def _ordered_broker_fills(
    raw_fills: list[Any],
    *,
    as_of: str,
    account: AccountState,
    known_fills: dict[str, Fill],
) -> list[dict[str, Any]]:
    """Return broker fills in a deterministic, causally valid order.

    ISO dates establish order across sessions.  Multiple new fills reported for
    one session require a broker execution sequence because list order is not a
    durable execution contract.  An incremental same-session snapshot must
    repeat the already imported fills with their sequences so the continuation
    can be ordered against durable state.
    """
    prepared: list[tuple[date_type, int | None, str, str, dict[str, Any]]] = []
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
        fill_date = _broker_date(raw.get("fill_date", as_of), field="fill_date")
        if "final" not in raw:
            raise ValueError("broker fill requires explicit boolean final")
        if not isinstance(raw["final"], bool):
            raise ValueError("broker fill final must be boolean")
        if "remaining_shares" not in raw:
            raise ValueError("broker fill requires explicit remaining_shares")
        _broker_integer(raw, "remaining_shares")
        sequence: int | None = None
        if "execution_sequence" in raw:
            sequence = _broker_integer(raw, "execution_sequence", positive=True)
        prepared.append((fill_date, sequence, order_id, fill_id, raw))

    novel = [item for item in prepared if item[3] not in known_fills]
    novel_by_date: dict[date_type, list[tuple[date_type, int | None, str, str, dict[str, Any]]]] = {}
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

    prepared_by_id = {item[3]: item for item in prepared}
    account_fills_by_order_date: dict[tuple[str, date_type], list[Fill]] = {}
    for fill in account.fills:
        if not fill.order_id:
            continue
        key = (fill.order_id, _broker_date(fill.fill_date, field="stored fill_date"))
        account_fills_by_order_date.setdefault(key, []).append(fill)

    novel_by_order: dict[str, list[tuple[date_type, int | None, str, str, dict[str, Any]]]] = {}
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
        }:
            raise ValueError("broker cannot append a fill to a terminal account order")
        if order is not None and order.last_update_date and order.filled_shares:
            last_update = _broker_date(order.last_update_date, field="order last_update_date")
            first_new_date = ordered[0][0]
            if first_new_date < last_update:
                raise ValueError(f"broker fill for order {order_id!r} predates its latest imported fill")
            if first_new_date == last_update:
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
                prior_sequences = [
                    int(item[1]) for item in known_items if item is not None and item[1] is not None
                ]
                if len(prior_sequences) != len(set(prior_sequences)):
                    raise ValueError("same-day imported broker fills require unique execution_sequence")
                continuation_sequences = [
                    int(item[1]) for item in ordered if item[0] == last_update and item[1] is not None
                ]
                if continuation_sequences and min(continuation_sequences) <= max(prior_sequences):
                    raise ValueError("same-day broker fill continuation sequence must follow imported fills")
        for item in ordered[:-1]:
            if item[4]["final"]:
                raise ValueError(f"final broker fill for order {order_id!r} must be its last reported fill")

    return [
        item[4]
        for item in sorted(
            prepared,
            key=lambda item: (item[0], item[1] if item[1] is not None else 0, item[2], item[3]),
        )
    ]


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
    if not isinstance(raw_positions, list) or not isinstance(raw_fills, list):
        raise ValueError("broker positions and fills must be arrays")

    # Work on a complete copy.  Fills, ledger transitions, events, cash, and
    # positions become visible together only after every snapshot invariant has
    # passed, so a late malformed position cannot leave a half-synced account.
    original_account = account
    account = copy.deepcopy(account)

    # A broker import may repair aggregate positions, but it must never build
    # on malformed order/fill history.  Validate that durable causal chain
    # before interpreting any new fill against it.
    _validate_order_state(account, sequence_was_explicit=False)
    _validate_strategy_risk_state(account)

    ledger = {order.order_id: order for order in account.order_ledger}
    for pending in account.pending_orders:
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
    known_fills = {fill.fill_id: fill for fill in account.fills if fill.fill_id}
    ordered_fills = _ordered_broker_fills(
        raw_fills,
        as_of=as_of,
        account=account,
        known_fills=known_fills,
    )
    economic_tranches = {
        symbol: copy.deepcopy(position.tranches) for symbol, position in account.positions.items()
    }
    imported_buy_lifecycle: dict[str, str] = {}
    imported = 0
    completed_order_ids: set[str] = set()
    for raw in ordered_fills:
        fill_id = str(raw.get("fill_id", "")).strip()
        if not fill_id:
            raise ValueError("each broker fill requires a stable fill_id")
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
        shares = _broker_integer(raw, "shares", default=0, positive=True)
        raw_price = raw.get("price", 0.0)
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            raise ValueError("broker fill price must be a finite number")
        price = float(raw_price)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("broker fill shares and price must be positive")
        fill_date_value = raw.get("fill_date", as_of)
        parsed_fill_date = _broker_date(fill_date_value, field="fill_date")
        fill_date = str(fill_date_value)
        signal_date = _broker_date(order.signal_date, field="order signal_date")
        submitted_date = _broker_date(order.submitted_date, field="order submitted_date")
        if parsed_fill_date <= max(signal_date, submitted_date):
            raise ValueError("broker fill date must be after order signal/submission")
        if parsed_fill_date > snapshot_date:
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
        existing_fill = known_fills.get(fill_id)
        if existing_fill is not None:
            identity_matches = (
                existing_fill.order_id == order_id
                and existing_fill.fill_date == fill_date
                and existing_fill.symbol == symbol
                and existing_fill.side == side
                and existing_fill.shares == shares
                and math.isclose(existing_fill.price, price, rel_tol=1e-12, abs_tol=1e-12)
                and math.isclose(existing_fill.gross_value, gross, rel_tol=1e-12, abs_tol=0.01)
                and math.isclose(existing_fill.commission, commission, abs_tol=1e-12)
                and math.isclose(existing_fill.stamp_duty, stamp_duty, abs_tol=1e-12)
                and math.isclose(existing_fill.transfer_fee, transfer_fee, abs_tol=1e-12)
                and math.isclose(existing_fill.slippage_cost, slippage_cost, abs_tol=1e-12)
            )
            if not identity_matches:
                raise ValueError("broker fill_id was reused with different economics")
            continue
        if order.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        }:
            raise ValueError("broker cannot append a fill to a terminal account order")
        cumulative_filled = order.filled_shares + shares
        reported_request = cumulative_filled + remaining
        if order.requested_shares > 0 and reported_request != order.requested_shares:
            raise ValueError("broker fill exceeds or contradicts requested order shares")
        if final and order.requested_shares > 0 and cumulative_filled != order.requested_shares:
            raise ValueError("final broker fill does not complete requested order shares")
        sold_tranches: list[dict[str, Any]] = []
        if side == Side.SELL.value:
            sold_tranches = _allocate_broker_sale(
                economic_tranches.setdefault(symbol, []),
                shares=shares,
                fill_date=fill_date,
                policy=order.reduction_policy,
            )
            attributed_shares = sum(int(item["shares"]) for item in sold_tranches)
            if attributed_shares != shares:
                missing_shares = shares - attributed_shares
                existing = account.positions.get(symbol)
                fallback_cost = (
                    existing.avg_cost
                    if existing is not None and math.isfinite(existing.avg_cost) and existing.avg_cost > 0
                    else price
                )
                fallback_entry_date = (
                    existing.entry_date if existing is not None and existing.entry_date else fill_date
                )
                sold_tranches.append(
                    {
                        "tranche_id": f"broker-degraded-sale:{fill_id}",
                        "lifecycle": (existing.lifecycle if existing is not None else order.lifecycle),
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
                    }
                )
                account.reconciliation_events.append(
                    {
                        "date": as_of,
                        "symbol": symbol,
                        "event": "sell_lot_attribution_incomplete",
                        "broker_shares": shares,
                        "attributed_shares": attributed_shares,
                        "degraded_shares": missing_shares,
                    }
                )
            _allocate_sell_costs(
                sold_tranches,
                commission=commission,
                stamp_duty=stamp_duty,
                transfer_fee=transfer_fee,
                slippage_cost=slippage_cost,
            )
        else:
            full_unit_cost = (gross + commission + transfer_fee) / shares
            economic_tranches.setdefault(symbol, []).append(
                Tranche(
                    tranche_id=f"broker-fill:{fill_id}",
                    lifecycle=order.lifecycle,
                    shares=shares,
                    avg_cost=full_unit_cost,
                    entry_date=fill_date,
                    sellable_date=str(date_type.fromisoformat(fill_date) + timedelta(days=1)),
                    highest_close=price,
                    lowest_close=price,
                    entry_score=order.entry_score,
                    entry_confidence=order.entry_confidence,
                    entry_regime=order.entry_regime,
                    entry_industry_strength=order.entry_industry_strength,
                )
            )
            imported_buy_lifecycle[symbol] = order.lifecycle
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
                reduction_policy=order.reduction_policy,
                reason_code=order.reason_code,
                exit_kind=order.exit_kind,
                sold_tranches=sold_tranches,
            )
        )
        known_fills[fill_id] = account.fills[-1]
        imported += 1
        order.filled_shares = cumulative_filled
        if order.requested_shares == 0:
            order.requested_shares = reported_request
        order.remaining_shares = remaining
        order.status = (
            OrderStatus.FILLED.value if final and remaining == 0 else OrderStatus.PARTIALLY_FILLED.value
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
        existing = account.positions.get(symbol)
        tranches = economic_tranches.get(symbol, [])
        economic_shares = sum(item.shares for item in tranches)
        lifecycle = imported_buy_lifecycle.get(
            symbol,
            existing.lifecycle if existing is not None else Lifecycle.CORE.value,
        )
        strategy_highest = (
            existing.highest_close
            if existing is not None
            else max((item.highest_close for item in tranches), default=avg_cost)
        )
        if economic_shares > shares:
            _allocate_broker_sale(
                tranches,
                shares=economic_shares - shares,
                fill_date="9999-12-31",
                policy=ReductionPolicy.FIFO.value,
            )
            account.reconciliation_events.append(
                {
                    "date": as_of,
                    "symbol": symbol,
                    "event": "broker_share_deficit_reconciled",
                    "shares": economic_shares - shares,
                }
            )
        elif economic_shares < shares:
            residual = shares - economic_shares
            tranches.append(
                Tranche(
                    tranche_id=(f"broker-unmatched:{as_of}:{symbol}:{economic_shares}-{shares}"),
                    lifecycle=Lifecycle.CORE.value,
                    shares=residual,
                    avg_cost=avg_cost,
                    entry_date=as_of,
                    sellable_date=as_of,
                    highest_close=avg_cost,
                    lowest_close=avg_cost,
                )
            )
            account.reconciliation_events.append(
                {
                    "date": as_of,
                    "symbol": symbol,
                    "event": "economic_lot_degraded",
                    "unmatched_shares": residual,
                    "reason": "broker snapshot exceeded known lot inventory",
                    "quality": "degraded_external_inventory",
                    "default_lifecycle": Lifecycle.CORE.value,
                    "default_entry_date": as_of,
                    "default_highest_close": avg_cost,
                }
            )
        tranches = _align_sellability(
            tranches,
            sellable=sellable,
            as_of=as_of,
            next_date=str(snapshot_date + timedelta(days=1)),
        )
        if sum(item.shares for item in tranches) != shares:
            raise RuntimeError("broker reconciliation lost tranche shares")
        derived_entry_date = min(
            (item.entry_date for item in tranches if item.entry_date),
            default=as_of,
        )
        derived_highest = max(
            [strategy_highest, *(item.highest_close for item in tranches)],
        )
        reconciled_positions[symbol] = Position(
            symbol=symbol,
            shares=shares,
            avg_cost=avg_cost,
            entry_date=derived_entry_date,
            highest_close=derived_highest,
            lifecycle=lifecycle,
            tranches=tranches,
        )
    if len(reconciled_positions) > cfg.max_positions:
        raise ValueError("broker snapshot exceeds the production position cap")

    # Exit bands are sell-only ownership of shares that still exist.  When an
    # authoritative snapshot reports zero shares, settle that member in the
    # same atomic reconciliation so stale bands cannot later resurrect it as
    # a restoration BUY.  A strategic member without an exit band may have
    # been temporarily risk-liquidated and therefore keeps its restore right.
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
            and order.order_id not in completed_order_ids
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
        # A broker-authoritative tactical zero with no durable BUY or restore
        # owner is a completed exit.  Leaving ``tactical_active`` set would
        # block every future tactical admission because no position remains to
        # advance or retire that lifecycle.
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promotable"] = 0
        account.candidate_tenure["tactical_cooldown"] = max(
            account.candidate_tenure.get("tactical_cooldown", 0),
            cfg.tactical_rebound_cooldown_days,
        )
        account.tactical_anchor_symbol = ""

    account.cash = cash
    account.positions = reconciled_positions
    account.pending_orders = [
        order for order in account.pending_orders if order.order_id not in completed_order_ids
    ]
    pending_by_id = {order.order_id: order for order in account.pending_orders if order.order_id}
    for order_id, pending in pending_by_id.items():
        entry = ledger.get(order_id)
        if entry is not None:
            pending.remaining_shares = entry.remaining_shares
    account.broker_as_of = as_of
    _validate_position_state(account)
    _validate_order_state(account, sequence_was_explicit=False)
    _validate_strategy_risk_state(account)
    for state_field in fields(AccountState):
        setattr(
            original_account,
            state_field.name,
            getattr(account, state_field.name),
        )
    return {
        "as_of": as_of,
        "fills_imported": imported,
        "positions_reconciled": len(reconciled_positions),
        "pending_orders": len(account.pending_orders),
    }
