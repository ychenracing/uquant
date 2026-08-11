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
    for window in (5, 10, cfg.trend_fast, cfg.trend_medium, cfg.trend_slow, 240):
        out[f"ret{window}"] = close.pct_change(window, fill_method=None)
    for window in (cfg.trend_fast, cfg.trend_medium, cfg.trend_slow):
        out[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    out["hhv"] = high.shift(1).rolling(cfg.breakout_window, min_periods=cfg.breakout_window).max()
    out["breakout"] = close / out["hhv"] - 1.0
    out["vol20"] = close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=0)
    out["volume_expansion"] = out["volume"] / out["volume"].rolling(20, min_periods=10).mean()
    slope_base = out[f"ma{cfg.trend_fast}"].shift(10)
    out["trend_slope"] = out[f"ma{cfg.trend_fast}"] / slope_base - 1.0
    out["ma60_slope"] = out[f"ma{cfg.trend_medium}"] / out[f"ma{cfg.trend_medium}"].shift(20) - 1.0
    out["ma120_slope"] = out[f"ma{cfg.trend_slow}"] / out[f"ma{cfg.trend_slow}"].shift(20) - 1.0
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
    rolling_peak240 = close.rolling(240, min_periods=120).max()
    rolling_low120 = close.rolling(120, min_periods=60).min()
    out["drawdown240"] = close / rolling_peak240 - 1.0
    out["recovery120"] = close / rolling_low120 - 1.0
    daily_return = close.pct_change(fill_method=None)
    path_length = daily_return.abs().rolling(120, min_periods=80).sum()
    out["trend_efficiency120"] = (out["ret120"].abs() / path_length.replace(0.0, np.nan)).clip(0.0, 1.0)
    out["above_ma60_persistence"] = (
        (close > out[f"ma{cfg.trend_medium}"]).astype(float).rolling(120, min_periods=60).mean()
    )
    downside = daily_return.where(daily_return < 0.0, 0.0)
    out["downside_vol120"] = downside.rolling(120, min_periods=60).std(ddof=0)
    # Squared correlation between log price and time is the linear-trend R².
    # A rolling correlation with a monotonic time index is equivalent to the
    # one-factor OLS statistic and remains strictly point-in-time.
    time = pd.Series(np.arange(len(out), dtype=float), index=out.index)
    log_close = pd.Series(
        np.log(close.where(close > 0.0).to_numpy(dtype=float)),
        index=out.index,
        dtype=float,
    )
    out["trend_r2_120"] = log_close.rolling(120, min_periods=80).corr(time).pow(2)
    return out.replace([np.inf, -np.inf], np.nan)


def scalar(
    row: pd.Series | pd.DataFrame,
    name: str,
    default: float = float("nan"),
) -> float:
    """Read one finite numeric feature or return the supplied fallback.

    Pandas types ``.loc[date]`` as a Series-or-DataFrame because a generic
    index may contain duplicates. Market-data validation guarantees unique
    sessions, but accepting the union here keeps that contract explicit and
    fails safely if an external caller still supplies duplicate rows.
    """
    if isinstance(row, pd.DataFrame):
        if row.empty:
            return default
        row = row.iloc[-1]
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
