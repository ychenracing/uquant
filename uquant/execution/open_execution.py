"""Task 6 mechanical owner for open execution."""

from __future__ import annotations

import math
from datetime import date as date_type
from datetime import timedelta
from typing import Any

import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..portfolio_core import symbol_weight_cap
from ..types import (
    AccountState,
    Fill,
    OrderStatus,
    PendingOrder,
    Position,
    Side,
    Tranche,
)
from .fees import fee_components
from .market_constraints import _blocked
from .reconciliation import _active_order_status, _register_account_order
from .tranches import (
    _allocate_sell_costs,
    _consume_sell_tranches,
    _rebuild_position_from_tranches,
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
                continue
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
                continue
            if pd.Timestamp(order.signal_date) >= date:
                account_order.status = _active_order_status(account_order)
                account_order.last_update_date = date_str
                account_order.last_event = "WAITING_NEXT_OPEN"
                retained.append(order)
                continue
            frame = panel.get(order.symbol)
            if frame is None or date not in frame.index:
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = _active_order_status(account_order)
                account_order.last_update_date = date_str
                account_order.last_event = "MISSING_OR_SUSPENDED"
                retained.append(order)
                continue
            row = frame.loc[date]
            history = frame.loc[:date]
            if len(history) < 2:
                account_order.status = _active_order_status(account_order)
                account_order.last_update_date = date_str
                account_order.last_event = "INSUFFICIENT_HISTORY"
                retained.append(order)
                continue
            previous_close = float(history.iloc[-2]["close"])
            if _blocked(order.symbol, order.side, row, previous_close):
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = _active_order_status(account_order)
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
            capacity = int(math.floor(volume_shares * self.cfg.max_volume_participation / 100.0) * 100)
            shares = min(requested, capacity)
            if order.side == Side.BUY.value:
                projected_positions = sum(position.shares > 0 for position in account.positions.values()) + (
                    current.shares == 0
                )
                if projected_positions > self.cfg.max_positions:
                    order.attempts += 1
                    account_order.attempts = order.attempts
                    account_order.status = _active_order_status(account_order)
                    account_order.last_update_date = date_str
                    account_order.last_event = "POSITION_CAP_BLOCKED"
                    retained.append(order)
                    continue
                max_by_weight = (
                    int(
                        math.floor(
                            symbol_weight_cap(self.cfg, account, order.symbol)
                            * open_equity
                            / execution_price
                            / 100.0
                        )
                        * 100
                    )
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
                continue
            gross = shares * execution_price
            commission, stamp, transfer = fee_components(order.side, gross, self.cfg)
            slippage_cost = shares * abs(execution_price - open_price)
            sold_tranches: list[dict[str, Any]] = []
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
                        avg_cost=(gross + commission + transfer) / shares,
                        entry_date=date_str,
                        sellable_date=sellable_date,
                        highest_close=float(row["close"]),
                        lowest_close=float(row["close"]),
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
                    )
                )
                account.positions[order.symbol] = current
            else:
                account.cash += gross - commission - stamp - transfer
                sold_tranches = _consume_sell_tranches(
                    current,
                    shares=shares,
                    date=date_str,
                    reduction_policy=order.reduction_policy,
                )
                _allocate_sell_costs(
                    sold_tranches,
                    commission=commission,
                    stamp_duty=stamp,
                    transfer_fee=transfer,
                    slippage_cost=slippage_cost,
                )
                _rebuild_position_from_tranches(current)
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
            )
            account.fills.append(fill)
            fills.append(fill)
            account_order.requested_shares = account_order.filled_shares + target_requested
            account_order.filled_shares += shares
            account_order.remaining_shares = max(0, target_requested - shares)
            account_order.last_update_date = date_str
            account_order.last_event = "FILL"
            if shares < target_requested:
                order.remaining_shares = target_requested - shares
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
