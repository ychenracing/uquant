"""Same-close daily economic ledger rows."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as date_type
from typing import Any

from ..types import AccountState
from .concentration import finite_attribution_number as _finite


def build_daily_ledger_row(
    *,
    date: str,
    account: AccountState,
    close_prices: Mapping[str, float],
    previous_equity: float,
    target_weights: Mapping[str, float],
    target_gross: float,
    risk_gross_cap: float,
    system_gross_cap: float,
    risk_state: str,
    opportunity: str,
) -> dict[str, Any]:
    """Capture one same-close account/decision row without future information."""

    try:
        date_type.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily attribution ledger date must be ISO") from exc
    cash = _finite(account.cash, label="daily cash", minimum=0.0)
    prior = _finite(previous_equity, label="previous equity", minimum=0.0)
    position_values: dict[str, float] = {}
    for symbol, position in sorted(account.positions.items()):
        if position.shares <= 0:
            continue
        price = _finite(close_prices.get(symbol), label=f"daily close for {symbol}", minimum=0.0)
        if price <= 0.0:
            raise ValueError(f"daily close for {symbol} must be positive")
        position_values[symbol] = position.shares * price
    equity = cash + sum(position_values.values())
    if equity <= 0.0:
        raise ValueError("daily equity must be positive")
    risk_cap = _finite(risk_gross_cap, label="risk gross cap", minimum=0.0)
    system_cap = _finite(system_gross_cap, label="system gross cap", minimum=0.0)
    target = _finite(target_gross, label="target gross", minimum=0.0)
    effective_cap = min(risk_cap, system_cap)
    if target > effective_cap + 1e-12:
        binding_owner = "STRATEGY_RETENTION_OVERRIDE"
    elif math.isclose(target, effective_cap, rel_tol=0.0, abs_tol=1e-12):
        if math.isclose(risk_cap, system_cap, rel_tol=0.0, abs_tol=1e-12):
            binding_owner = "RISK_AND_SYSTEM"
        elif risk_cap < system_cap:
            binding_owner = "RISK"
        else:
            binding_owner = "SYSTEM"
    else:
        binding_owner = "STRATEGY"
    normalized_targets = {
        symbol: _finite(weight, label=f"target weight for {symbol}", minimum=0.0)
        for symbol, weight in sorted(target_weights.items())
    }
    weights = {symbol: value / equity for symbol, value in position_values.items()}
    gross = sum(abs(value) for value in position_values.values()) / equity
    net = sum(position_values.values()) / equity
    return {
        "date": date,
        "cash": cash,
        "equity": equity,
        "gross_exposure": gross,
        "net_exposure": net,
        "cash_weight": cash / equity,
        "position_weights": weights,
        "daily_pnl": equity - prior,
        "target_weights": normalized_targets,
        "target_gross": target,
        "caps": {"risk_gross": risk_cap, "system_gross": system_cap},
        "binding_owner": binding_owner,
        "risk_state": risk_state,
        "opportunity": opportunity,
    }
