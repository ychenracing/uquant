"""Causal daily feature construction shared by daily decisions and replay."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig


def _wilder(values: pd.Series, window: int) -> pd.Series:
    return values.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def compute_features(frame: pd.DataFrame, cfg: SystemConfig) -> pd.DataFrame:
    """Build lagged trend, momentum, volatility, breakout, and breadth inputs."""
    out = frame.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat([(high - low).abs(), (high - previous).abs(), (low - previous).abs()], axis=1).max(
        axis=1
    )
    out["atr"] = _wilder(true_range, cfg.atr_window)
    for window in (5, 10, cfg.trend_fast, cfg.trend_medium, cfg.trend_slow):
        out[f"ret{window}"] = close.pct_change(window, fill_method=None)
    for window in (cfg.trend_fast, cfg.trend_medium, cfg.trend_slow):
        out[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    out["hhv"] = high.shift(1).rolling(cfg.breakout_window, min_periods=cfg.breakout_window).max()
    out["breakout"] = close / out["hhv"] - 1.0
    out["vol20"] = close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=0)
    out["volume_expansion"] = out["volume"] / out["volume"].rolling(20, min_periods=10).mean()
    slope_base = out[f"ma{cfg.trend_fast}"].shift(10)
    out["trend_slope"] = out[f"ma{cfg.trend_fast}"] / slope_base - 1.0
    out["mom_accel_5_20"] = out["ret5"] - out[f"ret{cfg.trend_fast}"]
    out["mom_accel_20_60"] = out[f"ret{cfg.trend_fast}"] - out[f"ret{cfg.trend_medium}"]

    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=out.index)
    atr = _wilder(true_range, 14).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, 14) / atr
    minus_di = 100.0 * _wilder(minus_dm, 14) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    out["adx"] = _wilder(dx, 14)
    rolling_peak = close.rolling(120, min_periods=20).max()
    out["drawdown120"] = close / rolling_peak - 1.0
    return out.replace([np.inf, -np.inf], np.nan)


def scalar(row: pd.Series, name: str, default: float = float("nan")) -> float:
    """Read one finite numeric feature or return the supplied fallback."""
    value = row.get(name, default)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def cross_section_returns(panel: dict[str, pd.DataFrame], date: pd.Timestamp) -> pd.DataFrame:
    """Return the recent point-in-time return panel used for correlations."""
    series: dict[str, pd.Series] = {}
    for symbol, frame in panel.items():
        bounded = frame.loc[:date, "close"].tail(61).pct_change(fill_method=None).dropna()
        if len(bounded) >= 20:
            series[symbol] = bounded
    return pd.DataFrame(series).dropna(how="all")
