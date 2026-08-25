"""Bounded exit, post-exit, cash-drag, and risk-avoidance diagnostics."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

import pandas as pd

from .concentration import finite_attribution_number as _finite


def attribution_diagnostics(
    *,
    daily_ledger: Sequence[Mapping[str, Any]],
    benchmark_close: Mapping[str, float],
    paired_counterfactual_equity: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Calculate non-accounting cash opportunity cost and paired risk avoidance."""

    dates = tuple(str(row.get("date", "")) for row in daily_ledger)
    if not dates or tuple(sorted(set(dates))) != dates:
        raise ValueError("diagnostic daily ledger must be unique and ordered")
    if set(benchmark_close) != set(dates):
        raise ValueError("cash-drag benchmark must exactly cover daily ledger dates")
    benchmark = {
        date: _finite(benchmark_close[date], label=f"benchmark close {date}", minimum=0.0) for date in dates
    }
    if any(value <= 0.0 for value in benchmark.values()):
        raise ValueError("cash-drag benchmark closes must be positive")
    cash_drag = 0.0
    for prior_row, date, prior_date in zip(daily_ledger, dates[1:], dates, strict=False):
        cash = _finite(prior_row.get("cash"), label=f"ledger cash {prior_date}", minimum=0.0)
        cash_drag -= cash * (benchmark[date] / benchmark[prior_date] - 1.0)
    if paired_counterfactual_equity is None:
        risk_avoidance: dict[str, Any] = {
            "status": "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL",
            "value": None,
            "is_accounting_pnl": False,
        }
    else:
        if set(paired_counterfactual_equity) != set(dates):
            raise ValueError("paired counterfactual must exactly cover daily ledger dates")
        counterfactual = {
            date: _finite(
                paired_counterfactual_equity[date],
                label=f"paired counterfactual equity {date}",
                minimum=0.0,
            )
            for date in dates
        }
        actual_final = _finite(daily_ledger[-1].get("equity"), label="actual final equity")
        risk_avoidance = {
            "status": "PAIRED_COUNTERFACTUAL",
            "value": actual_final - counterfactual[dates[-1]],
            "definition": "actual final equity minus paired counterfactual final equity",
            "is_accounting_pnl": False,
        }
    return {
        "cash_drag": {
            "status": "DIAGNOSTIC",
            "value": cash_drag,
            "definition": "negative prior-close cash times next-session benchmark return",
            "is_accounting_pnl": False,
        },
        "risk_avoidance": risk_avoidance,
    }


@dataclass(frozen=True, slots=True)
class ExitRecord:
    """Structured exit identity used only for bounded post-exit diagnostics."""

    symbol: str
    exit_date: str
    exit_price: float
    origin_subsystem: str
    mechanism: str
    benchmark_symbol: str = ""

    def __post_init__(self) -> None:
        if not self.symbol or not self.origin_subsystem or not self.mechanism:
            raise ValueError("exit diagnostics require structured attribution identity")
        try:
            date_type.fromisoformat(self.exit_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("exit diagnostic date must be ISO") from exc
        if not math.isfinite(self.exit_price) or self.exit_price <= 0.0:
            raise ValueError("exit diagnostic price must be positive and finite")


def _bounded_price_series(
    series: pd.Series,
    *,
    symbol: str,
    economic_end: pd.Timestamp,
) -> pd.Series:
    clean = series.astype(float).dropna().copy()
    clean.index = pd.to_datetime(clean.index)
    clean = clean.sort_index().loc[:economic_end]
    if clean.empty or not clean.index.is_unique or (clean <= 0.0).any():
        raise ValueError(f"invalid bounded attribution prices: {symbol}")
    return clean


def post_exit_diagnostics(
    *,
    exits: Sequence[ExitRecord],
    prices: Mapping[str, pd.Series],
    economic_end: str,
    horizons: Sequence[int] = (5, 10, 20, 40),
) -> list[dict[str, Any]]:
    """Measure post-exit paths after slicing every input at ``economic_end``."""

    try:
        end = pd.Timestamp(date_type.fromisoformat(economic_end))
    except (TypeError, ValueError) as exc:
        raise ValueError("post-exit economic_end must be an ISO date") from exc
    requested = tuple(sorted(set(horizons)))
    if not requested or requested[0] <= 0:
        raise ValueError("post-exit horizons must be positive")
    bounded = {
        symbol: _bounded_price_series(series, symbol=symbol, economic_end=end)
        for symbol, series in prices.items()
    }
    output: list[dict[str, Any]] = []
    for record in sorted(exits, key=lambda item: (item.exit_date, item.symbol)):
        if pd.Timestamp(record.exit_date) > end:
            raise ValueError("exit diagnostic lies after economic_end")
        series = bounded.get(record.symbol)
        if series is None:
            raise ValueError(f"missing bounded attribution prices: {record.symbol}")
        exit_date = pd.Timestamp(record.exit_date)
        if exit_date not in series.index:
            raise ValueError(f"exit date is not an observed session: {record.symbol}")
        location = int(series.index.get_indexer(pd.DatetimeIndex([exit_date]))[0])
        values: dict[str, Any] = {}
        for horizon in requested:
            future = location + horizon
            if future >= len(series):
                values[str(horizon)] = None
                continue
            absolute = float(series.iloc[future] / record.exit_price - 1.0)
            values[str(horizon)] = {
                "absolute_return": absolute,
                "avoided_loss": max(0.0, -absolute),
                "regret": max(0.0, absolute),
            }
        output.append(
            {
                "symbol": record.symbol,
                "exit_date": record.exit_date,
                "economic_end": economic_end,
                "origin_subsystem": record.origin_subsystem,
                "mechanism": record.mechanism,
                "horizons": values,
            }
        )
    return output
