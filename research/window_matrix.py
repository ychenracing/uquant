"""Pure, research-only contracts for the approved five-window evidence gate."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS

INITIAL_CASH = 2_000_000.0
COMPARISON_CONTRACT = {
    "initial_cash": INITIAL_CASH,
    "signal": "close_t",
    "execution": "next_tradable_open",
    "intraday_exit": False,
    "prelisting": "invisible until first observable row",
}
LOCKED_COMPETITOR_SOURCES = {
    "aquant": {
        "repository": "ychenracing/aquant",
        "commit": "3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
        "python_sha256": "0fdc39c40239e51b5c91024507bef1bed222cd83575e4d9f870b8ada2f73a50a",
    },
    "qwenquant": {
        "repository": "ychenracing/qwenquant",
        "commit": "0b3681e10b75425ad8600e75835677a6a125ed13",
        "python_sha256": "66fc531989e294990d40dae5f0c0ff867fe4e144ab2bae81863b42e7113c46c0",
    },
    "trade": {
        "repository": "ychenracing/trade",
        "commit": "cee1620f40af3af8f839e15db188a9e388a78dd0",
        "python_sha256": "03e33e1396ca31d61e724bcd9cf58971ae656134740eb8929313167aa8ed8597",
    },
}


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """Requested, canonical, and mechanically selected A1 interval."""

    name: str
    requested_start: str
    requested_end: str
    start: str
    end: str
    acute_start: str
    acute_end: str
    acute_reference_return: float


def _window_spec(
    name: str,
    requested_start: str,
    requested_end: str,
    acute_reference_return: float,
) -> WindowSpec:
    """Combine research-only requested bounds with the production calendar."""

    start, end = AI_ERA_WINDOWS[name]
    acute_start, acute_end = AI_ERA_ACUTE_WINDOWS[name]
    return WindowSpec(
        name,
        requested_start,
        requested_end,
        start,
        end,
        acute_start,
        acute_end,
        acute_reference_return,
    )


WINDOW_SPECS: tuple[WindowSpec, ...] = (
    _window_spec(
        "h1_2023",
        "2023-01-01",
        "2023-07-01",
        -0.14608642470490663,
    ),
    _window_spec(
        "h2_2023",
        "2023-07-01",
        "2023-12-31",
        -0.0938807893408431,
    ),
    _window_spec(
        "h1_2024",
        "2024-01-01",
        "2024-07-01",
        -0.20140594031058867,
    ),
    _window_spec(
        "h2_2024",
        "2024-07-01",
        "2024-12-31",
        -0.09769488741717602,
    ),
    _window_spec(
        "bull_crash_2025_2026",
        "2025-01-01",
        "2026-08-01",
        -0.3121450300327826,
    ),
)

WINDOWS: dict[str, tuple[str, str]] = {
    spec.name: (spec.start, spec.end) for spec in WINDOW_SPECS
}
ACUTE_WINDOWS: dict[str, tuple[str, str]] = {
    spec.name: (spec.acute_start, spec.acute_end) for spec in WINDOW_SPECS
}


def _normalized_sessions(values: pd.Index) -> pd.DatetimeIndex:
    sessions = pd.DatetimeIndex(pd.to_datetime(values)).normalize()
    if sessions.has_duplicates:
        raise RuntimeError("window calendar contains duplicate sessions")
    return sessions.sort_values()


def canonicalize_requested_interval(
    *,
    requested_start: str,
    requested_end: str,
    broad_sessions: pd.Index,
    tech_sessions: pd.Index,
) -> tuple[str, str]:
    """Map inclusive calendar bounds inward onto the common index calendar."""
    start = pd.Timestamp(requested_start).normalize()
    end = pd.Timestamp(requested_end).normalize()
    if start > end:
        raise RuntimeError("requested window starts after it ends")
    common = _normalized_sessions(broad_sessions).intersection(
        _normalized_sessions(tech_sessions),
        sort=True,
    )
    eligible = common[(common >= start) & (common <= end)]
    if eligible.empty:
        raise RuntimeError("requested window has no common trading session")
    return str(eligible[0].date()), str(eligible[-1].date())


def select_acute_window(
    *,
    close: pd.Series,
    start: str,
    end: str,
    horizon_sessions: int = 22,
) -> tuple[str, str, float]:
    """Select the earliest minimum fixed-session close return inside a window."""
    if horizon_sessions < 1:
        raise ValueError("acute horizon must be positive")
    if close.index.has_duplicates:
        raise RuntimeError("acute reference contains duplicate sessions")
    series = close.copy()
    series.index = pd.DatetimeIndex(pd.to_datetime(series.index)).normalize()
    series = series.sort_index().loc[pd.Timestamp(start) : pd.Timestamp(end)]
    values = pd.to_numeric(series, errors="coerce").astype(float)
    if len(values) <= horizon_sessions:
        raise RuntimeError("acute reference has insufficient target-window sessions")
    if not np.isfinite(values.to_numpy()).all() or bool((values <= 0).any()):
        raise RuntimeError("acute reference close values are invalid")
    returns = values / values.shift(horizon_sessions) - 1.0
    eligible = returns.dropna()
    if eligible.empty:
        raise RuntimeError("acute reference has no eligible return")
    minimum = float(eligible.min())
    acute_end = eligible.index[eligible == minimum][0]
    end_position = values.index.get_loc(acute_end)
    if not isinstance(end_position, int):
        raise RuntimeError("acute reference session lookup is ambiguous")
    acute_start = values.index[end_position - horizon_sessions]
    return str(acute_start.date()), str(acute_end.date()), minimum
