"""Next-open execution and fill application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from typing import Any, cast

import pandas as pd

from ..account.corporate_actions import dividend_tax_on_sale, receivable_total
from ..config import SystemConfig
from ..market.valuation import mark_price
from ..models.strategic_epoch import record_account_strategic_epoch_fill
from ..models.strategic_grant import (
    acknowledge_strategic_grant_order,
    record_strategic_grant_fill,
)
from ..models.trading import account_order_decision_origin_session
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
from .market_constraints import (
    buy_share_step,
    legal_buy_shares,
    legal_sell_shares,
    price_limits,
    round_price,
    security_board,
)
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
    economic_target_requested: int
    shares: int


def _registered_remainder_request(
    account: AccountState,
    account_order: AccountOrder,
) -> int | None:
    """Preserve registered strategic partial quantities, including legacy chains."""

    predecessors = tuple(
        item
        for item in account.order_ledger
        if item.replaced_by == account_order.order_id
    )
    if not predecessors:
        if account_order.grant_id and account_order.filled_shares > 0:
            remaining = account_order.requested_shares - account_order.filled_shares
            if remaining <= 0 or account_order.remaining_shares != remaining:
                raise RuntimeError("strategic partial order remaining quantity differs")
            return remaining
        return None
    if len(predecessors) != 1:
        raise RuntimeError("strategic remainder successor predecessor differs")
    predecessor = predecessors[0]
    if predecessor.cancel_reason != "strategic partial remainder replaced":
        return None
    try:
        account_order_decision_origin_session(
            account_order,
            predecessor,
            prior_physical_fills=tuple(
                fill
                for fill in account.fills
                if fill.order_id == predecessor.order_id
            ),
        )
    except ValueError as exc:
        raise RuntimeError("strategic remainder successor evidence differs") from exc
    remaining = account_order.requested_shares - account_order.filled_shares
    if remaining <= 0 or account_order.remaining_shares != remaining:
        raise RuntimeError("strategic remainder successor remaining quantity differs")
    return remaining


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
    if _blocked(order.symbol, order.side, row, _limit_reference(history), date):
        order.attempts += 1
        account_order.attempts = order.attempts
        account_order.status = _active_order_status(account_order)
        account_order.last_update_date = date_str
        account_order.last_event = "LIMIT_BLOCKED"
        retained.append(order)
        return None
    return row


def _limit_reference(history: pd.DataFrame) -> float:
    """Return the exchange limit reference: the supplied ex-rights preclose, else the prior close."""

    row = history.iloc[-1]
    if "preclose" in history.columns:
        value = float(row["preclose"])
        if math.isfinite(value) and value > 0:
            return value
    return float(history.iloc[-2]["close"])


def _buy_cost(shares: int, price: float, cfg: SystemConfig, date: pd.Timestamp) -> tuple[float, float, float]:
    gross = shares * price
    commission, _stamp, transfer = fee_components(Side.BUY.value, gross, cfg, date)
    return gross, commission, transfer


def _bounded_buy_shares(
    *, cfg: SystemConfig, account: AccountState, order: PendingOrder, current: Position,
    open_equity: float, execution_price: float, shares: int, cash: float, date: pd.Timestamp,
    cap_price: float | None = None,
) -> int:
    """Fit a buy request within cash and user-selected ceilings at a legal quantity.

    ``execution_price`` budgets cash; ``cap_price`` is the price at which the
    target weight was sized and bounds the symbol weight (default: the same).
    """
    max_by_weight = math.floor(
        symbol_weight_cap(cfg, account, order.symbol) * open_equity / (cap_price or execution_price)
    ) - current.shares
    shares = min(shares, max(0, max_by_weight))
    if cfg.max_gross < 1.0:
        gross_room = max(0.0, cfg.max_gross * open_equity - (open_equity - account.cash))
        shares = min(shares, int(gross_room / execution_price))
    per_share = execution_price * (1.0 + cfg.commission_rate + (cfg.transfer_fee or 0.0001))
    shares = min(shares, int(max(0.0, cash) / per_share) + 1)
    shares = legal_buy_shares(order.symbol, shares)
    while shares > 0:
        gross, commission, transfer = _buy_cost(shares, execution_price, cfg, date)
        funded = gross + commission + transfer <= cash + 1e-8
        # An opening gap must not turn a lower account limit into extra
        # buying permission. Retained shares themselves are not force-sold.
        within_gross = (cfg.max_gross == 1.0 or
                        open_equity - account.cash + gross <=
                        cfg.max_gross * (open_equity - commission - transfer) + 1e-8)
        within_symbol = (cfg.max_symbol_weight == .60 or
                         (current.shares + shares) * execution_price <=
                         symbol_weight_cap(cfg, account, order.symbol) *
                         (open_equity - commission - transfer) + 1e-8)
        if funded and within_gross and within_symbol:
            break
        shares = legal_buy_shares(order.symbol, shares - buy_share_step(order.symbol, shares))
    return shares


def _previous_session_capacity(previous_row: pd.Series, cfg: SystemConfig) -> int:
    """Estimate opening capacity in shares from the last completed session."""
    volume_shares = float(previous_row.get("volume", 0.0))
    if not math.isfinite(volume_shares) or volume_shares <= 0:
        return 0
    return int(math.floor(volume_shares * cfg.max_volume_participation / 100.0) * 100)


def _risk_target_shortfall(
    order: PendingOrder, current: Position, open_price: float, open_equity: float,
) -> bool:
    return bool(
        order.side == Side.SELL.value
        and order.reduction_policy == "RISK_PRIORITY"
        and current.shares * open_price > order.target_weight * open_equity + 1e-8
    )


@dataclass(slots=True)
class _SessionBook:
    """Per-session execution state shared by every order at one open."""

    auction: bool
    buy_cash: float
    capacity_used: dict[str, int]
    marks: dict[str, float] | None


def _session_marks(
    account: AccountState, panel: dict[str, pd.DataFrame], date: pd.Timestamp, *, auction: bool,
) -> dict[str, float] | None:
    """Mark positions for sizing: prior close before the auction, else the open.

    Returns ``None`` when a held position has no valid price at all, which
    blocks every new buy for the session.
    """

    marks: dict[str, float] = {}
    for symbol, position in account.positions.items():
        if position.shares <= 0:
            continue
        mark = _mark_one(panel.get(symbol), date, auction=auction)
        if mark is None:
            return None
        marks[symbol] = mark
    return marks


def _mark_one(frame: pd.DataFrame | None, date: pd.Timestamp, *, auction: bool) -> float | None:
    if auction:
        mark = mark_price(frame, date - pd.Timedelta(days=1), field="close")
    else:
        mark = mark_price(frame, date, field="open")
    return None if mark is None else mark.price


def _open_prices(
    *, cfg: SystemConfig, symbol: str, date: pd.Timestamp, row: pd.Series, history: pd.DataFrame,
    buy: bool, auction: bool,
) -> tuple[float, float, float, bool]:
    """Return (budget price, execution price, quantity price, crosses) for one order.

    At the auction the quantity and limit are fixed before the open from the
    prior close; the open only decides whether the fixed order trades.
    """
    open_price = float(row["open"])
    side = 1.0 + cfg.slippage if buy else 1.0 - cfg.slippage
    if not auction:
        return open_price * side, open_price * side, open_price * side, True
    reference = float(history.iloc[-2]["close"])
    lower, upper = price_limits(
        symbol, date, _limit_reference(history), special_treatment=bool(row.get("special_treatment", False)),
    )
    if buy:
        limit = float(min(upper, round_price(reference * (1.0 + cfg.auction_limit_buffer))))
        budget = limit * (1.0 + cfg.slippage)
        return budget, min(open_price * side, budget), reference * side, open_price <= limit + 1e-9
    limit = float(max(lower, round_price(reference * (1.0 - cfg.auction_limit_buffer))))
    execution = max(open_price * side, limit * (1.0 - cfg.slippage))
    return reference, execution, reference * side, open_price >= limit - 1e-9


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
    book: _SessionBook,
) -> _OpenOrderRequest | None:
    date_str = str(date.date())
    open_price = float(row["open"])
    history = panel[order.symbol].loc[:date]
    previous_row = history.iloc[-2]
    buy = order.side == Side.BUY.value
    sizing_price, execution_price, target_price, crosses = _open_prices(
        cfg=cfg, symbol=order.symbol, date=date, row=row, history=history, buy=buy, auction=book.auction,
    )
    if book.marks is None:
        if buy:
            order.attempts += 1
            account_order.attempts = order.attempts
            account_order.status = _active_order_status(account_order)
            account_order.last_update_date = date_str
            account_order.last_event = "VALUATION_BLOCKED"
            retained.append(order)
            return None
        open_equity = math.nan
    else:
        for symbol, position in account.positions.items():
            if position.shares > 0 and symbol not in book.marks:
                fresh = _mark_one(panel.get(symbol), date, auction=book.auction)
                if fresh is None:
                    raise RuntimeError(f"{symbol} filled without a valid session mark")
                book.marks[symbol] = fresh
        open_equity = account.cash + receivable_total(account) + sum(
            float(position.shares) * book.marks[symbol]
            for symbol, position in account.positions.items()
            if position.shares > 0
        )
    current = account.positions.get(order.symbol, Position(symbol=order.symbol))
    if math.isfinite(open_equity):
        desired_shares = math.floor(order.target_weight * open_equity / target_price)
        if security_board(order.symbol) != "STAR":
            desired_shares = desired_shares // 100 * 100
    else:
        desired_shares = 0 if order.target_weight == 0 else current.shares
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
    economic_target_requested = target_requested
    registered_remainder = _registered_remainder_request(account, account_order)
    if registered_remainder is not None:
        requested = min(requested, registered_remainder)
        target_requested = registered_remainder
    # Previous-session liquidity is a proxy, not a guarantee of opening auction quantity.
    capacity = max(0, _previous_session_capacity(previous_row, cfg) - book.capacity_used.get(order.symbol, 0))
    shares = min(requested, capacity)
    if not crosses:
        shares = 0
    if order.side == Side.SELL.value:
        shares = legal_sell_shares(order.symbol, shares, holding=current.shares)
    else:
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
        cash = book.buy_cash if book.auction else account.cash
        if book.auction:
            # The submitted auction quantity is what the preset limit budget can
            # pay; a trimmed difference was never submitted and is not a remainder.
            def payable(requested: int) -> int:
                bounded = _bounded_buy_shares(cfg=cfg, account=account, order=order, current=current,
                    open_equity=open_equity, execution_price=sizing_price, shares=requested,
                    cash=cash, date=date, cap_price=target_price)
                return bounded if bounded > 0 else requested

            target_requested = payable(target_requested)
            economic_target_requested = payable(economic_target_requested)
        shares = _bounded_buy_shares(cfg=cfg, account=account, order=order, current=current,
            open_equity=open_equity, execution_price=sizing_price, shares=shares, cash=cash, date=date,
            cap_price=target_price)
    if shares <= 0:
        if economic_target_requested > 0:
            order.attempts += 1
            account_order.requested_shares = account_order.filled_shares + target_requested
            account_order.remaining_shares = target_requested
            order.remaining_shares = target_requested
            account_order.attempts = order.attempts
            account_order.status = _active_order_status(account_order)
            account_order.last_update_date = date_str
            account_order.last_event = (
                "AUCTION_LIMIT_NOT_REACHED" if not crosses
                else "T_PLUS_ONE_BLOCKED" if order.side == Side.SELL.value and requested == 0
                else "LIQUIDITY_PROXY_BLOCKED" if order.side == Side.SELL.value
                else "CAPACITY_OR_CASH_BLOCKED"
            )
            retained.append(order)
        else:
            risk_shortfall = math.isfinite(open_equity) and _risk_target_shortfall(
                order, current, open_price, open_equity,
            )
            if risk_shortfall:
                order.attempts += 1
                account_order.attempts = order.attempts
                account_order.status = _active_order_status(account_order)
                retained.append(order)
            else:
                account_order.status = OrderStatus.CANCELLED.value
                account_order.cancel_reason = "target already satisfied"
            account_order.last_update_date = date_str
            account_order.last_event = "RISK_TARGET_UNMET_LOT" if risk_shortfall else "ZERO_REQUEST"
        return None
    book.capacity_used[order.symbol] = book.capacity_used.get(order.symbol, 0) + shares
    if buy and book.auction:
        gross, commission, transfer = _buy_cost(shares, execution_price, cfg, date)
        book.buy_cash -= gross + commission + transfer
    return _OpenOrderRequest(
        order=order,
        account_order=account_order,
        row=row,
        current=current,
        open_price=open_price,
        execution_price=execution_price,
        target_requested=target_requested,
        economic_target_requested=economic_target_requested,
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
    has_position = current.shares > 0
    if order.grant_id and has_position and current.grant_id != order.grant_id:
        raise RuntimeError("strategic fill would create a second grant owner for one position")
    if order.epoch_id and has_position and current.epoch_id != order.epoch_id:
        raise RuntimeError("strategic fill would create a second epoch owner for one position")
    previous_lifecycle = current.lifecycle if current.shares > 0 else "NONE"
    account.cash -= gross + commission + transfer
    old_value = current.shares * current.avg_cost
    current.shares += request.shares
    current.avg_cost = (old_value + gross + commission + transfer) / current.shares
    current.entry_date = current.entry_date or date_str
    current.highest_close = max(current.highest_close, request.open_price)
    current.lifecycle = order.lifecycle
    if order.grant_id:
        current.grant_id = order.grant_id
    if order.epoch_id:
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
            highest_close=request.open_price,
            lowest_close=request.open_price,
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
    dividend_tax_on_sale(account, symbol=request.order.symbol, sold_tranches=sold_tranches, sale_date=date_str)
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
    commission, stamp, transfer = fee_components(request.order.side, gross, cfg, date_str)
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
        if (
            request.order.grant_id
            and request.shares >= request.economic_target_requested
        ):
            account_order.status = OrderStatus.CANCELLED.value
            account_order.cancel_reason = "target already satisfied"
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
                completed=request.shares >= request.economic_target_requested,
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
        auction = self.cfg.execution_clock == "AUCTION"
        book = _SessionBook(
            auction=auction,
            buy_cash=account.cash,
            capacity_used={},
            marks=_session_marks(account, panel, date, auction=auction),
        )
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
                book=book,
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
