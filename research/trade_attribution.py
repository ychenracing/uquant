"""Offline exit-regret and avoided-loss attribution.

Future prices are consumed only after an exit has already been recorded. This
module must never be imported by the production decision path.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class ExitRecord:
    symbol: str
    exit_date: str
    exit_price: float
    reason_code: str
    lifecycle: str
    entry_cost: float
    mfe: float
    mae: float
    benchmark_symbol: str = ""

    def __post_init__(self) -> None:
        values = (self.exit_price, self.entry_cost, self.mfe, self.mae)
        if not self.symbol or not self.exit_date or not self.reason_code:
            raise ValueError("exit records need symbol, date, and reason_code")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("exit record metrics must be finite")
        if self.exit_price <= 0 or self.entry_cost <= 0:
            raise ValueError("exit and entry prices must be positive")


@dataclass(frozen=True, slots=True)
class ExitAttribution:
    record: ExitRecord
    post_exit_returns: tuple[tuple[int, float | None], ...]
    relative_returns: tuple[tuple[int, float | None], ...]
    avoided_loss: tuple[tuple[int, float | None], ...]
    regret: tuple[tuple[int, float | None], ...]


def _validated_series(series: pd.Series, *, symbol: str) -> pd.Series:
    clean = series.astype(float).dropna().copy()
    clean.index = pd.to_datetime(clean.index)
    clean = clean.sort_index()
    if clean.empty or not clean.index.is_unique or (clean <= 0).any():
        raise ValueError(f"invalid attribution price series: {symbol}")
    return clean


def _session_position(series: pd.Series, date: pd.Timestamp) -> int | None:
    position = int(series.index.searchsorted(date, side="left"))
    return position if position < len(series) else None


def attribute_exits(
    exits: Iterable[ExitRecord],
    prices: Mapping[str, pd.Series],
    *,
    horizons: Iterable[int] = (5, 10, 20, 40),
) -> tuple[ExitAttribution, ...]:
    """Measure what happened after each already-completed exit."""
    requested = tuple(sorted(set(horizons)))
    if not requested or requested[0] < 1:
        raise ValueError("attribution horizons must be positive")
    validated = {symbol: _validated_series(series, symbol=symbol) for symbol, series in prices.items()}
    output: list[ExitAttribution] = []
    for record in sorted(exits, key=lambda item: (item.exit_date, item.symbol)):
        series = validated.get(record.symbol)
        if series is None:
            raise ValueError(f"missing attribution prices for {record.symbol}")
        date = pd.Timestamp(record.exit_date)
        start = _session_position(series, date)
        benchmark = validated.get(record.benchmark_symbol) if record.benchmark_symbol else None
        benchmark_start = _session_position(benchmark, date) if benchmark is not None else None
        post: list[tuple[int, float | None]] = []
        relative: list[tuple[int, float | None]] = []
        avoided: list[tuple[int, float | None]] = []
        regret: list[tuple[int, float | None]] = []
        for horizon in requested:
            value: float | None = None
            relative_value: float | None = None
            if start is not None and start + horizon < len(series):
                value = float(series.iloc[start + horizon] / record.exit_price - 1.0)
                if (
                    benchmark is not None
                    and benchmark_start is not None
                    and benchmark_start + horizon < len(benchmark)
                ):
                    benchmark_return = float(
                        benchmark.iloc[benchmark_start + horizon] / benchmark.iloc[benchmark_start] - 1.0
                    )
                    relative_value = value - benchmark_return
            post.append((horizon, value))
            relative.append((horizon, relative_value))
            avoided.append((horizon, None if value is None else max(0.0, -value)))
            regret.append((horizon, None if value is None else max(0.0, value)))
        output.append(
            ExitAttribution(
                record=record,
                post_exit_returns=tuple(post),
                relative_returns=tuple(relative),
                avoided_loss=tuple(avoided),
                regret=tuple(regret),
            )
        )
    return tuple(output)


def aggregate_by_reason(
    attributions: Iterable[ExitAttribution],
) -> dict[str, dict[str, float | int]]:
    """Aggregate comparable horizons without treating missing data as zero."""
    counts: dict[str, int] = {}
    avoided_values: dict[str, dict[int, list[float]]] = {}
    regret_values: dict[str, dict[int, list[float]]] = {}
    relative_values: dict[str, dict[int, list[float]]] = {}
    for item in attributions:
        reason = item.record.reason_code
        counts[reason] = counts.get(reason, 0) + 1
        avoided_values.setdefault(reason, {})
        regret_values.setdefault(reason, {})
        relative_values.setdefault(reason, {})
        for horizon, value in item.avoided_loss:
            if value is not None:
                avoided_values[reason].setdefault(horizon, []).append(value)
        for horizon, value in item.regret:
            if value is not None:
                regret_values[reason].setdefault(horizon, []).append(value)
        for horizon, value in item.relative_returns:
            if value is not None:
                relative_values[reason].setdefault(horizon, []).append(value)
    result: dict[str, dict[str, float | int]] = {}
    for reason in sorted(counts):
        result[reason] = {"count": counts[reason]}
        metric_values_by_horizon = {
            "avoided_loss": avoided_values[reason],
            "regret": regret_values[reason],
            "relative_return": relative_values[reason],
        }
        for name, horizon_values in metric_values_by_horizon.items():
            values = [value for items in horizon_values.values() for value in items]
            result[reason][f"mean_{name}"] = float(sum(values) / len(values)) if values else 0.0
            for horizon, items in sorted(horizon_values.items()):
                result[reason][f"mean_{name}_{horizon}d"] = float(sum(items) / len(items))
        result[reason]["net_exit_value"] = float(result[reason]["mean_avoided_loss"]) - float(
            result[reason]["mean_regret"]
        )
    return result
