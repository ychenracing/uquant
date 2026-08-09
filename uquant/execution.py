"""A-share next-tradable-open execution and T+1 tranche lifecycle."""

from __future__ import annotations

import math
from datetime import date as date_type
from datetime import timedelta

import pandas as pd

from .config import SystemConfig
from .features import scalar
from .types import (
    AccountOrder,
    AccountState,
    Fill,
    OrderStatus,
    PendingOrder,
    Position,
    Side,
    Target,
    Tranche,
)


def fee_components(side: str, gross: float, cfg: SystemConfig) -> tuple[float, float, float]:
    """Return commission, stamp duty, and transfer fee for one fill."""
    commission = max(cfg.min_commission, gross * cfg.commission_rate) if gross > 0 else 0.0
    stamp = gross * cfg.stamp_duty if side == Side.SELL.value else 0.0
    transfer = gross * cfg.transfer_fee
    return commission, stamp, transfer


def _limit_rate(symbol: str) -> float:
    digits = symbol[2:]
    return 0.20 if digits.startswith(("300", "688")) else 0.10


def _blocked(
    symbol: str,
    side: str,
    row: pd.Series | pd.DataFrame,
    previous_close: float,
) -> bool:
    if isinstance(row, pd.DataFrame):
        if row.empty:
            return True
        row = row.iloc[-1]
    volume = float(row.get("volume", 0.0) or 0.0)
    if volume <= 0 or previous_close <= 0:
        return True
    rate = _limit_rate(symbol)
    upper = previous_close * (1.0 + rate)
    lower = previous_close * (1.0 - rate)
    if side == Side.BUY.value:
        return float(row["open"]) >= upper * 0.999 and float(row["low"]) >= upper * 0.999
    return float(row["open"]) <= lower * 1.001 and float(row["high"]) <= lower * 1.001


def plan_orders(
    *,
    signal_date: str,
    targets: tuple[Target, ...],
    account: AccountState,
    prices: dict[str, float],
    cfg: SystemConfig,
) -> tuple[PendingOrder, ...]:
    """Translate final target weights into one next-open intent per symbol.

    No fill is simulated here. Small adjustments remain inside the configured
    no-trade band, and sticky strategic holdings are not rebalanced merely due
    to close-price drift.
    """
    market = sum(position.shares * prices.get(symbol, 0.0) for symbol, position in account.positions.items())
    equity = account.cash + market
    planned: list[PendingOrder] = []
    for target in targets:
        current = account.positions.get(target.symbol)
        if (
            current is not None
            and current.shares > 0
            and target.weight > 0
            and target.reason in {"mature anchored leader", "causal crash-recovery leader"}
        ):
            # Sticky strategic holdings express a hold decision, not a request
            # to rebalance price drift back to yesterday's close weight.
            continue
        current_value = (current.shares if current else 0) * prices.get(target.symbol, 0.0)
        difference = target.weight * equity - current_value
        threshold = max(cfg.min_trade_value, cfg.min_trade_weight * equity)
        if target.weight == 0 and current_value > 0:
            difference = -current_value
        elif abs(difference) < threshold:
            continue
        planned.append(
            PendingOrder(
                signal_date=signal_date,
                symbol=target.symbol,
                side=Side.BUY.value if difference > 0 else Side.SELL.value,
                target_weight=target.weight,
                reason=target.reason,
                lifecycle=target.lifecycle,
            )
        )
    # Exactly one direction per symbol. Sells execute first at the next open.
    unique: dict[str, PendingOrder] = {}
    for order in planned:
        unique[order.symbol] = order
    return tuple(sorted(unique.values(), key=lambda item: (item.side != Side.SELL.value, item.symbol)))


def merge_pending_orders(
    *,
    retained: list[PendingOrder],
    planned: tuple[PendingOrder, ...],
    targets: tuple[Target, ...],
) -> tuple[PendingOrder, ...]:
    """Keep blocked/partial orders while letting today's target supersede stale intent."""
    target_by_symbol = {target.symbol: target for target in targets}
    merged: dict[str, PendingOrder] = {}
    for order in retained:
        target = target_by_symbol.get(order.symbol)
        if target is None:
            continue
        consistent = (order.side == Side.BUY.value and target.weight > 0) or (
            order.side == Side.SELL.value and target.weight == 0
        )
        if consistent:
            merged[order.symbol] = order
    for order in planned:
        existing = merged.get(order.symbol)
        if (
            existing is not None
            and existing.side == order.side
            and abs(existing.target_weight - order.target_weight) <= 1e-12
        ):
            # An unchanged GTC instruction remains one broker order even when
            # the daily planner independently derives the same target again.
            continue
        merged[order.symbol] = order
    return tuple(
        sorted(merged.values(), key=lambda item: (item.side != Side.SELL.value, item.symbol))
    )


def _register_account_order(
    account: AccountState,
    order: PendingOrder,
    *,
    submitted_date: str,
) -> AccountOrder:
    if order.order_id:
        existing = next(
            (item for item in account.order_ledger if item.order_id == order.order_id),
            None,
        )
        if existing is not None:
            return existing
        raise RuntimeError(
            f"pending order references unknown account order {order.order_id}"
        )
    order.order_id = f"O{account.next_order_sequence:09d}"
    account.next_order_sequence += 1
    entry = AccountOrder(
        order_id=order.order_id,
        signal_date=order.signal_date,
        submitted_date=submitted_date,
        symbol=order.symbol,
        side=order.side,
        target_weight=order.target_weight,
        reason=order.reason,
        lifecycle=order.lifecycle,
        last_update_date=submitted_date,
    )
    account.order_ledger.append(entry)
    return entry


def reconcile_account_orders(
    *,
    account: AccountState,
    previous: list[PendingOrder],
    current: tuple[PendingOrder, ...],
    submitted_date: str,
) -> tuple[PendingOrder, ...]:
    """Persist submissions and cancel/replace transitions without counting fills."""
    for order in previous:
        _register_account_order(
            account,
            order,
            submitted_date=order.signal_date or submitted_date,
        )
    for order in current:
        _register_account_order(account, order, submitted_date=submitted_date)

    current_ids = {order.order_id for order in current}
    current_by_symbol = {order.symbol: order for order in current}
    ledger = {item.order_id: item for item in account.order_ledger}
    for order in previous:
        if order.order_id in current_ids:
            continue
        entry = ledger[order.order_id]
        if entry.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        }:
            continue
        replacement = current_by_symbol.get(order.symbol)
        entry.status = (
            OrderStatus.REPLACED.value
            if replacement is not None
            else OrderStatus.CANCELLED.value
        )
        entry.replaced_by = replacement.order_id if replacement is not None else ""
        entry.cancel_reason = (
            "daily target changed" if replacement is not None else "daily target removed"
        )
        entry.last_update_date = submitted_date
        entry.last_event = entry.status
    return current


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
        date_str = str(date.date())
        retained: list[PendingOrder] = []
        fills: list[Fill] = []
        orders = sorted(account.pending_orders, key=lambda item: (item.side != Side.SELL.value, item.symbol))
        for order in orders:
            _register_account_order(
                account,
                order,
                submitted_date=order.signal_date or date_str,
            )
        ledger = {item.order_id: item for item in account.order_ledger}
        for order in orders:
            account_order = ledger[order.order_id]
            if pd.Timestamp(order.signal_date) >= date:
                account_order.status = OrderStatus.OPEN.value
                account_order.last_update_date = date_str
                account_order.last_event = "WAITING_NEXT_OPEN"
                retained.append(order)
                continue
            frame = panel.get(order.symbol)
            if frame is None or date not in frame.index:
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = OrderStatus.OPEN.value
                account_order.last_update_date = date_str
                account_order.last_event = "MISSING_OR_SUSPENDED"
                retained.append(order)
                continue
            row = frame.loc[date]
            history = frame.loc[:date]
            if len(history) < 2:
                account_order.status = OrderStatus.OPEN.value
                account_order.last_update_date = date_str
                account_order.last_event = "INSUFFICIENT_HISTORY"
                retained.append(order)
                continue
            previous_close = float(history.iloc[-2]["close"])
            if _blocked(order.symbol, order.side, row, previous_close):
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = OrderStatus.OPEN.value
                account_order.last_update_date = date_str
                account_order.last_event = "LIMIT_BLOCKED"
                retained.append(order)
                continue
            open_price = float(row["open"])
            execution_price = open_price * (
                1.0 + self.cfg.slippage if order.side == Side.BUY.value else 1.0 - self.cfg.slippage
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
            desired_shares = int(
                math.floor(order.target_weight * open_equity / execution_price / 100.0) * 100
            )
            if order.symbol.startswith("sh688") and desired_shares > 0:
                desired_shares = max(200, desired_shares)
            current = account.positions.get(order.symbol, Position(symbol=order.symbol))
            requested = desired_shares - current.shares
            if order.side == Side.SELL.value:
                requested = min(max(0, -requested), current.sellable_shares(date_str))
                if order.target_weight == 0:
                    requested = current.sellable_shares(date_str)
            else:
                requested = max(0, requested)
            volume_shares = float(row.get("volume", 0.0))
            # Some data sources report hands; amount/close detects that case without future data.
            implied = float(row.get("amount", 0.0)) / max(float(row["close"]), 1e-12)
            if implied > volume_shares * 50:
                volume_shares *= 100.0
            capacity = int(math.floor(volume_shares * self.cfg.max_volume_participation / 100.0) * 100)
            shares = min(requested, capacity)
            if order.side == Side.BUY.value:
                projected_positions = sum(position.shares > 0 for position in account.positions.values()) + (
                    current.shares == 0
                )
                if projected_positions > self.cfg.max_positions:
                    order.attempts += 1
                    account_order.attempts = order.attempts
                    account_order.status = OrderStatus.OPEN.value
                    account_order.last_update_date = date_str
                    account_order.last_event = "POSITION_CAP_BLOCKED"
                    retained.append(order)
                    continue
                max_by_weight = (
                    int(math.floor(self.cfg.max_symbol_weight * open_equity / execution_price / 100.0) * 100)
                    - current.shares
                )
                shares = min(shares, max(0, max_by_weight))
                while shares >= 100:
                    gross = shares * execution_price
                    commission, stamp, transfer = fee_components(order.side, gross, self.cfg)
                    if gross + commission + transfer <= account.cash + 1e-8:
                        break
                    shares -= 100
            if shares <= 0:
                if requested > 0:
                    order.attempts += 1
                    account_order.requested_shares = max(
                        account_order.requested_shares,
                        requested,
                    )
                    account_order.remaining_shares = requested
                    account_order.attempts = order.attempts
                    account_order.status = OrderStatus.OPEN.value
                    account_order.last_update_date = date_str
                    account_order.last_event = "CAPACITY_OR_CASH_BLOCKED"
                    retained.append(order)
                else:
                    account_order.status = OrderStatus.CANCELLED.value
                    account_order.cancel_reason = "target already satisfied"
                    account_order.last_update_date = date_str
                    account_order.last_event = "ZERO_REQUEST"
                continue
            gross = shares * execution_price
            commission, stamp, transfer = fee_components(order.side, gross, self.cfg)
            slippage_cost = shares * abs(execution_price - open_price)
            if order.side == Side.BUY.value:
                previous_lifecycle = current.lifecycle if current.shares > 0 else "NONE"
                account.cash -= gross + commission + transfer
                old_value = current.shares * current.avg_cost
                current.shares += shares
                current.avg_cost = (old_value + gross + commission + transfer) / current.shares
                current.entry_date = current.entry_date or date_str
                current.highest_close = max(current.highest_close, float(row["close"]))
                current.lifecycle = order.lifecycle
                if previous_lifecycle != order.lifecycle:
                    account.lifecycle_events.append(
                        {
                            "date": date_str,
                            "symbol": order.symbol,
                            "from": previous_lifecycle,
                            "to": order.lifecycle,
                            "shares": shares,
                            "reason": order.reason,
                        }
                    )
                sellable_date = str((date_type.fromisoformat(date_str) + timedelta(days=1)).isoformat())
                current.tranches.append(
                    Tranche(
                        tranche_id=f"{date_str}:{order.symbol}:{len(current.tranches) + 1}",
                        lifecycle=order.lifecycle,
                        shares=shares,
                        avg_cost=execution_price,
                        entry_date=date_str,
                        sellable_date=sellable_date,
                        highest_close=float(row["close"]),
                    )
                )
                account.positions[order.symbol] = current
            else:
                account.cash += gross - commission - stamp - transfer
                remaining = shares
                for tranche in sorted(
                    current.tranches, key=lambda item: (item.sellable_date, item.entry_date)
                ):
                    if tranche.sellable_date > date_str or remaining <= 0:
                        continue
                    sold = min(tranche.shares, remaining)
                    tranche.shares -= sold
                    remaining -= sold
                current.tranches = [item for item in current.tranches if item.shares > 0]
                current.shares -= shares
                if current.shares <= 0:
                    account.positions.pop(order.symbol, None)
                else:
                    account.positions[order.symbol] = current
            fill = Fill(
                signal_date=order.signal_date,
                fill_date=date_str,
                symbol=order.symbol,
                side=order.side,
                shares=shares,
                price=execution_price,
                gross_value=gross,
                commission=commission,
                stamp_duty=stamp,
                transfer_fee=transfer,
                slippage_cost=slippage_cost,
                reason=order.reason,
                lifecycle=order.lifecycle,
                order_id=account_order.order_id,
            )
            account.fills.append(fill)
            fills.append(fill)
            account_order.requested_shares = max(
                account_order.requested_shares,
                account_order.filled_shares + requested,
            )
            account_order.filled_shares += shares
            account_order.remaining_shares = max(0, requested - shares)
            account_order.last_update_date = date_str
            account_order.last_event = "FILL"
            if shares < requested:
                order.remaining_shares = requested - shares
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = OrderStatus.PARTIALLY_FILLED.value
                retained.append(order)
            else:
                account_order.status = OrderStatus.FILLED.value
        if account.cash < -1e-6:
            raise RuntimeError("execution produced negative cash")
        account.pending_orders = retained
        return fills
