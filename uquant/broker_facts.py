"""Broker snapshot identity, external trade facts and external cash flows."""

from __future__ import annotations

import math
from datetime import date as date_type
from typing import Any

from .broker_contract import broker_date, broker_identity, broker_integer
from .contracts.strict_json import canonical_json_bytes, canonical_json_sha256
from .contracts.universe import decision_ai_universe
from .data import normalize_symbol
from .types import (
    AccountOrder,
    AccountState,
    AttributionIdentity,
    AttributionMechanism,
    Lifecycle,
    OrderStatus,
    OriginSubsystem,
    ReductionPolicy,
    Side,
    derive_attribution_event_id,
)

EXTERNAL_TRADE_SOURCES = frozenset({"MANUAL", "OTHER_SYSTEM"})
EXTERNAL_FILL_PREFIX = "external:"


def non_strategy_identity(
    *,
    symbol: str,
    signal_date: str,
    lifecycle: str,
    token: str,
    origin: OriginSubsystem = OriginSubsystem.BROKER_RECONCILIATION,
    mechanism: AttributionMechanism = AttributionMechanism.BROKER_RECONCILIATION,
    exit_kind: str = "broker_reconciliation",
) -> AttributionIdentity:
    """Create explicit identity for inventory not backed by a strategy order."""

    industry = decision_ai_universe().industry_of(symbol, signal_date)
    if industry == "unknown":
        industry = "legacy_unmapped"
        manifest = "0" * 64
    else:
        manifest = decision_ai_universe().sha256
    reason_code = f"{exit_kind}:{token}"
    return {
        "event_id": derive_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            origin_lifecycle=lifecycle,
            origin_subsystem=origin.value,
            mechanism=mechanism.value,
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
            reduction_policy=ReductionPolicy.FIFO.value,
            reason_code=reason_code,
            exit_kind=exit_kind,
        ),
        "origin_subsystem": origin.value,
        "mechanism": mechanism.value,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": manifest,
        "grant_id": "",
        "epoch_id": "",
    }


def register_broker_snapshot(account: AccountState, payload: dict[str, Any], *, as_of: str) -> bool:
    """Bind the broker account and order snapshots; return True for an exact replay.

    Snapshots carrying the same identity must carry the same content.  A
    different same-date snapshot must prove it is newer with a larger
    ``sequence``; a date-only export cannot silently replace another one.
    """

    if payload.get("complete", True) is not True:
        raise ValueError("broker snapshot must be a complete cash/positions snapshot")
    content_sha256 = canonical_json_sha256(
        {
            key: sorted(value, key=canonical_json_bytes) if isinstance(value, list) else value
            for key, value in payload.items()
        }
    )
    snapshot_id = payload.get("snapshot_id", "sha256:" + content_sha256)
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("broker snapshot_id must be a nonempty string")
    sequence = broker_integer(payload, "sequence") if "sequence" in payload else None
    broker_account = payload.get("broker_account")
    if broker_account is not None and (not isinstance(broker_account, str) or not broker_account.strip()):
        raise ValueError("broker_account must be a nonempty string")
    if account.broker_binding:
        if broker_account is None:
            raise ValueError("account is bound to a broker account; snapshot must declare broker_account")
        if broker_account != account.broker_binding:
            raise ValueError("broker snapshot belongs to a different broker account")
    for record in account.broker_snapshots:
        if record["snapshot_id"] == snapshot_id:
            if record["sha256"] != content_sha256:
                raise ValueError(f"broker snapshot_id {snapshot_id!r} was reused with different content")
            return True
    same_date = [record for record in account.broker_snapshots if record["as_of"] == as_of]
    if same_date:
        latest = max(record["sequence"] or 0 for record in same_date)
        if sequence is None or sequence <= latest:
            raise ValueError(
                "broker snapshot conflicts with another snapshot for the same as_of; "
                "a newer same-date snapshot requires a larger sequence"
            )
    if broker_account is not None and not account.broker_binding:
        account.broker_binding = broker_account
    account.broker_snapshots.append(
        {
            "snapshot_id": snapshot_id,
            "as_of": as_of,
            "sequence": sequence,
            "sha256": content_sha256,
            "source": str(payload.get("source", "")),
        }
    )
    return False


def _external_trade_order(
    raw: dict[str, Any], *, as_of: str, account: AccountState, order_id: str
) -> tuple[AccountOrder, dict[str, Any]]:
    trade_id = broker_identity(raw, "trade_id")
    source = str(raw.get("source", ""))
    if source not in EXTERNAL_TRADE_SOURCES:
        raise ValueError(f"external trade source must be one of {sorted(EXTERNAL_TRADE_SOURCES)}")
    side = str(raw.get("side", "")).upper()
    if side not in {Side.BUY.value, Side.SELL.value}:
        raise ValueError("external trade has invalid side")
    symbol = normalize_symbol(str(raw.get("symbol", "")))
    shares = broker_integer(raw, "shares", positive=True)
    trade_date = broker_date(raw.get("trade_date"), field="external trade_date").isoformat()
    if date_type.fromisoformat(trade_date) > date_type.fromisoformat(as_of):
        raise ValueError("external trade date is after snapshot as_of")
    existing = account.positions.get(symbol)
    lifecycle = existing.lifecycle if existing is not None else Lifecycle.CORE.value
    identity = non_strategy_identity(
        symbol=symbol,
        signal_date=trade_date,
        lifecycle=lifecycle,
        token=trade_id,
        origin=OriginSubsystem.EXTERNAL_TRADE,
        mechanism=AttributionMechanism.EXTERNAL_TRADE,
        exit_kind="external_trade",
    )
    order = AccountOrder(
        order_id=order_id,
        signal_date=trade_date,
        submitted_date=trade_date,
        symbol=symbol,
        side=side,
        target_weight=0.0,
        reason=f"external_trade:{source}",
        lifecycle=lifecycle,
        status=OrderStatus.SUBMITTED.value,
        requested_shares=shares,
        remaining_shares=shares,
        last_update_date=trade_date,
        last_event="EXTERNAL_TRADE",
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code=f"external_trade:{trade_id}",
        exit_kind="external_trade",
        **identity,
    )
    fill = {
        key: raw[key]
        for key in ("price", "commission", "stamp_duty", "transfer_fee", "execution_sequence")
        if key in raw
    }
    fill.update(
        fill_id=EXTERNAL_FILL_PREFIX + trade_id,
        order_id=order_id,
        symbol=symbol,
        side=side,
        shares=shares,
        fill_date=trade_date,
        final=True,
        remaining_shares=0,
    )
    return order, fill


def external_trade_fills(account: AccountState, payload: dict[str, Any], *, as_of: str) -> list[dict[str, Any]]:
    """Register declared manual/other-system trades as filled external orders.

    External orders never enter ``pending_orders`` and carry a registered
    non-strategy attribution, so they cannot acquire strategy BUY authority.
    """

    raw_trades = payload.get("external_trades", [])
    if not isinstance(raw_trades, list):
        raise ValueError("broker external_trades must be an array")
    by_reason = {order.reason_code: order for order in account.order_ledger if is_external_order(order)}
    fills: list[dict[str, Any]] = []
    for raw in raw_trades:
        if not isinstance(raw, dict):
            raise ValueError("each external trade must be an object")
        reason_code = f"external_trade:{broker_identity(raw, 'trade_id')}"
        known = by_reason.get(reason_code)
        order_id = known.order_id if known is not None else f"O{account.next_order_sequence:09d}"
        order, fill = _external_trade_order(raw, as_of=as_of, account=account, order_id=order_id)
        if known is None:
            account.order_ledger.append(order)
            account.next_order_sequence += 1
            by_reason[reason_code] = order
        elif (known.symbol, known.side, known.signal_date, known.requested_shares) != (
            order.symbol, order.side, order.signal_date, order.requested_shares
        ):
            raise ValueError(f"external trade_id {raw['trade_id']!r} was reused with different economics")
        fills.append(fill)
    return fills


def is_external_order(order: AccountOrder) -> bool:
    return order.origin_subsystem == OriginSubsystem.EXTERNAL_TRADE.value


def apply_external_cash_flows(account: AccountState, payload: dict[str, Any], *, as_of: str) -> int:
    """Record deposits/withdrawals and shift high-water marks by the same amount.

    The broker snapshot already owns the cash balance; a flow record explains
    the change so that deposits never read as returns and withdrawals never
    read as drawdown.
    """

    raw_flows = payload.get("cash_flows", [])
    if not isinstance(raw_flows, list):
        raise ValueError("broker cash_flows must be an array")
    known = {item["flow_id"]: item for item in account.external_cash_flows}
    applied = 0
    for raw in raw_flows:
        if not isinstance(raw, dict):
            raise ValueError("each cash flow must be an object")
        flow_id = broker_identity(raw, "flow_id")
        amount = raw.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount == 0:
            raise ValueError("cash flow amount must be a finite nonzero number (deposit > 0)")
        flow_date = broker_date(raw.get("flow_date"), field="cash flow_date").isoformat()
        if date_type.fromisoformat(flow_date) > date_type.fromisoformat(as_of):
            raise ValueError("cash flow date is after snapshot as_of")
        value = float(amount)
        record: dict[str, Any] = {
            "flow_id": flow_id,
            "amount": value,
            "flow_date": flow_date,
            "source": str(raw.get("source", "")),
            "reason": str(raw.get("reason", "")),
        }
        if flow_id in known:
            if known[flow_id] != record:
                raise ValueError(f"cash flow_id {flow_id!r} was reused with different content")
            continue
        if min(account.operating_peak, account.capital_peak, account.deployed_peak) + value <= 0:
            raise ValueError("cash withdrawal exceeds the recorded equity high-water marks")
        account.operating_peak += value
        account.capital_peak += value
        account.deployed_peak += value
        account.external_cash_flows.append(record)
        known[flow_id] = record
        applied += 1
    return applied
