"""One causal, group-balanced reference view shared by market classifiers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import SystemConfig
from .contracts.universe import default_ai_universe
from .features import scalar
from .industry import compute_industry_signals


def production_reference_symbols() -> tuple[str, ...]:
    """Return the production reference membership from the canonical PIT manifest."""
    return default_ai_universe().symbols


@dataclass(frozen=True, slots=True)
class ReferenceContext:
    """Immutable point-in-time market evidence for one decision session."""

    date: pd.Timestamp
    visible_symbols: tuple[str, ...]
    expected_symbols: tuple[str, ...]
    visible_groups: tuple[str, ...]
    coverage: float
    name_breadth20: float
    group_breadth20: float
    breadth20: float
    name_breadth60: float
    group_breadth60: float
    breadth60: float
    name_declining: float
    group_declining: float
    declining: float
    sector_stress: float
    dispersion20: float
    median_correlation: float
    global_strength: float
    industry_strength: tuple[tuple[str, float], ...]
    details: tuple[tuple[str, float], ...]

    def evidence(self) -> dict[str, object]:
        """Return a JSON-ready copy without exposing mutable internal state."""
        return {
            "reference_visible_symbols": list(self.visible_symbols),
            "reference_expected_symbols": list(self.expected_symbols),
            "reference_visible_groups": list(self.visible_groups),
            "reference_coverage": self.coverage,
            "name_weighted_breadth20": self.name_breadth20,
            "group_balanced_breadth20": self.group_breadth20,
            "breadth20": self.breadth20,
            "name_weighted_breadth60": self.name_breadth60,
            "group_balanced_breadth60": self.group_breadth60,
            "breadth60": self.breadth60,
            "name_weighted_declining_ratio": self.name_declining,
            "group_balanced_declining_ratio": self.group_declining,
            "declining_ratio": self.declining,
            "sector_stress_ratio": self.sector_stress,
            "reference_dispersion20": self.dispersion20,
            "median_correlation": self.median_correlation,
            "reference_global_strength": self.global_strength,
            "reference_industry_strength": dict(self.industry_strength),
            "reference_details": dict(self.details),
        }


def _mean(values: list[float], default: float = 0.0) -> float:
    finite = [float(value) for value in values if math.isfinite(value)]
    return float(np.mean(finite)) if finite else default


def _build_reference_context_stage_1(
    *,
    cfg: Any,
    date: Any,
    industries: Any,
    panel: Any,
    reference_returns: Any,
    visible: Any,
) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    grouped: dict[str, list[str]] = {}
    raw: dict[str, dict[str, float]] = {}
    above20: list[float] = []
    above60: list[float] = []
    declining: list[float] = []
    ret20_values: list[float] = []
    for symbol in visible:
        row = panel[symbol].loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        ret5 = scalar(row, "ret5")
        ret20 = scalar(row, f"ret{cfg.trend_fast}")
        group = industries.get(symbol, "unknown")
        grouped.setdefault(group, []).append(symbol)
        item_above20 = float(math.isfinite(close) and math.isfinite(ma20) and close > ma20)
        item_above60 = float(math.isfinite(close) and math.isfinite(ma60) and close > ma60)
        item_declining = float(math.isfinite(ret5) and ret5 < 0.0)
        above20.append(item_above20)
        above60.append(item_above60)
        declining.append(item_declining)
        if math.isfinite(ret20):
            ret20_values.append(ret20)
        raw[symbol] = {
            "ret20": ret20,
            "ret60": scalar(row, f"ret{cfg.trend_medium}"),
            "ret120": scalar(row, f"ret{cfg.trend_slow}"),
            "above20": item_above20,
            "above60": item_above60,
            "accel": scalar(row, "mom_accel_5_20", 0.0),
        }

    group_above20 = [_mean([raw[symbol]["above20"] for symbol in symbols]) for symbols in grouped.values()]
    group_above60 = [_mean([raw[symbol]["above60"] for symbol in symbols]) for symbols in grouped.values()]
    group_declining = [
        _mean([float(scalar(panel[symbol].loc[date], "ret5") < 0.0) for symbol in symbols])
        for symbols in grouped.values()
    ]
    group_stress = [
        _mean([scalar(panel[symbol].loc[date], "ret5") for symbol in symbols]) < -0.04
        for symbols in grouped.values()
    ]
    name_breadth20 = _mean(above20)
    name_breadth60 = _mean(above60)
    name_declining = _mean(declining)
    balanced_breadth20 = _mean(group_above20)
    balanced_breadth60 = _mean(group_above60)
    balanced_declining = _mean(group_declining)
    name_weight = cfg.risk_breadth_name_weight
    breadth20 = name_weight * name_breadth20 + (1.0 - name_weight) * balanced_breadth20
    breadth60 = name_weight * name_breadth60 + (1.0 - name_weight) * balanced_breadth60
    declining_ratio = name_weight * name_declining + (1.0 - name_weight) * balanced_declining

    returns = (
        reference_returns.loc[:date].tail(max(61, cfg.correlation_window))
        if reference_returns is not None
        else pd.DataFrame(
            {symbol: panel[symbol].loc[:date, "close"].pct_change(fill_method=None) for symbol in visible}
        )
    )
    correlation = float("nan")
    return (
        balanced_breadth20,
        balanced_breadth60,
        balanced_declining,
        breadth20,
        breadth60,
        correlation,
        declining_ratio,
        group_stress,
        grouped,
        name_breadth20,
        name_breadth60,
        name_declining,
        raw,
        ret20_values,
        returns,
    )


def build_reference_context(
    *,
    date: pd.Timestamp,
    panel: Mapping[str, pd.DataFrame],
    industries: Mapping[str, str],
    cfg: SystemConfig,
    reference_returns: pd.DataFrame | None = None,
) -> ReferenceContext:
    """Build a single causal reference observation with capped group authority."""
    date = pd.Timestamp(date).normalize()
    expected = tuple(
        sorted(symbol for symbol, frame in panel.items() if not frame.empty and frame.index.min() <= date)
    )
    visible = tuple(sorted(symbol for symbol in expected if date in panel[symbol].index))
    (
        balanced_breadth20,
        balanced_breadth60,
        balanced_declining,
        breadth20,
        breadth60,
        correlation,
        declining_ratio,
        group_stress,
        grouped,
        name_breadth20,
        name_breadth60,
        name_declining,
        raw,
        ret20_values,
        returns,
    ) = _build_reference_context_stage_1(
        cfg=cfg,
        date=date,
        industries=industries,
        panel=panel,
        reference_returns=reference_returns,
        visible=visible,
    )
    if len(returns.columns) >= 4:
        stacked = (
            returns.tail(cfg.correlation_window)
            .corr()
            .where(~np.eye(len(returns.columns), dtype=bool))
            .stack()
        )
        if not stacked.empty:
            correlation = float(stacked.median())

    signals = compute_industry_signals(
        raw=raw,
        reference_symbols=visible,
        industries=industries,
        minimum_members=cfg.industry_signal_min_members,
        hierarchical=cfg.hierarchical_industry_shrinkage_enabled,
    )
    industry_strength = tuple(sorted((name, signal.score) for name, signal in signals.items()))
    global_strength = float(0.45 * breadth20 + 0.35 * breadth60 + 0.20 * (1.0 - declining_ratio))
    coverage = len(visible) / len(expected) if expected else 0.0
    return ReferenceContext(
        date=date,
        visible_symbols=visible,
        expected_symbols=expected,
        visible_groups=tuple(sorted(grouped)),
        coverage=coverage,
        name_breadth20=name_breadth20,
        group_breadth20=balanced_breadth20,
        breadth20=breadth20,
        name_breadth60=name_breadth60,
        group_breadth60=balanced_breadth60,
        breadth60=breadth60,
        name_declining=name_declining,
        group_declining=balanced_declining,
        declining=declining_ratio,
        sector_stress=_mean([float(value) for value in group_stress]),
        dispersion20=float(np.std(ret20_values, ddof=0)) if ret20_values else 0.0,
        median_correlation=correlation,
        global_strength=global_strength,
        industry_strength=industry_strength,
        details=(
            ("visible_count", float(len(visible))),
            ("expected_count", float(len(expected))),
            ("group_count", float(len(grouped))),
        ),
    )
