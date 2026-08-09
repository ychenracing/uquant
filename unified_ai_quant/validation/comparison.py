"""Common, auditable attribution metrics for new and frozen legacy replays."""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_TOKENS = (
    "risk",
    "drawdown",
    "stop",
    "shock",
    "hazard",
    "sector",
    "regime",
    "defensive",
    "crisis",
    "capital protection",
)


def normalize_symbol(value: str) -> str:
    symbol = str(value).lower()
    if symbol.startswith(("sh", "sz")):
        return symbol
    return ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol


def equity_series(row: dict[str, Any]) -> pd.Series:
    curve = row.get("equity_curve", [])
    series = pd.Series(
        {
            pd.Timestamp(item["date"]): float(item["equity"])
            for item in curve
            if item.get("date") is not None and item.get("equity") is not None
        },
        dtype=float,
    ).sort_index()
    if series.empty:
        raise RuntimeError("comparison row has no equity curve")
    return series


def bounded_performance(
    row: dict[str, Any], start: str, end: str
) -> dict[str, float]:
    series = equity_series(row).loc[pd.Timestamp(start) : pd.Timestamp(end)]
    if len(series) < 2:
        raise RuntimeError(f"equity curve has fewer than two rows in {start}..{end}")
    drawdown = series / series.cummax() - 1.0
    return {
        "return": float(series.iloc[-1] / series.iloc[0] - 1.0),
        "max_drawdown": float(-drawdown.min()),
    }


def is_risk_reason(reason: str) -> bool:
    normalized = reason.lower().replace("-", "_")
    return any(token in normalized for token in RISK_TOKENS)


def risk_action_dates(
    row: dict[str, Any], *, start: str, end: str
) -> list[pd.Timestamp]:
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end)
    dates: set[pd.Timestamp] = set()
    for event in row.get("risk_events", []):
        state = str(event.get("to", event.get("state", ""))).upper()
        if state not in {"RISK_OFF", "CRISIS"}:
            continue
        value = event.get("date", event.get("signal_date"))
        if value:
            date = pd.Timestamp(value).normalize()
            if lower <= date <= upper:
                dates.add(date)
    reductions = row.get("risk_reductions")
    if reductions is None:
        reductions = [
            fill
            for fill in row.get("fills", [])
            if str(fill.get("side", "")).upper() == "SELL"
            and is_risk_reason(str(fill.get("reason", "")))
        ]
    for fill in reductions:
        value = fill.get("signal_date") or fill.get("fill_date") or fill.get("date")
        if value and str(value).lower() not in {"none", "nan", "nat"}:
            date = pd.Timestamp(value).normalize()
            if lower <= date <= upper:
                dates.add(date)
    return sorted(dates)


def market_drawdown_target(
    data_dir: Path, *, start: str, end: str, threshold: float = 0.10
) -> tuple[pd.Timestamp | None, pd.DatetimeIndex]:
    frame = pd.read_csv(data_dir / "sh000682.csv", parse_dates=["date"]).set_index("date")
    close = frame.loc[pd.Timestamp(start) : pd.Timestamp(end), "close"]
    drawdown = close / close.cummax() - 1.0
    hits = drawdown.index[drawdown <= -threshold]
    return (pd.Timestamp(hits[0]) if len(hits) else None, close.index)


def lead_to_target(
    actions: Iterable[pd.Timestamp],
    target: pd.Timestamp | None,
    sessions: pd.DatetimeIndex,
    *,
    maximum_lookback: int = 60,
) -> int | None:
    if target is None or target not in sessions:
        return None
    target_index = int(sessions.get_loc(target))
    eligible: list[int] = []
    for action in actions:
        candidates = np.flatnonzero(sessions <= action)
        if not len(candidates):
            continue
        action_index = int(candidates[-1])
        lead = target_index - action_index
        if -maximum_lookback <= lead <= maximum_lookback:
            eligible.append(lead)
    return max(eligible) if eligible else -maximum_lookback


def false_risk_off_events(
    row: dict[str, Any],
    *,
    tech_close: pd.Series,
    horizon: int = 20,
    damage_threshold: float = 0.05,
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for event in row.get("risk_events", []):
        state = str(event.get("to", "")).upper()
        if state not in {"RISK_OFF", "CRISIS"}:
            continue
        date = pd.Timestamp(event["date"])
        prior = tech_close.loc[:date].tail(horizon + 1)
        future = tech_close.loc[date:].head(horizon + 1)
        if prior.empty or future.empty:
            continue
        reference_peak = float(prior.max())
        damage = 1.0 - float(future.min()) / reference_peak
        labels.append(
            {
                "date": str(date.date()),
                "state": state,
                "forward_or_existing_damage": damage,
                "false_positive": damage < damage_threshold,
            }
        )
    return labels


def false_risk_state_diagnostics(
    row: dict[str, Any],
    *,
    tech_close: pd.Series,
    horizon: int = 20,
    damage_threshold: float = 0.05,
) -> dict[str, Any]:
    """Label complete RISK_OFF/CRISIS segments and count false-positive days."""
    daily = [
        {
            "date": pd.Timestamp(item["date"]).normalize(),
            "state": str(item["state"]).upper(),
        }
        for item in row.get("daily_risk_states", [])
    ]
    risk_states = {"RISK_OFF", "CRISIS"}
    segments: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, item in enumerate(daily):
        active = item["state"] in risk_states
        if active and start_index is None:
            start_index = index
        if start_index is not None and (not active or index == len(daily) - 1):
            end_index = index - 1 if not active else index
            start = daily[start_index]["date"]
            end = daily[end_index]["date"]
            prior = tech_close.loc[:start].tail(horizon + 1)
            future = tech_close.loc[start:].head(horizon + 1)
            if not prior.empty and not future.empty:
                damage = 1.0 - float(future.min()) / float(prior.max())
                false_positive = damage < damage_threshold
                segments.append(
                    {
                        "start": str(start.date()),
                        "end": str(end.date()),
                        "days": end_index - start_index + 1,
                        "forward_or_existing_damage": damage,
                        "false_positive": false_positive,
                    }
                )
            start_index = None
    return {
        "false_positives": sum(
            bool(item["false_positive"]) for item in segments
        ),
        "false_positive_days": sum(
            int(item["days"])
            for item in segments
            if bool(item["false_positive"])
        ),
        "segments": segments,
    }


def _future_return(
    frame: pd.DataFrame,
    *,
    date: pd.Timestamp,
    entry_price: float,
    horizon: int,
) -> float | None:
    future = frame.loc[frame.index > date, "close"].head(horizon)
    if len(future) < horizon or entry_price <= 0:
        return None
    return float(future.iloc[-1] / entry_price - 1.0)


def mature_false_exit_regrets(
    row: dict[str, Any],
    *,
    data_dir: Path,
    mature_symbols: set[str],
    horizon: int = 20,
    as_of: str | pd.Timestamp | None = None,
) -> list[float]:
    frames: dict[str, pd.DataFrame] = {}
    regrets: list[float] = []
    for fill in row.get("fills", []):
        if str(fill.get("side", "")).upper() != "SELL":
            continue
        symbol = normalize_symbol(str(fill.get("symbol", "")))
        reason = str(fill.get("reason", ""))
        if symbol not in mature_symbols or is_risk_reason(reason):
            continue
        lifecycle = str(fill.get("lifecycle", "CORE")).upper()
        if lifecycle not in {"CORE", "ADD1", "ADD2", ""}:
            continue
        date_value = fill.get("fill_date") or fill.get("date")
        if not date_value:
            continue
        if symbol not in frames:
            frame = pd.read_csv(
                data_dir / f"{symbol}.csv", parse_dates=["date"]
            ).set_index("date")
            frames[symbol] = (
                frame.loc[: pd.Timestamp(as_of)] if as_of is not None else frame
            )
        regret = _future_return(
            frames[symbol],
            date=pd.Timestamp(date_value),
            entry_price=float(fill["price"]),
            horizon=horizon,
        )
        if regret is not None and math.isfinite(regret):
            regrets.append(regret)
    return regrets


def replacement_spreads(
    events: Iterable[dict[str, Any]],
    *,
    data_dir: Path,
    horizons: tuple[int, ...] = (20, 40),
    as_of: str | pd.Timestamp | None = None,
) -> dict[int, list[float]]:
    output = {horizon: [] for horizon in horizons}
    frames: dict[str, pd.DataFrame] = {}
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        old_value = event.get("old_symbol", event.get("old"))
        new_value = event.get("new_symbol", event.get("new"))
        date_value = event.get("signal_date", event.get("date"))
        if not old_value or not new_value or not date_value:
            continue
        old_symbol = normalize_symbol(str(old_value))
        new_symbol = normalize_symbol(str(new_value))
        date = pd.Timestamp(date_value)
        key = (str(date.date()), old_symbol, new_symbol)
        if key in seen:
            continue
        seen.add(key)
        for symbol in (old_symbol, new_symbol):
            if symbol not in frames:
                frame = pd.read_csv(
                    data_dir / f"{symbol}.csv", parse_dates=["date"]
                ).set_index("date")
                frames[symbol] = (
                    frame.loc[: pd.Timestamp(as_of)] if as_of is not None else frame
                )
        old_frame = frames[old_symbol]
        new_frame = frames[new_symbol]
        old_history = old_frame.loc[:date, "close"]
        new_history = new_frame.loc[:date, "close"]
        if old_history.empty or new_history.empty:
            continue
        old_price = float(event.get("old_close", old_history.iloc[-1]))
        new_price = float(event.get("new_close", new_history.iloc[-1]))
        for horizon in horizons:
            old_return = _future_return(
                old_frame, date=date, entry_price=old_price, horizon=horizon
            )
            new_return = _future_return(
                new_frame, date=date, entry_price=new_price, horizon=horizon
            )
            if old_return is not None and new_return is not None:
                output[horizon].append(new_return - old_return)
    return output


def recovery_capture(
    row: dict[str, Any],
    *,
    trough: pd.Timestamp,
    sessions: pd.DatetimeIndex,
    horizon: int = 60,
) -> float:
    curve = equity_series(row)
    trough_candidates = curve.index[curve.index <= trough]
    if not len(trough_candidates):
        raise RuntimeError("equity curve starts after recovery trough")
    trough_date = trough_candidates[-1]
    market_index = int(np.searchsorted(sessions.values, np.datetime64(trough), side="left"))
    end_index = min(len(sessions) - 1, market_index + horizon)
    end_date = sessions[end_index]
    end_candidates = curve.index[curve.index <= end_date]
    if not len(end_candidates):
        raise RuntimeError("equity curve has no recovery endpoint")
    return float(curve.loc[end_candidates[-1]] / curve.loc[trough_date] - 1.0)


def first_recovery_buy_date(
    row: dict[str, Any],
    *,
    trough: pd.Timestamp,
    sessions: pd.DatetimeIndex,
    horizon: int = 60,
) -> pd.Timestamp | None:
    """Return the first executable post-trough buy inside the recovery window."""
    market_index = int(
        np.searchsorted(sessions.values, np.datetime64(trough), side="left")
    )
    end_index = min(len(sessions) - 1, market_index + horizon)
    end_date = pd.Timestamp(sessions[end_index])
    dates: list[pd.Timestamp] = []
    for fill in row.get("fills", []):
        if str(fill.get("side", "")).upper() != "BUY":
            continue
        value = fill.get("fill_date") or fill.get("date")
        if not value:
            continue
        date = pd.Timestamp(value).normalize()
        if trough < date <= end_date:
            dates.append(date)
    return min(dates) if dates else None


def recovery_delay_opportunity_cost(
    row: dict[str, Any],
    *,
    comparable_rows: Iterable[dict[str, Any]],
    trough: pd.Timestamp,
    market_close: pd.Series,
    horizon: int = 60,
) -> dict[str, Any]:
    """Measure market return missed versus the earliest comparable re-entry.

    H1 is a delay gate, not a requirement to beat every legacy portfolio's
    subsequent security selection.  The cost is therefore the positive index
    move between the earliest old executable BUY and the new executable BUY.
    If the new system re-enters first, its delay cost is zero.  A missing new
    re-entry is measured through the end of the preregistered 60-session window.
    """
    sessions = pd.DatetimeIndex(market_close.index)
    comparable_dates = [
        date
        for comparable in comparable_rows
        if (
            date := first_recovery_buy_date(
                comparable,
                trough=trough,
                sessions=sessions,
                horizon=horizon,
            )
        )
        is not None
    ]
    if not comparable_dates:
        raise RuntimeError("no comparable post-trough recovery buy")
    benchmark_date = min(comparable_dates)
    new_date = first_recovery_buy_date(
        row, trough=trough, sessions=sessions, horizon=horizon
    )
    market_index = int(
        np.searchsorted(sessions.values, np.datetime64(trough), side="left")
    )
    end_index = min(len(sessions) - 1, market_index + horizon)
    measurement_date = new_date or pd.Timestamp(sessions[end_index])
    if measurement_date <= benchmark_date:
        cost = 0.0
    else:
        benchmark_price = float(market_close.loc[benchmark_date])
        measurement_price = float(market_close.loc[measurement_date])
        cost = max(0.0, measurement_price / benchmark_price - 1.0)
    return {
        "opportunity_cost": cost,
        "benchmark_date": str(benchmark_date.date()),
        "new_date": str(new_date.date()) if new_date is not None else None,
        "measurement_date": str(measurement_date.date()),
        "horizon_sessions": horizon,
    }
