"""Backtest metrics and economic-accounting result assembly."""

from __future__ import annotations

from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from ..types import AccountOrder, Fill


def _drawdown_stats(equity: pd.Series) -> dict[str, Any]:
    """Return end-of-session drawdown depth, duration, and censored recovery.

    Recovery is measured from the peak preceding the deepest trough. An
    unrecovered drawdown reports ``None`` recovery lengths with
    ``drawdown_recovered = False`` and the observed underwater length.
    """
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    underwater = drawdown < 0
    duration = current = 0
    for flag in underwater:
        current = current + 1 if flag else 0
        duration = max(duration, current)
    values = drawdown.to_numpy(dtype=float)
    trough = int(values.argmin()) if len(values) else 0
    peak_value = float(peak.iloc[trough]) if len(values) else 0.0
    peak_location = int(np.flatnonzero(equity.to_numpy(dtype=float)[: trough + 1] >= peak_value)[-1]) if len(values) else 0
    recovery_location = next(
        (trough + offset for offset, value in enumerate(equity.iloc[trough + 1 :], start=1) if value >= peak_value),
        None,
    )
    recovered = recovery_location is not None or not len(values) or values[trough] >= 0
    if not len(values) or values[trough] >= 0:
        recovery_location = trough

    def _date(location: int | None) -> str | None:
        return None if location is None or not len(values) else str(equity.index[location])[:10]

    return {
        "max_drawdown": float(-drawdown.min()) + 0.0,
        "pointwise_drawdown_p95": float((-drawdown).quantile(0.95)) + 0.0,
        "max_drawdown_duration": duration,
        "drawdown_recovered": bool(recovered),
        "peak_to_recovery_days": None if recovery_location is None else recovery_location - peak_location,
        "trough_to_recovery_days": None if recovery_location is None else recovery_location - trough,
        "observed_underwater_sessions": (len(values) - 1 - peak_location) if recovery_location is None else 0,
        "drawdown_peak_date": _date(peak_location),
        "drawdown_trough_date": _date(trough),
        "drawdown_recovery_date": _date(recovery_location),
    }


def _calmar(cagr: float, max_drawdown: float, equity: pd.Series) -> tuple[float | None, str]:
    """Return Calmar with an explicit state; special states do not rank as zero."""

    if len(equity) < 20:
        return None, "SHORT_WINDOW"
    if float(equity.min()) <= 0:
        return None, "NONPOSITIVE_EQUITY"
    if max_drawdown <= 1e-12:
        return None, "ZERO_DRAWDOWN"
    return cagr / max_drawdown, "OK"


def _first_risk_reduction(
    *,
    fills: Any,
) -> Any:
    risk_tokens = ("risk", "drawdown", "shock", "crisis", "capital protection")
    structured_risk_exits = {
        "risk",
        "portfolio_risk",
        "sector_guard",
        "risk_off",
        "crisis",
        "capital_budget",
    }
    first_reduce = next(
        (
            fill.fill_date
            for fill in fills
            if fill.side == "SELL"
            and (
                fill.exit_kind in structured_risk_exits
                or any(token in fill.reason.lower() for token in risk_tokens)
            )
        ),
        None,
    )
    return first_reduce


def _holding_and_rolling_metrics(
    *,
    equity: Any,
    fills: Any,
) -> tuple[Any, Any, Any, Any]:
    holding_days: list[int] = []
    buy_lots: dict[str, list[list[Any]]] = {}
    inventory: dict[str, int] = {}
    round_trips = 0
    for fill in fills:
        if fill.side == "BUY":
            buy_lots.setdefault(fill.symbol, []).append([fill.shares, pd.Timestamp(fill.fill_date)])
            inventory[fill.symbol] = inventory.get(fill.symbol, 0) + fill.shares
            continue
        before = inventory.get(fill.symbol, 0)
        remaining = fill.shares
        if fill.sold_tranches:
            for allocation in fill.sold_tranches:
                entry_date = str(allocation.get("entry_date", ""))
                if entry_date:
                    holding_days.append((pd.Timestamp(fill.fill_date) - pd.Timestamp(entry_date)).days)
            # Execution supplied authoritative lot identity. The synthetic FIFO
            # queue is needed only when a fill lacks tranche attribution.
            remaining = 0
        else:
            for lot in buy_lots.get(fill.symbol, []):
                available = int(lot[0])
                if available <= 0 or remaining <= 0:
                    continue
                sold = min(available, remaining)
                holding_days.append((pd.Timestamp(fill.fill_date) - pd.Timestamp(lot[1])).days)
                lot[0] = available - sold
                remaining -= sold
        buy_lots[fill.symbol] = [lot for lot in buy_lots.get(fill.symbol, []) if int(lot[0]) > 0]
        inventory[fill.symbol] = max(0, before - fill.shares)
        if before > 0 and inventory[fill.symbol] == 0:
            round_trips += 1
    rolling20 = equity.pct_change(20, fill_method=None)
    rolling60 = equity.pct_change(60, fill_method=None)
    return holding_days, rolling20, rolling60, round_trips


def _return_and_drawdown_metrics(
    *,
    equity: Any,
    initial_cash: Any,
) -> tuple[Any, Any, Any, Any, Any, Any]:
    returns = equity.pct_change(fill_method=None).dropna()
    years = max(len(equity) / 242.0, 1 / 242.0)
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    cagr = float((equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0)
    sharpe = (
        float(np.sqrt(242) * returns.mean() / returns.std(ddof=0)) if returns.std(ddof=0) > 1e-12 else 0.0
    )
    dd = _drawdown_stats(equity)
    max_dd = float(dd["max_drawdown"])
    return cagr, dd, max_dd, sharpe, total_return, years


def _lead_to_drawdown(
    *,
    drawdown: pd.Series,
    equity: pd.Series,
    first_action: pd.Timestamp | None,
    threshold: float,
) -> int | None:
    """Count sessions from the first risk action to a drawdown crossing."""

    crossings = drawdown[drawdown >= threshold]
    if crossings.empty or first_action is None:
        return None
    target = crossings.index[0]
    target_location = equity.index.get_indexer(pd.Index([target]))[0]
    action_location = equity.index.get_indexer(
        pd.Index([first_action]),
        method="ffill",
    )[0]
    return int(target_location - action_location)


def _economic_order_groups(orders: list[AccountOrder]) -> list[list[AccountOrder]]:
    groups: list[list[AccountOrder]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for item in orders:
        key = (
            ("STRATEGIC_GRANT_EVENT", item.grant_id, item.event_id)
            if item.grant_id and item.event_id
            else ("PHYSICAL_ORDER", item.order_id)
        )
        index = indexes.get(key)
        if index is None:
            indexes[key] = len(groups)
            groups.append([item])
        else:
            groups[index].append(item)
    return groups


def _order_ledger_rows(groups: list[list[AccountOrder]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        first = group[0]
        last = group[-1]
        filled_shares = sum(item.filled_shares for item in group)
        remaining_shares = last.remaining_shares
        rows.append(
            {
                "order_id": first.order_id,
                "signal_date": first.signal_date,
                "submitted_date": first.submitted_date,
                "symbol": first.symbol,
                "side": first.side,
                "target_weight": first.target_weight,
                "reason": first.reason,
                "lifecycle": first.lifecycle,
                "reduction_policy": first.reduction_policy,
                "reason_code": first.reason_code,
                "exit_kind": first.exit_kind,
                "status": last.status,
                "requested_shares": filled_shares + remaining_shares,
                "filled_shares": filled_shares,
                "remaining_shares": remaining_shares,
                "attempts": max(item.attempts for item in group),
                "last_update_date": last.last_update_date,
                "last_event": last.last_event,
                "replaced_by": last.replaced_by,
                "cancel_reason": last.cancel_reason,
            }
        )
    return rows


def performance_metrics(
    *,
    equity_rows: list[tuple[pd.Timestamp, float]],
    fills: list[Fill],
    orders: list[AccountOrder],
    initial_cash: float,
    risk_events: list[dict[str, Any]],
    benchmark_total_return: float,
) -> dict[str, Any]:
    """Calculate portfolio, drawdown, turnover, order, and attribution metrics."""
    order_groups = _economic_order_groups(orders)
    broker_order_groups = [
        group for group in order_groups if sum(item.filled_shares for item in group) > 0
    ]
    equity = pd.Series({date: value for date, value in equity_rows}, dtype=float).sort_index()
    cagr, dd, max_dd, sharpe, total_return, years = _return_and_drawdown_metrics(
        equity=equity,
        initial_cash=initial_cash,
    )
    gross_turnover = sum(item.gross_value for item in fills) / initial_cash
    fees = sum(item.commission + item.stamp_duty + item.transfer_fee for item in fills)
    holding_days, rolling20, rolling60, round_trips = _holding_and_rolling_metrics(
        equity=equity,
        fills=fills,
    )
    first_caution = next(
        (str(item.get("date")) for item in risk_events if item.get("to") == "CAUTION"),
        None,
    )
    first_risk_off = next(
        (str(item.get("date")) for item in risk_events if item.get("to") in {"RISK_OFF", "CRISIS"}),
        None,
    )
    first_reduce = _first_risk_reduction(
        fills=fills,
    )
    first_action = min(
        (pd.Timestamp(value) for value in (first_caution, first_risk_off, first_reduce) if value),
        default=None,
    )
    drawdown = 1.0 - equity / equity.cummax()
    calmar, calmar_status = _calmar(cagr, max_dd, equity)

    return {
        "total_return": total_return,
        "final_wealth_multiple": 1.0 + total_return,
        "cagr": cagr,
        "benchmark_total_return": benchmark_total_return,
        "benchmark_final_wealth_multiple": 1.0 + benchmark_total_return,
        "excess_return": total_return - benchmark_total_return,
        "sharpe": sharpe,
        "calmar": calmar,
        "calmar_status": calmar_status,
        **dd,
        "worst_20d": float(rolling20.min()) if rolling20.notna().any() else 0.0,
        "worst_60d": float(rolling60.min()) if rolling60.notna().any() else 0.0,
        "account_orders": len(broker_order_groups),
        "submitted_account_orders": len(order_groups),
        "physical_orders": len(orders),
        "physical_orders_filled": sum(item.filled_shares > 0 for item in orders),
        "fill_count": len(fills),
        "unfilled_account_submissions": sum(
            sum(item.filled_shares for item in group) == 0 for group in order_groups
        ),
        "round_trips": round_trips,
        "gross_turnover": gross_turnover,
        "annual_turnover": gross_turnover / years,
        "median_holding_days": float(median(holding_days)) if holding_days else 0.0,
        "fees": fees,
        "slippage_cost": sum(item.slippage_cost for item in fills),
        "first_caution": first_caution,
        "first_risk_off": first_risk_off,
        "first_reduce": first_reduce,
        "lead_to_10pct_dd": _lead_to_drawdown(
            drawdown=drawdown,
            equity=equity,
            first_action=first_action,
            threshold=0.10,
        ),
        "lead_to_15pct_dd": _lead_to_drawdown(
            drawdown=drawdown,
            equity=equity,
            first_action=first_action,
            threshold=0.15,
        ),
        "risk_events": risk_events,
        "order_ledger": _order_ledger_rows(broker_order_groups),
        "submission_ledger": _order_ledger_rows(order_groups),
        "equity_curve": [{"date": str(date)[:10], "equity": value} for date, value in equity.items()],
    }


equity_drawdown_stats = _drawdown_stats
