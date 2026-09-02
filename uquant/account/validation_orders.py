"""Order, fill, and lot-origin validation for durable accounts."""

from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from typing import Any

from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    Fill,
    Lifecycle,
    Opportunity,
    OrderStatus,
    PendingOrder,
    ReductionPolicy,
    Side,
    order_intent_metadata,
)
from .validation_attribution import (
    validate_attribution_identity as _validate_attribution_identity,
)
from .validation_attribution import validate_order_intent as _validate_order_intent
from .validation_common import (
    ORDER_ID_PATTERN as _ORDER_ID,
)
from .validation_common import (
    finite_number as _finite_number,
)
from .validation_common import (
    nonnegative_integer as _nonnegative_integer,
)
from .validation_common import (
    required_iso_date as _required_iso_date,
)
from .validation_common import (
    required_text as _required_text,
)
from .validation_common import (
    unlinked_fill_matches_order as _unlinked_fill_matches_order,
)


def validate_pending_order_for_account_write(
    state: AccountState,
    order: PendingOrder,
) -> None:
    """Validate one current-schema order before execution mutates the ledger."""

    if state.schema_version != ACCOUNT_SCHEMA_VERSION:
        raise RuntimeError("pending order registration requires the current account schema")
    _validate_order_intent(
        order,
        label="pending order",
        validate_attribution=True,
    )


def _validate_fill_attribution_reconciliation(
    *,
    allocated_fee_totals: Any,
    allocations_with_fee_detail: Any,
    attributed_shares: Any,
    fill: Any,
    shares: Any,
) -> None:
    if allocations_with_fee_detail:
        if allocations_with_fee_detail != len(fill.sold_tranches):
            raise RuntimeError("fill sold-lot fee detail must cover every allocation")
        for name, allocated in allocated_fee_totals.items():
            if not math.isclose(
                allocated,
                float(getattr(fill, name)),
                rel_tol=1e-12,
                abs_tol=1e-8,
            ):
                raise RuntimeError(f"fill sold-lot {name} does not reconcile to fill")
    if (
        fill.side == Side.SELL.value
        and fill.order_id
        and attributed_shares != shares
    ):
        raise RuntimeError("linked sell fill sold-lot attribution does not reconcile")
    if fill.side == Side.SELL.value and not fill.order_id and attributed_shares not in {0, shares}:
        raise RuntimeError("unlinked sell fill sold-lot attribution does not reconcile")


def _resolve_fill_order(
    *,
    fill: Any,
    ledger: Any,
) -> Any:
    _required_text(fill.reason_code, field="fill reason_code")
    _required_text(fill.exit_kind, field="fill exit_kind")
    if not isinstance(fill.order_id, str) or not isinstance(fill.fill_id, str):
        raise RuntimeError("fill identifiers must be text")
    if fill.order_id:
        _order_sequence(fill.order_id)
    if fill.fill_id and not fill.fill_id.strip():
        raise RuntimeError("fill_id cannot contain only whitespace")
    _validate_attribution_identity(fill, label="fill")

    order = ledger.get(fill.order_id) if fill.order_id else None
    if fill.order_id and order is None:
        raise RuntimeError("fill references an unknown account order")
    return order


def _validate_fill_quantity_and_value(
    *,
    fill: Any,
) -> Any:
    shares = _nonnegative_integer(fill.shares, field="fill shares", positive=True)
    price = _finite_number(fill.price, field="fill price", minimum=0.0)
    if price == 0.0:
        raise RuntimeError("fill price must be positive")
    gross = _finite_number(fill.gross_value, field="fill gross_value", minimum=0.0)
    expected_gross = shares * price
    if not math.isclose(gross, expected_gross, rel_tol=1e-12, abs_tol=0.01):
        raise RuntimeError("fill gross_value does not reconcile to shares * price")
    return shares


def _validate_sold_lot_cost_fields(allocation: dict[str, Any]) -> int:
    allocated_shares = _nonnegative_integer(
        allocation.get("shares"),
        field="fill sold-lot shares",
        positive=True,
    )
    _required_text(allocation.get("tranche_id"), field="fill sold-lot tranche_id")
    lifecycle = allocation.get("lifecycle")
    if not isinstance(lifecycle, str) or lifecycle not in {item.value for item in Lifecycle}:
        raise RuntimeError("fill sold-lot attribution has invalid lifecycle")
    if "entry_date" in allocation:
        _required_iso_date(allocation["entry_date"], field="fill sold-lot entry_date")
    numeric_fields = (
        "cost",
        "unit_cost",
        "avg_cost",
        "cost_basis",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "slippage_cost",
        "fees",
        "transaction_costs",
    )
    for name in numeric_fields:
        if name in allocation:
            _finite_number(
                allocation[name],
                field=f"fill sold-lot {name}",
                minimum=0.0,
            )
    unit_cost_aliases = {
        name: _finite_number(
            allocation[name],
            field=f"fill sold-lot {name}",
            minimum=0.0,
        )
        for name in ("cost", "unit_cost", "avg_cost")
        if name in allocation
    }
    if unit_cost_aliases:
        reference_cost = next(iter(unit_cost_aliases.values()))
        if any(
            not math.isclose(value, reference_cost, rel_tol=1e-12, abs_tol=1e-8)
            for value in unit_cost_aliases.values()
        ):
            raise RuntimeError("fill sold-lot unit-cost aliases differ")
        if "cost_basis" in allocation:
            cost_basis = _finite_number(
                allocation["cost_basis"],
                field="fill sold-lot cost_basis",
                minimum=0.0,
            )
            expected_basis = int(allocation["shares"]) * reference_cost
            if not math.isclose(
                cost_basis,
                expected_basis,
                rel_tol=1e-12,
                abs_tol=0.01,
            ):
                raise RuntimeError("fill sold-lot cost_basis does not reconcile to shares * unit_cost")
    elif "cost_basis" in allocation:
        raise RuntimeError("fill sold-lot cost_basis requires a unit-cost alias")
    return allocated_shares


def _validate_sold_lot_attribution_and_fees(
    *,
    allocated_fee_totals: dict[str, float],
    allocation: dict[str, Any],
) -> bool:
    if "mae" in allocation:
        _finite_number(
            allocation["mae"],
            field="fill sold-lot mae",
            maximum=0.0,
        )
    if "entry_score" in allocation:
        _finite_number(
            allocation["entry_score"],
            field="fill sold-lot entry_score",
        )
    if "entry_confidence" in allocation:
        _finite_number(
            allocation["entry_confidence"],
            field="fill sold-lot entry_confidence",
            minimum=0.0,
            maximum=1.0,
        )
    if "entry_regime" in allocation:
        entry_regime = allocation["entry_regime"]
        if not isinstance(entry_regime, str) or entry_regime not in {item.value for item in Opportunity}:
            raise RuntimeError("fill sold-lot attribution has invalid entry_regime")
    if "entry_industry_strength" in allocation:
        _finite_number(
            allocation["entry_industry_strength"],
            field="fill sold-lot entry_industry_strength",
        )
    allocation_identity = SimpleNamespace(**allocation)
    _validate_attribution_identity(
        allocation_identity,
        label="fill sold-lot attribution",
    )

    fee_components = tuple(allocated_fee_totals)
    has_fee_detail = any(name in allocation for name in (*fee_components, "fees", "transaction_costs"))
    if has_fee_detail:
        if any(name not in allocation for name in fee_components):
            raise RuntimeError("fill sold-lot fee detail is incomplete")
        component_values = {
            name: _finite_number(
                allocation[name],
                field=f"fill sold-lot {name}",
                minimum=0.0,
            )
            for name in fee_components
        }
        for name, value in component_values.items():
            allocated_fee_totals[name] += value
        if "fees" in allocation:
            expected_fees = sum(
                component_values[name] for name in ("commission", "stamp_duty", "transfer_fee")
            )
            if not math.isclose(
                float(allocation["fees"]),
                expected_fees,
                rel_tol=1e-12,
                abs_tol=1e-8,
            ):
                raise RuntimeError("fill sold-lot fees alias does not reconcile")
        if "transaction_costs" in allocation:
            expected_costs = sum(component_values.values())
            if not math.isclose(
                float(allocation["transaction_costs"]),
                expected_costs,
                rel_tol=1e-12,
                abs_tol=1e-8,
            ):
                raise RuntimeError("fill sold-lot transaction_costs alias does not reconcile")
    return has_fee_detail


def _validate_sold_lot_evidence_and_fees(
    allocation: dict[str, Any],
    *,
    allocated_fee_totals: dict[str, float],
) -> bool:
    if "mfe" in allocation:
        _finite_number(
            allocation["mfe"],
            field="fill sold-lot mfe",
            minimum=0.0,
        )
    has_fee_detail = _validate_sold_lot_attribution_and_fees(
        allocated_fee_totals=allocated_fee_totals,
        allocation=allocation,
    )
    return has_fee_detail


def _validate_fill_order_and_costs(
    *,
    fill: Any,
    fill_date: Any,
    order: Any,
) -> tuple[Any, Any, Any]:
    if order is not None:
        fields_that_must_match = (
            "signal_date",
            "symbol",
            "side",
            "lifecycle",
            "reduction_policy",
            "exit_kind",
            "event_id",
            "origin_subsystem",
            "mechanism",
            "origin_lifecycle",
            "replaces_symbol",
            "industry_at_entry",
            "industry_manifest_sha256",
            "grant_id",
            "epoch_id",
        )
        changed = [name for name in fields_that_must_match if getattr(fill, name) != getattr(order, name)]
        if changed:
            raise RuntimeError("fill metadata differs from account order: " + ", ".join(changed))
        submitted_date = _required_iso_date(
            order.submitted_date,
            field="account order submitted_date",
        )
        if fill_date <= submitted_date:
            raise RuntimeError("fill date must be after its order submission date")

    if not isinstance(fill.sold_tranches, list):
        raise RuntimeError("fill sold-lot attribution must be an array")
    attributed_shares = 0
    allocated_fee_totals = {
        name: 0.0 for name in ("commission", "stamp_duty", "transfer_fee", "slippage_cost")
    }
    allocations_with_fee_detail = 0
    for allocation in fill.sold_tranches:
        if not isinstance(allocation, dict):
            raise RuntimeError("fill sold-lot attribution must contain objects")
        attributed_shares += _validate_sold_lot_cost_fields(allocation)
        if _validate_sold_lot_evidence_and_fees(
            allocation,
            allocated_fee_totals=allocated_fee_totals,
        ):
            allocations_with_fee_detail += 1
    return allocated_fee_totals, allocations_with_fee_detail, attributed_shares


def _validate_fill(
    fill: Fill,
    *,
    ledger: dict[str, AccountOrder],
) -> None:
    """Validate one fill and reconcile its immutable order attribution."""

    signal_date = _required_iso_date(fill.signal_date, field="fill signal_date")
    fill_date = _required_iso_date(fill.fill_date, field="fill fill_date")
    if fill_date <= signal_date:
        raise RuntimeError("fill date must be after its signal date")
    _required_text(fill.symbol, field="fill symbol")
    if not isinstance(fill.side, str) or fill.side not in {item.value for item in Side}:
        raise RuntimeError("fill has invalid side")
    if not isinstance(fill.lifecycle, str) or fill.lifecycle not in {item.value for item in Lifecycle}:
        raise RuntimeError("fill has invalid lifecycle")
    shares = _validate_fill_quantity_and_value(
        fill=fill,
    )
    for name in ("commission", "stamp_duty", "transfer_fee", "slippage_cost"):
        _finite_number(getattr(fill, name), field=f"fill {name}", minimum=0.0)
    _required_text(fill.reason, field="fill reason")
    if not isinstance(fill.reduction_policy, str) or fill.reduction_policy not in {
        item.value for item in ReductionPolicy
    }:
        raise RuntimeError("fill has invalid reduction policy")
    order = _resolve_fill_order(
        fill=fill,
        ledger=ledger,
    )
    allocated_fee_totals, allocations_with_fee_detail, attributed_shares = _validate_fill_order_and_costs(
        fill=fill,
        fill_date=fill_date,
        order=order,
    )
    _validate_fill_attribution_reconciliation(
        allocated_fee_totals=allocated_fee_totals,
        allocations_with_fee_detail=allocations_with_fee_detail,
        attributed_shares=attributed_shares,
        fill=fill,
        shares=shares,
    )
    if fill.side == Side.BUY.value and attributed_shares:
        raise RuntimeError("buy fill cannot contain sold-lot attribution")


def _order_sequence(order_id: str) -> int:
    if not isinstance(order_id, str) or _ORDER_ID.fullmatch(order_id) is None:
        raise RuntimeError(f"account state has invalid order id: {order_id!r}")
    sequence = int(order_id[1:])
    if sequence <= 0:
        raise RuntimeError(f"account state has invalid order id: {order_id!r}")
    return sequence


def _validate_pending_order_state(
    *,
    reduction_policies: Any,
    state: Any,
) -> None:
    for pending_item in state.pending_orders:
        _validate_order_intent(
            pending_item,
            label="pending order",
            validate_attribution=True,
        )
        _nonnegative_integer(
            pending_item.remaining_shares,
            field="pending order remaining_shares",
        )
        _nonnegative_integer(pending_item.attempts, field="pending order attempts")
        if not isinstance(pending_item.order_id, str):
            raise RuntimeError("pending order id must be text")
        if pending_item.order_id:
            _order_sequence(pending_item.order_id)
        if pending_item.reduction_policy not in reduction_policies:
            raise RuntimeError("pending order has invalid reduction policy")
        if not pending_item.exit_kind or not pending_item.reason_code:
            raise RuntimeError("pending order has invalid exit attribution")


def _normalize_next_order_sequence(
    *,
    sequence_was_explicit: Any,
    sequences: Any,
    state: Any,
) -> None:
    required_next = max(sequences, default=0) + 1
    if state.next_order_sequence > 999_999_999:
        raise RuntimeError("account state next order sequence exceeds the canonical ID space")
    if sequence_was_explicit:
        if state.next_order_sequence < required_next:
            raise RuntimeError("account state next order sequence would reuse an order id")
        if state.next_order_sequence > required_next:
            raise RuntimeError("account state next order sequence does not exactly follow the durable ledger")
    state.next_order_sequence = max(state.next_order_sequence, required_next)
    if state.next_order_sequence <= 0:
        raise RuntimeError("account state has invalid next order sequence")


def _validate_remainder_release_evidence(
    ledger_item: AccountOrder,
    *,
    submitted_date: date,
    last_update: date | None,
    requested: int,
) -> None:
    release_session = ledger_item.remainder_release_session
    if not isinstance(release_session, str):
        raise RuntimeError("account order remainder release evidence differs")
    release_shares = _nonnegative_integer(
        ledger_item.remainder_release_shares,
        field="account order remainder release shares",
    )
    if bool(release_session) != bool(release_shares):
        raise RuntimeError("account order remainder release evidence differs")
    if not release_session:
        return
    release_date = _required_iso_date(
        release_session,
        field="account order remainder release session",
    )
    if (
        release_date < submitted_date
        or (last_update is not None and release_date > last_update)
        or release_shares >= requested
        or not ledger_item.grant_id
        or ledger_item.cancel_reason != "strategic partial remainder replaced"
    ):
        raise RuntimeError("account order remainder release evidence differs")


def _validate_account_order_ledger(
    state: AccountState,
    *,
    reduction_policies: set[str],
    statuses: set[str],
) -> None:
    for ledger_item in state.order_ledger:
        signal_date = _validate_order_intent(
            ledger_item,
            label="account order",
            validate_attribution=True,
        )
        submitted_date = _required_iso_date(
            ledger_item.submitted_date,
            field="account order submitted_date",
        )
        if submitted_date < signal_date:
            raise RuntimeError("account order submission predates its signal")
        last_update = None
        if ledger_item.last_update_date:
            last_update = _required_iso_date(
                ledger_item.last_update_date,
                field="account order last_update_date",
            )
            if last_update < submitted_date:
                raise RuntimeError("account order update predates its submission")
        if not isinstance(ledger_item.last_event, str) or not ledger_item.last_event.strip():
            raise RuntimeError("account order last_event must be non-empty text")
        if not isinstance(ledger_item.replaced_by, str) or not isinstance(
            ledger_item.cancel_reason,
            str,
        ):
            raise RuntimeError("account order replacement metadata must be text")
        if ledger_item.replaced_by:
            _order_sequence(ledger_item.replaced_by)
        if ledger_item.status not in statuses:
            raise RuntimeError(f"account state has invalid order status: {ledger_item.status!r}")
        requested = _nonnegative_integer(
            ledger_item.requested_shares,
            field="account order requested_shares",
        )
        filled = _nonnegative_integer(
            ledger_item.filled_shares,
            field="account order filled_shares",
        )
        remaining = _nonnegative_integer(
            ledger_item.remaining_shares,
            field="account order remaining_shares",
        )
        _validate_remainder_release_evidence(
            ledger_item,
            submitted_date=submitted_date,
            last_update=last_update,
            requested=requested,
        )
        _nonnegative_integer(ledger_item.attempts, field="account order attempts")
        if filled + remaining != requested:
            raise RuntimeError("account order requested shares do not equal filled plus remaining")
        if ledger_item.status == OrderStatus.PARTIALLY_FILLED.value and (filled == 0 or remaining == 0):
            raise RuntimeError("partially filled order requires filled and remaining shares")
        if ledger_item.status == OrderStatus.FILLED.value and remaining:
            raise RuntimeError("filled order cannot retain remaining shares")
        if ledger_item.reduction_policy not in reduction_policies:
            raise RuntimeError("account state has invalid reduction policy")
        if not ledger_item.exit_kind or not ledger_item.reason_code:
            raise RuntimeError("account state has invalid exit attribution")


def _validate_pending_order_links(
    state: AccountState,
    *,
    ledger: dict[str, AccountOrder],
) -> None:
    pending_ids = [item.order_id for item in state.pending_orders if item.order_id]
    if len(pending_ids) != len(set(pending_ids)):
        raise RuntimeError("account state has duplicate pending order ids")
    for order_id in pending_ids:
        _order_sequence(order_id)
    terminal = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    for order_id in pending_ids:
        pending_account_order = ledger.get(order_id)
        if pending_account_order is None:
            raise RuntimeError("pending order references an unknown account order")
        if pending_account_order.status in terminal:
            raise RuntimeError("pending order references a terminal account order")
        pending = next(item for item in state.pending_orders if item.order_id == order_id)
        pending_metadata = order_intent_metadata(pending)
        ledger_metadata = order_intent_metadata(pending_account_order)
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
                "pending order immutable metadata differs from account order: " + ", ".join(changed)
            )
        if pending.remaining_shares != pending_account_order.remaining_shares:
            raise RuntimeError("pending order remaining shares differ from account order")
        if pending.attempts != pending_account_order.attempts:
            raise RuntimeError("pending order attempts differ from account order")


def _validated_fill_links(
    state: AccountState,
    *,
    ledger: dict[str, AccountOrder],
) -> tuple[dict[str, list[Fill]], list[Fill]]:
    for ledger_item in state.order_ledger:
        if ledger_item.replaced_by and ledger_item.replaced_by not in ledger:
            raise RuntimeError("replaced order references an unknown replacement")
    for fill in state.fills:
        _validate_fill(
            fill,
            ledger=ledger,
        )
    fill_ids = [fill.fill_id for fill in state.fills if fill.fill_id]
    if len(fill_ids) != len(set(fill_ids)):
        raise RuntimeError("account state has duplicate broker fill ids")

    linked_fills: dict[str, list[Fill]] = {order_id: [] for order_id in ledger}
    unlinked_fills = [fill for fill in state.fills if not fill.order_id]
    for fill in unlinked_fills:
        structured_matches = sum(
            _unlinked_fill_matches_order(fill, candidate)
            for candidate in state.order_ledger
        )
        if structured_matches != 1:
            raise RuntimeError("unlinked fill must match exactly one structured account order")
    for fill in state.fills:
        if fill.order_id:
            linked_fills[fill.order_id].append(fill)
    return linked_fills, unlinked_fills


def _unlinked_order_fill_is_exempt(
    order_fills: list[Fill],
    unlinked_matches: list[Fill],
    *,
    ledger_item: AccountOrder,
    state: AccountState,
) -> bool:
    unlinked_match_shares = sum(fill.shares for fill in unlinked_matches)
    return bool(
        not order_fills
        and unlinked_matches
        and unlinked_match_shares == ledger_item.filled_shares
        and all(
            sum(
                _unlinked_fill_matches_order(fill, candidate)
                for candidate in state.order_ledger
            )
            == 1
            for fill in unlinked_matches
        )
    )


def _validate_order_fill_reconciliation(
    state: AccountState,
    *,
    linked_fills: dict[str, list[Fill]],
    unlinked_fills: list[Fill],
) -> None:
    for ledger_item in state.order_ledger:
        order_fills = linked_fills[ledger_item.order_id]
        accounted_fill_shares = sum(fill.shares for fill in order_fills)
        # Some accepted account payloads contain fills without broker-visible
        # order IDs. Only a unique immutable-identity match may link them.
        unlinked_matches = [
            fill
            for fill in unlinked_fills
            if _unlinked_fill_matches_order(fill, ledger_item)
        ]
        unlinked_exempt = _unlinked_order_fill_is_exempt(
            order_fills,
            unlinked_matches,
            ledger_item=ledger_item,
            state=state,
        )
        if accounted_fill_shares != ledger_item.filled_shares and not unlinked_exempt:
            raise RuntimeError("account order filled shares do not reconcile to fills")
        if (
            ledger_item.status
            in {
                OrderStatus.SUBMITTED.value,
                OrderStatus.OPEN.value,
            }
            and ledger_item.filled_shares
        ):
            raise RuntimeError("unfilled order status cannot retain filled shares")
        if ledger_item.status == OrderStatus.FILLED.value and ledger_item.filled_shares == 0:
            raise RuntimeError("filled order requires at least one executed share")
        relevant_fills = order_fills or unlinked_matches
        if relevant_fills:
            if not ledger_item.last_update_date:
                raise RuntimeError("filled account order requires last_update_date")
            last_update = _required_iso_date(
                ledger_item.last_update_date,
                field="account order last_update_date",
            )
            if last_update < max(
                _required_iso_date(fill.fill_date, field="fill fill_date") for fill in relevant_fills
            ):
                raise RuntimeError("account order update predates its latest fill")


def _validate_order_state(
    state: AccountState,
    *,
    sequence_was_explicit: bool,
) -> None:
    """Validate order identifiers, lifecycle transitions, fills, and references."""

    _nonnegative_integer(
        state.next_order_sequence,
        field="account state next order sequence",
        positive=True,
    )
    identifiers = [item.order_id for item in state.order_ledger]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("account state has duplicate order ids")
    sequences = [_order_sequence(order_id) for order_id in identifiers]
    _normalize_next_order_sequence(
        sequence_was_explicit=sequence_was_explicit,
        sequences=sequences,
        state=state,
    )

    statuses = {item.value for item in OrderStatus}
    reduction_policies = {item.value for item in ReductionPolicy}
    ledger = {ledger_item.order_id: ledger_item for ledger_item in state.order_ledger}
    _validate_account_order_ledger(
        state,
        reduction_policies=reduction_policies,
        statuses=statuses,
    )
    _validate_pending_order_links(state, ledger=ledger)
    _validate_pending_order_state(
        reduction_policies=reduction_policies,
        state=state,
    )
    linked_fills, unlinked_fills = _validated_fill_links(
        state,
        ledger=ledger,
    )
    _validate_order_fill_reconciliation(
        state,
        linked_fills=linked_fills,
        unlinked_fills=unlinked_fills,
    )


validate_order_state = _validate_order_state
