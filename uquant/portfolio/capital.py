"""Shared executable capital limits for every admission in the one book."""

from __future__ import annotations

import math
from typing import cast

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
) -> float:
    """Cap increments against the whole committed book; missing evidence is no room."""
    name_limit = cfg.max_symbol_weight if symbol_cap is None else symbol_cap
    cluster_limit = cfg.industry_weight_cap if concentration_cap is None else concentration_cap
    active = sorted(s for s, weight in committed.items() if weight > 1e-12 and s != symbol)
    if len(active) >= cfg.max_positions:
        return 0.0
    if any(s not in leaders or s not in user_panel for s in active):
        return 0.0
    industry = leaders[symbol].industry
    same_industry = sum(committed[s] for s in active if leaders[s].industry == industry)
    room = min(
        name_limit - committed.get(symbol, 0.0),
        gross_cap - sum(committed.values()),
        cluster_limit - same_industry - committed.get(symbol, 0.0),
    )
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
                return 0.0
            correlation = float(cast(float, pair.corr().iloc[0, 1]))
            if not math.isfinite(correlation):
                return 0.0
            if correlation > cfg.risk_correlation:
                connected[left].add(right)
                connected[right].add(left)
    cluster = {symbol}
    while (expanded := cluster | set().union(*(connected[s] for s in cluster))) != cluster:
        cluster = expanded
    room = min(room, cluster_limit - sum(committed.get(s, 0.0) for s in cluster))
    return max(0.0, room)


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
