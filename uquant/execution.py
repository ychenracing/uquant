"""A-share next-tradable-open execution and T+1 tranche lifecycle."""

from __future__ import annotations

import math
from datetime import date as date_type
from datetime import timedelta
from typing import Any

import pandas as pd

from .config import SystemConfig
from .features import scalar
from .portfolio_core import symbol_weight_cap
from .types import (
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    Fill,
    Lifecycle,
    OrderStatus,
    PendingOrder,
    Position,
    ReductionPolicy,
    Side,
    Target,
    Tranche,
    order_intent_metadata,
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
    """Return whether volume or a one-price limit prevents this side from filling."""

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
            and target.reason
            in {
                "mature anchored leader",
                "causal crash-recovery leader",
                "completed post-shock restoration; retain price drift",
                "post-shock restoration; retain winner drift",
            }
        ):
            # Sticky strategic holdings express a hold decision, not a request
            # to rebalance price drift back to yesterday's close weight.
            continue
        current_value = (current.shares if current else 0) * prices.get(target.symbol, 0.0)
        difference = target.weight * equity - current_value
        threshold = max(cfg.min_trade_value, cfg.min_trade_weight * equity)
        # Restoration has a smaller weight band than ordinary rebalancing, but
        # it must still respect the broker's absolute minimum ticket.  Keeping
        # these two thresholds separate previously generated orders that the
        # allocator itself considered economically unexecutable.
        restoration_threshold = max(
            cfg.min_trade_value,
            (
                cfg.protected_restore_min_trade_weight
                if target.symbol in account.protected_weights
                and target.weight >= cfg.core_admission_weight
                else cfg.restoration_min_trade_weight
            )
            * equity,
        )
        restoration_buy_below_completion = bool(
            difference > 0
            and target.symbol
            in {
                *account.protected_weights,
                *account.strategic_restore_weights,
            }
            and equity > 1e-12
            and current_value / equity < 0.95 * target.weight
            and difference >= restoration_threshold
        )
        if target.weight == 0 and current_value > 0:
            difference = -current_value
        elif abs(difference) < threshold and not restoration_buy_below_completion:
            continue
        planned.append(
            PendingOrder(
                signal_date=signal_date,
                symbol=target.symbol,
                side=Side.BUY.value if difference > 0 else Side.SELL.value,
                target_weight=target.weight,
                reason=target.reason,
                lifecycle=target.lifecycle,
                reduction_policy=target.reduction_policy,
                reason_code=target.reason_code,
                exit_kind=target.exit_kind,
                entry_score=target.alpha_score,
                entry_confidence=target.confidence,
                entry_regime=account.opportunity,
                entry_industry_strength=target.entry_industry_strength,
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
    cfg: SystemConfig | None = None,
) -> tuple[PendingOrder, ...]:
    """Keep blocked/partial orders while letting today's target supersede stale intent."""
    target_by_symbol = {target.symbol: target for target in targets}

    def durable_subthreshold_buy(
        order: PendingOrder,
        target: Target | None,
    ) -> bool:
        """Keep a partially filled buy when its economic target is unchanged."""

        return bool(
            cfg is not None
            and target is not None
            and order.side == Side.BUY.value
            and bool(order.order_id)
            and order.remaining_shares > 0
            and target.weight > 1e-12
            and order.lifecycle == target.lifecycle
            and order.reduction_policy == target.reduction_policy
            and order.reason_code == target.reason_code
            and order.exit_kind == target.exit_kind
            and abs(order.target_weight - target.weight)
            < cfg.min_trade_weight
        )

    merged: dict[str, PendingOrder] = {}
    for order in retained:
        target = target_by_symbol.get(order.symbol)
        if target is None:
            continue
        consistent = (
            (order.side == Side.BUY.value and target.weight > 0) or order.side == Side.SELL.value
        ) and (abs(order.target_weight - target.weight) <= 1e-12)
        same_execution_policy = (
            order.lifecycle == target.lifecycle
            and order.reduction_policy == target.reduction_policy
            and order.reason_code == target.reason_code
            and order.exit_kind == target.exit_kind
        )
        durable_full_exit = bool(
            order.side == Side.SELL.value
            and order.order_id
            and order.remaining_shares > 0
            and abs(order.target_weight) <= 1e-12
            and abs(target.weight) <= 1e-12
        )
        durable_partial_risk_exit = bool(
            cfg is not None
            and order.side == Side.SELL.value
            and order.order_id
            and order.remaining_shares > 0
            and order.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
            and target.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
            and target.weight > 1e-12
            and target.weight < order.target_weight
            and order.target_weight - target.weight < cfg.min_trade_weight
        )
        if (
            (consistent and (same_execution_policy or durable_full_exit))
            or durable_partial_risk_exit
            or durable_subthreshold_buy(order, target)
        ):
            merged[order.symbol] = order
    for order in planned:
        existing = merged.get(order.symbol)
        if existing is not None:
            target = target_by_symbol.get(order.symbol)
            if durable_subthreshold_buy(existing, target):
                continue
            durable_partial_risk_exit = bool(
                cfg is not None
                and target is not None
                and existing.side == Side.SELL.value
                and existing.order_id
                and existing.remaining_shares > 0
                and existing.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
                and target.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
                and target.weight > 1e-12
                and target.weight < existing.target_weight
                and existing.target_weight - target.weight < cfg.min_trade_weight
            )
            if durable_partial_risk_exit:
                continue
        if (
            existing is not None
            and existing.side == order.side
            and abs(existing.target_weight - order.target_weight) <= 1e-12
            and (
                (
                    existing.lifecycle == order.lifecycle
                    and existing.reduction_policy == order.reduction_policy
                    and existing.reason_code == order.reason_code
                    and existing.exit_kind == order.exit_kind
                )
                or (
                    existing.side == Side.SELL.value
                    and abs(existing.target_weight) <= 1e-12
                    and abs(order.target_weight) <= 1e-12
                )
            )
        ):
            # An unchanged GTC instruction remains one broker order.  A full
            # exit is also already maximally conservative, so a later change
            # in attribution cannot justify replacing its immutable intent.
            continue
        merged[order.symbol] = order
    return tuple(sorted(merged.values(), key=lambda item: (item.side != Side.SELL.value, item.symbol)))


def _register_account_order(
    account: AccountState,
    order: PendingOrder,
    *,
    submitted_date: str,
) -> AccountOrder:
    """Reuse a matching ledger order or allocate a stable new order identifier."""

    if order.order_id:
        existing = next(
            (item for item in account.order_ledger if item.order_id == order.order_id),
            None,
        )
        if existing is not None:
            pending_metadata = order_intent_metadata(order)
            ledger_metadata = order_intent_metadata(existing)
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
                    f"pending order {order.order_id} immutable metadata differs from account order: "
                    + ", ".join(changed)
                )
            return existing
        raise RuntimeError(f"pending order references unknown account order {order.order_id}")
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
        reduction_policy=order.reduction_policy,
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        entry_score=order.entry_score,
        entry_confidence=order.entry_confidence,
        entry_regime=order.entry_regime,
        entry_industry_strength=order.entry_industry_strength,
    )
    account.order_ledger.append(entry)
    return entry


def _active_order_status(order: AccountOrder) -> str:
    """Never regress an already-partial broker order back to merely open."""
    return str(OrderStatus.PARTIALLY_FILLED.value if order.filled_shares > 0 else OrderStatus.OPEN.value)


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
        entry.status = OrderStatus.REPLACED.value if replacement is not None else OrderStatus.CANCELLED.value
        entry.replaced_by = replacement.order_id if replacement is not None else ""
        entry.cancel_reason = "daily target changed" if replacement is not None else "daily target removed"
        entry.last_update_date = submitted_date
        entry.last_event = entry.status
    return current


_RISK_LIFECYCLE_PRIORITY = {
    Lifecycle.SATELLITE.value: 0,
    Lifecycle.ADD2.value: 1,
    Lifecycle.ADD1.value: 2,
    Lifecycle.RECOVERY.value: 3,
    Lifecycle.CORE.value: 4,
}


def risk_priority_tranche_key(
    item: Tranche,
) -> tuple[int, float, float, float, str, str, str]:
    """Return the one canonical fragile-first ordering used by every execution path."""
    lifecycle_rank = _RISK_LIFECYCLE_PRIORITY.get(
        item.lifecycle,
        _RISK_LIFECYCLE_PRIORITY[Lifecycle.CORE.value],
    )
    if item.lifecycle == Lifecycle.CORE.value:
        # Within the protected core, retire structurally weaker lots first:
        # deeper adverse excursion, less favorable excursion, and weaker
        # entry evidence all sort ahead of a healthy long-term winner.
        damage_key = (item.mae, item.mfe, item.entry_score)
    else:
        damage_key = (0.0, 0.0, 0.0)
    return (
        lifecycle_rank,
        *damage_key,
        item.sellable_date,
        item.entry_date,
        item.tranche_id,
    )


def _sell_tranches(
    position: Position,
    *,
    date: str,
    reduction_policy: str,
) -> list[Tranche]:
    """Return eligible lots in the economic order requested by the strategy."""
    eligible = [item for item in position.tranches if item.sellable_date <= date]
    if reduction_policy != ReductionPolicy.RISK_PRIORITY.value:
        # Preserve the historical ordinary-exit contract exactly.
        return sorted(eligible, key=lambda item: (item.sellable_date, item.entry_date))

    return sorted(eligible, key=risk_priority_tranche_key)


def _consume_sell_tranches(
    position: Position,
    *,
    shares: int,
    date: str,
    reduction_policy: str,
) -> list[dict[str, Any]]:
    """Consume exact sellable lots and return immutable fill attribution."""
    remaining = shares
    sold_tranches: list[dict[str, Any]] = []
    for tranche in _sell_tranches(
        position,
        date=date,
        reduction_policy=reduction_policy,
    ):
        if remaining <= 0:
            break
        sold = min(tranche.shares, remaining)
        sold_tranches.append(
            {
                "tranche_id": tranche.tranche_id,
                "shares": sold,
                "cost": tranche.avg_cost,
                "unit_cost": tranche.avg_cost,
                "avg_cost": tranche.avg_cost,
                "cost_basis": sold * tranche.avg_cost,
                "lifecycle": tranche.lifecycle,
                "mfe": tranche.mfe,
                "mae": tranche.mae,
                "entry_date": tranche.entry_date,
                "entry_score": tranche.entry_score,
                "entry_confidence": tranche.entry_confidence,
                "entry_regime": tranche.entry_regime,
                "entry_industry_strength": tranche.entry_industry_strength,
            }
        )
        tranche.shares -= sold
        remaining -= sold
    if remaining:
        raise RuntimeError("sell fill exceeds eligible T+1 tranche inventory")
    position.tranches = [item for item in position.tranches if item.shares > 0]
    return sold_tranches


def _allocate_sell_costs(
    sold_tranches: list[dict[str, Any]],
    *,
    commission: float,
    stamp_duty: float,
    transfer_fee: float,
    slippage_cost: float,
) -> None:
    """Allocate every fill-level selling cost by each lot's actual shares."""
    total_shares = sum(int(item["shares"]) for item in sold_tranches)
    if total_shares <= 0:
        return
    totals = {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "slippage_cost": slippage_cost,
    }
    allocated = {name: 0.0 for name in totals}
    for index, item in enumerate(sold_tranches):
        is_last = index == len(sold_tranches) - 1
        ratio = int(item["shares"]) / total_shares
        for name, total in totals.items():
            amount = total - allocated[name] if is_last else total * ratio
            item[name] = amount
            allocated[name] += amount
        item["fees"] = sum(float(item[name]) for name in ("commission", "stamp_duty", "transfer_fee"))
        item["transaction_costs"] = float(item["fees"]) + float(item["slippage_cost"])


def _rebuild_position_from_tranches(position: Position) -> None:
    """Recompute aggregate state solely from the economic lots still owned."""
    shares = sum(item.shares for item in position.tranches)
    if shares <= 0:
        position.shares = 0
        return
    position.shares = shares
    position.avg_cost = sum(item.shares * item.avg_cost for item in position.tranches) / shares
    position.entry_date = min(item.entry_date for item in position.tranches)
    position.highest_close = max(item.highest_close for item in position.tranches)
    newest = max(
        position.tranches,
        key=lambda item: (item.entry_date, item.sellable_date, item.tranche_id),
    )
    position.lifecycle = newest.lifecycle


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
                and account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
                and any(item.side == Side.SELL.value for item in retained)
            ):
                # A recovery-owner handoff is explicitly sell-funded.  The
                # sorted sell intents execute first, but a limit/T+1/capacity
                # block must also hold every replacement BUY; cash on hand is
                # not permission to exceed the frozen gross budget.
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
