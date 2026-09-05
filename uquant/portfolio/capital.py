"""Shared executable capital limits for every admission in the one book."""

from __future__ import annotations

import math
from typing import Any, cast

import pandas as pd

from ..config import SystemConfig
from ..portfolio_core import current_weights
from ..types import AccountState, LeaderScore


def admission_room(
    *,
    cfg: SystemConfig,
    symbol: str,
    committed: dict[str, float],
    leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    gross_cap: float,
    symbol_cap: float | None = None,
    concentration_cap: float | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> float:
    """Cap increments against the whole committed book; missing evidence is no room."""
    detail = diagnostics if diagnostics is not None else {}
    name_limit = cfg.max_symbol_weight if symbol_cap is None else symbol_cap
    cluster_limit = cfg.industry_weight_cap if concentration_cap is None else concentration_cap
    active = sorted(s for s, weight in committed.items() if weight > 1e-12 and s != symbol)
    detail["position_slots"] = cfg.max_positions - len(active)
    if len(active) >= cfg.max_positions:
        detail.update(block="POSITION_COUNT_LIMIT", effective_increment_room=0.0)
        return 0.0
    if any(s not in leaders or s not in user_panel for s in active):
        detail.update(block="MISSING_BOOK_EVIDENCE", effective_increment_room=0.0)
        return 0.0
    industry = leaders[symbol].industry
    same_industry = sum(committed[s] for s in active if leaders[s].industry == industry)
    limits = {
        "symbol_room": name_limit - committed.get(symbol, 0.0),
        "gross_room": gross_cap - sum(committed.values()),
        "industry_room": cluster_limit - same_industry - committed.get(symbol, 0.0),
    }
    detail.update(limits)
    room = min(limits.values())
    correlation_room = _correlation_room(
        cfg=cfg, symbol=symbol, active=active, committed=committed, user_panel=user_panel,
        date=date, cluster_limit=cluster_limit, diagnostics=detail,
    )
    result = max(0.0, min(room, correlation_room))
    detail["effective_increment_room"] = result
    return result


def _correlation_room(
    *, cfg: SystemConfig, symbol: str, active: list[str], committed: dict[str, float],
    user_panel: dict[str, pd.DataFrame], date: pd.Timestamp, cluster_limit: float,
    diagnostics: dict[str, Any],
) -> float:
    names = [*active, symbol]
    returns = pd.DataFrame(
        {
            s: user_panel[s].loc[:date, "close"].pct_change(fill_method=None).tail(cfg.correlation_window)
            for s in names
        }
    )
    connected = {s: {s} for s in names}
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            pair = returns[[left, right]].dropna()
            if len(pair) < cfg.correlation_window:
                diagnostics["correlation_block"] = "INSUFFICIENT_CORRELATION_HISTORY"
                return 0.0
            correlation = float(cast(float, pair.corr().iloc[0, 1]))
            if not math.isfinite(correlation):
                diagnostics["correlation_block"] = "NONFINITE_CORRELATION"
                return 0.0
            if correlation > cfg.risk_correlation:
                connected[left].add(right)
                connected[right].add(left)
    cluster = {symbol}
    while (expanded := cluster | set().union(*(connected[s] for s in cluster))) != cluster:
        cluster = expanded
    diagnostics["correlation_cluster"] = sorted(cluster)
    room = cluster_limit - sum(committed.get(s, 0.0) for s in cluster)
    diagnostics["correlation_room"] = room
    return room


def committed_capital(
    *,
    account: AccountState,
    prices: dict[str, float],
    proposed: dict[str, float],
) -> tuple[dict[str, float], float]:
    """Reserve live positions and pending buys; unfunded sells create no cash."""
    weights, equity = current_weights(account, prices)
    committed = {s: max(weights.get(s, 0.0), proposed.get(s, 0.0)) for s in set(weights) | set(proposed)}
    for order in account.pending_orders:
        if order.side == "BUY":
            committed[order.symbol] = max(committed.get(order.symbol, 0.0), order.target_weight)
    cash = max(
        0.0, account.cash / equity - sum(max(0.0, w - weights.get(s, 0.0)) for s, w in committed.items())
    )
    return committed, cash


def funded_increment(
    *,
    cfg: SystemConfig,
    symbol: str,
    desired: float,
    current: float,
    committed: dict[str, float],
    cash_room: float,
    leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    gross_cap: float,
    symbol_cap: float | None = None,
    concentration_cap: float | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> float:
    """A continuing intent can use its own reservation, subject to today's caps."""
    reserved = max(0.0, committed.get(symbol, 0.0) - current)
    detail = diagnostics if diagnostics is not None else {}
    detail.update(desired_increment=desired-current, reserved_for_intent=reserved,
                  unreserved_cash=cash_room, cash_room=cash_room+reserved)
    result = max(0.0, min(
        desired - current,
        cash_room + reserved,
        admission_room(
            cfg=cfg, symbol=symbol, committed={**committed, symbol: current},
            leaders=leaders, user_panel=user_panel, date=date, gross_cap=gross_cap,
            symbol_cap=symbol_cap, concentration_cap=concentration_cap, diagnostics=detail,
        ),
    ))
    detail["funded_increment"] = result
    return result
