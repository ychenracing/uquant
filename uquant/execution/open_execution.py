"""Next-open execution and fill application."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date as date_type
from datetime import timedelta
from typing import Any, cast

import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..models.strategic_epoch import record_account_strategic_epoch_fill
from ..models.strategic_grant import (
    acknowledge_strategic_grant_order,
    record_strategic_grant_fill,
)
from ..portfolio_core import symbol_weight_cap
from ..types import (
    AccountOrder,
    AccountState,
    Fill,
    OrderStatus,
    PendingOrder,
    Position,
    Side,
    Tranche,
)
from .fees import fee_components
from .market_constraints import market_execution_blocked as _blocked
from .reconciliation import active_order_status as _active_order_status
from .reconciliation import register_account_order as _register_account_order
from .tranches import (
    allocate_sell_costs as _allocate_sell_costs,
)
from .tranches import (
    consume_sell_tranches as _consume_sell_tranches,
)
from .tranches import (
    rebuild_position_from_tranches as _rebuild_position_from_tranches,
)


@dataclass(frozen=True, slots=True)
class _OpenOrderRequest:
    order: PendingOrder
    account_order: AccountOrder
    row: pd.Series
    current: Position
    open_price: float
    execution_price: float
    target_requested: int
    shares: int


def _register_open_orders(
    account: AccountState,
    orders: list[PendingOrder],
    *,
    date_str: str,
) -> dict[str, AccountOrder]:
    for order in orders:
        _register_account_order(
            account,
            order,
            submitted_date=order.signal_date or date_str,
        )
    return {item.order_id: item for item in account.order_ledger}


def _eligible_open_row(
    *,
    date: pd.Timestamp,
    date_str: str,
    order: PendingOrder,
    account: AccountState,
    account_order: AccountOrder,
    panel: dict[str, pd.DataFrame],
    retained: list[PendingOrder],
) -> pd.Series | None:
    if (
        order.side == Side.BUY.value
        and account_order.cancel_reason == "sentinel_freeze_new_risk"
        and account_order.status
        not in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        }
    ):
        account_order.last_update_date = date_str
        account_order.last_event = "CANCEL_REQUESTED"
        retained.append(order)
        return None
    if (
        order.side == Side.BUY.value
        and account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
        and any(item.side == Side.SELL.value for item in retained)
    ):
        # A recovery-owner handoff is explicitly sell-funded.  A
        # blocked incumbent sale must hold every replacement BUY.
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "AWAITING_HANDOFF_SELL"
        retained.append(order)
        return None
    if pd.Timestamp(order.signal_date) >= date:
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "WAITING_NEXT_OPEN"
        retained.append(order)
        return None
    acknowledge_strategic_grant_order(
        account.strategic_grant,
        grant_id=order.grant_id,
        order_id=order.order_id,
    )
    frame = panel.get(order.symbol)
    if frame is None or date not in frame.index:
        order.attempts += 1
        account_order.attempts = order.attempts
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "MISSING_OR_SUSPENDED"
        retained.append(order)
        return None
    row = cast(pd.Series, frame.loc[date])
    history = frame.loc[:date]
    if len(history) < 2:
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "INSUFFICIENT_HISTORY"
        retained.append(order)
        return None
    previous_close = float(history.iloc[-2]["close"])
    if _blocked(order.symbol, order.side, row, previous_close):
        order.attempts += 1
        account_order.attempts = order.attempts
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "LIMIT_BLOCKED"
        retained.append(order)
        return None
    return row


def _size_open_order(
    *,
    cfg: SystemConfig,
    date: pd.Timestamp,
    order: PendingOrder,
    account: AccountState,
    account_order: AccountOrder,
    panel: dict[str, pd.DataFrame],
    row: pd.Series,
    retained: list[PendingOrder],
) -> _OpenOrderRequest | None:
    date_str = str(date.date())
    open_price = float(row["open"])
    execution_price = open_price * (
        1.0 + cfg.slippage if order.side == Side.BUY.value else 1.0 - cfg.slippage
    )
    open_equity = account.cash + sum(
        float(position.shares)
        * (
            scalar(panel[symbol].loc[date], "open", position.avg_cost)
            if symbol in panel and date in panel[symbol].index
            else position.avg_cost
        )
        for symbol, position in account.positions.items()
    )
    desired_shares = int(math.floor(order.target_weight * open_equity / execution_price / 100.0) * 100)
    if order.symbol.startswith("sh688") and desired_shares > 0:
        desired_shares = max(200, desired_shares)
    current = account.positions.get(order.symbol, Position(symbol=order.symbol))
    requested = desired_shares - current.shares
    if order.side == Side.SELL.value:
        target_requested = max(0, -requested)
        if order.target_weight == 0:
            target_requested = current.shares
        requested = min(
            target_requested,
            current.sellable_shares(date_str),
        )
    else:
        requested = max(0, requested)
        target_requested = requested
    volume_shares = float(row.get("volume", 0.0))
    # Some data sources report hands; amount/close detects that case without future data.
    implied = float(row.get("amount", 0.0)) / max(float(row["close"]), 1e-12)
    if implied > volume_shares * 50:
        volume_shares *= 100.0
    capacity = int(math.floor(volume_shares * cfg.max_volume_participation / 100.0) * 100)
    shares = min(requested, capacity)
    if order.side == Side.BUY.value:
        projected_positions = sum(position.shares > 0 for position in account.positions.values()) + (
            current.shares == 0
        )
        if projected_positions > cfg.max_positions:
            order.attempts += 1
            account_order.attempts = order.attempts
            account_order.status = _active_order_status(account_order)
            account_order.last_update_date = date_str
            account_order.last_event = "POSITION_CAP_BLOCKED"
            retained.append(order)
            return None
        max_by_weight = (
            int(
                math.floor(
                    symbol_weight_cap(cfg, account, order.symbol) * open_equity / execution_price / 100.0
                )
                * 100
            )
            - current.shares
        )
        shares = min(shares, max(0, max_by_weight))
        while shares >= 100:
            gross = shares * execution_price
            commission, _stamp, transfer = fee_components(order.side, gross, cfg)
            if gross + commission + transfer <= account.cash + 1e-8:
                break
            shares -= 100
    if shares <= 0:
        if target_requested > 0:
            order.attempts += 1
            account_order.requested_shares = account_order.filled_shares + target_requested
            account_order.remaining_shares = target_requested
            order.remaining_shares = target_requested
            account_order.attempts = order.attempts
            account_order.status = _active_order_status(account_order)
            account_order.last_update_date = date_str
            account_order.last_event = "CAPACITY_OR_CASH_BLOCKED"
            retained.append(order)
        else:
            account_order.status = OrderStatus.CANCELLED.value
            account_order.cancel_reason = "target already satisfied"
            account_order.last_update_date = date_str
            account_order.last_event = "ZERO_REQUEST"
        return None
    return _OpenOrderRequest(
        order=order,
        account_order=account_order,
        row=row,
        current=current,
        open_price=open_price,
        execution_price=execution_price,
        target_requested=target_requested,
        shares=shares,
    )


def _apply_buy_fill(
    *,
    request: _OpenOrderRequest,
    date_str: str,
    account: AccountState,
    gross: float,
    commission: float,
    transfer: float,
) -> None:
    order = request.order
    current = request.current
    previous_lifecycle = current.lifecycle if current.shares > 0 else "NONE"
    account.cash -= gross + commission + transfer
    old_value = current.shares * current.avg_cost
    current.shares += request.shares
    current.avg_cost = (old_value + gross + commission + transfer) / current.shares
    current.entry_date = current.entry_date or date_str
    current.highest_close = max(current.highest_close, float(request.row["close"]))
    current.lifecycle = order.lifecycle
    if order.grant_id:
        if current.shares - request.shares > 0 and current.grant_id != order.grant_id:
            raise RuntimeError("strategic fill would create a second grant owner for one position")
        current.grant_id = order.grant_id
    if order.epoch_id:
        if current.shares - request.shares > 0 and current.epoch_id != order.epoch_id:
            raise RuntimeError("strategic fill would create a second epoch owner for one position")
        current.epoch_id = order.epoch_id
    if previous_lifecycle != order.lifecycle:
        account.lifecycle_events.append(
            {
                "date": date_str,
                "symbol": order.symbol,
                "from": previous_lifecycle,
                "to": order.lifecycle,
                "shares": request.shares,
                "reason": order.reason,
            }
        )
    sellable_date = str((date_type.fromisoformat(date_str) + timedelta(days=1)).isoformat())
    current.tranches.append(
        Tranche(
            tranche_id=f"{date_str}:{order.symbol}:{len(current.tranches) + 1}",
            lifecycle=order.lifecycle,
            shares=request.shares,
            avg_cost=(gross + commission + transfer) / request.shares,
            entry_date=date_str,
            sellable_date=sellable_date,
            highest_close=float(request.row["close"]),
            lowest_close=float(request.row["close"]),
            entry_score=order.entry_score,
            entry_confidence=order.entry_confidence,
            entry_regime=order.entry_regime,
            entry_industry_strength=order.entry_industry_strength,
            event_id=order.event_id,
            origin_subsystem=order.origin_subsystem,
            mechanism=order.mechanism,
            origin_lifecycle=order.origin_lifecycle,
            replaces_symbol=order.replaces_symbol,
            industry_at_entry=order.industry_at_entry,
            industry_manifest_sha256=order.industry_manifest_sha256,
            grant_id=order.grant_id,
            epoch_id=order.epoch_id,
        )
    )
    account.positions[order.symbol] = current


def _apply_sell_fill(
    *,
    request: _OpenOrderRequest,
    date_str: str,
    account: AccountState,
    gross: float,
    commission: float,
    stamp: float,
    transfer: float,
    slippage_cost: float,
) -> list[dict[str, Any]]:
    account.cash += gross - commission - stamp - transfer
    sold_tranches = _consume_sell_tranches(
        request.current,
        shares=request.shares,
        date=date_str,
        reduction_policy=request.order.reduction_policy,
    )
    _allocate_sell_costs(
        sold_tranches,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        slippage_cost=slippage_cost,
    )
    _rebuild_position_from_tranches(request.current)
    if request.current.shares <= 0:
        account.positions.pop(request.order.symbol, None)
    else:
        account.positions[request.order.symbol] = request.current
    return sold_tranches


def _build_open_fill(
    *,
    cfg: SystemConfig,
    request: _OpenOrderRequest,
    date_str: str,
    account: AccountState,
) -> Fill:
    gross = request.shares * request.execution_price
    commission, stamp, transfer = fee_components(request.order.side, gross, cfg)
    slippage_cost = request.shares * abs(request.execution_price - request.open_price)
    sold_tranches: list[dict[str, Any]] = []
    if request.order.side == Side.BUY.value:
        _apply_buy_fill(
            request=request,
            date_str=date_str,
            account=account,
            gross=gross,
            commission=commission,
            transfer=transfer,
        )
    else:
        sold_tranches = _apply_sell_fill(
            request=request,
            date_str=date_str,
            account=account,
            gross=gross,
            commission=commission,
            stamp=stamp,
            transfer=transfer,
            slippage_cost=slippage_cost,
        )
    order = request.order
    return Fill(
        signal_date=order.signal_date,
        fill_date=date_str,
        symbol=order.symbol,
        side=order.side,
        shares=request.shares,
        price=request.execution_price,
        gross_value=gross,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        slippage_cost=slippage_cost,
        reason=order.reason,
        lifecycle=order.lifecycle,
        order_id=request.account_order.order_id,
        reduction_policy=order.reduction_policy,
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        sold_tranches=sold_tranches,
        event_id=order.event_id,
        origin_subsystem=order.origin_subsystem,
        mechanism=order.mechanism,
        origin_lifecycle=order.origin_lifecycle,
        replaces_symbol=order.replaces_symbol,
        industry_at_entry=order.industry_at_entry,
        industry_manifest_sha256=order.industry_manifest_sha256,
        grant_id=order.grant_id,
        epoch_id=order.epoch_id,
    )


def _record_open_fill(
    *,
    fill: Fill,
    request: _OpenOrderRequest,
    date_str: str,
    account: AccountState,
    retained: list[PendingOrder],
    fills: list[Fill],
) -> None:
    account.fills.append(fill)
    fills.append(fill)
    account_order = request.account_order
    account_order.requested_shares = account_order.filled_shares + request.target_requested
    account_order.filled_shares += request.shares
    account_order.remaining_shares = max(0, request.target_requested - request.shares)
    account_order.last_update_date = date_str
    account_order.last_event = "FILL"
    if request.shares < request.target_requested:
        request.order.remaining_shares = request.target_requested - request.shares
        request.order.attempts += 1
        account_order.attempts = request.order.attempts
        if request.order.grant_id:
            # Strategic capacity retries are fresh physical orders while the
            # economic grant/event remain unchanged. The filled order keeps
            # its complete audit quantity and a broker-late fill can still be
            # reconciled against the same grant identity.
            account_order.status = OrderStatus.CANCELLED.value
            account_order.cancel_reason = "strategic partial remainder replaced"
            account_order.last_event = "PARTIAL_REMAINDER_RELEASED"
            account_order.remainder_release_session = date_str
            account_order.remainder_release_shares = account_order.remaining_shares
            retained.append(
                replace(
                    request.order,
                    order_id="",
                    remaining_shares=request.target_requested - request.shares,
                )
            )
        else:
            account_order.status = OrderStatus.PARTIALLY_FILLED.value
            retained.append(request.order)
    else:
        account_order.status = OrderStatus.FILLED.value
    if fill.side == Side.BUY.value and fill.grant_id:
        if (
            account.strategic_grant is not None
            and account.strategic_grant.candidate_symbol == fill.symbol
        ):
            record_strategic_grant_fill(
                account.strategic_grant,
                grant_id=fill.grant_id,
                shares=fill.shares,
                completed=request.shares >= request.target_requested,
            )
        record_account_strategic_epoch_fill(
            account,
            epoch_id=fill.epoch_id,
            grant_id=fill.grant_id,
            symbol=fill.symbol,
            fill_session=fill.fill_date,
            filled_shares=fill.shares,
        )


class ExecutionPlanner:
    """Execute pending intents under A-share market and account constraints."""

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    def execute_open(
        self,
        *,
        date: pd.Timestamp,
        account: AccountState,
        panel: dict[str, pd.DataFrame],
    ) -> list[Fill]:
        """Execute pending intents at one open and retain every blocked remainder.

        Sells are processed before buys. Each fill updates the durable ledger,
        cash, tranche inventory, and pending quantity under market, capacity,
        lot-size, and T+1 constraints.
        """

        date_str = str(date.date())
        retained: list[PendingOrder] = []
        fills: list[Fill] = []
        orders = sorted(
            account.pending_orders,
            key=lambda item: (item.side != Side.SELL.value, item.symbol),
        )
        ledger = _register_open_orders(account, orders, date_str=date_str)
        for order in orders:
            account_order = ledger[order.order_id]
            row = _eligible_open_row(
                date=date,
                date_str=date_str,
                order=order,
                account=account,
                account_order=account_order,
                panel=panel,
                retained=retained,
            )
            if row is None:
                continue
            request = _size_open_order(
                cfg=self.cfg,
                date=date,
                order=order,
                account=account,
                account_order=account_order,
                panel=panel,
                row=row,
                retained=retained,
            )
            if request is None:
                continue
            fill = _build_open_fill(
                cfg=self.cfg,
                request=request,
                date_str=date_str,
                account=account,
            )
            _record_open_fill(
                fill=fill,
                request=request,
                date_str=date_str,
                account=account,
                retained=retained,
                fills=fills,
            )
        if account.cash < -1e-6:
            raise RuntimeError("execution produced negative cash")
        account.pending_orders = retained
        return fills
