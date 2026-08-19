"""Causal, equal-subindustry Sentinel evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

from .models import RISK_FAMILIES, SubindustryEvidence

_FAST_SESSIONS: Final = 5
_MA_SESSIONS: Final = 20
_MINIMUM_HISTORY: Final = 21


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    """One causal evidence snapshot with one vote per risk family."""

    date: str
    subindustries: tuple[SubindustryEvidence, ...]
    metrics: dict[str, float]
    family_votes: dict[str, bool]
    family_reasons: dict[str, str]
    first_evidence_date: str | None

    @property
    def families(self) -> tuple[str, ...]:
        """Return triggered family names in canonical order."""

        return tuple(sorted(family for family, triggered in self.family_votes.items() if triggered))

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON fields."""

        return {
            "date": self.date,
            "subindustries": [item.to_dict() for item in self.subindustries],
            "metrics": dict(sorted(self.metrics.items())),
            "family_votes": dict(sorted(self.family_votes.items())),
            "family_reasons": dict(sorted(self.family_reasons.items())),
            "first_evidence_date": self.first_evidence_date,
        }


@dataclass(frozen=True, slots=True)
class _NameEvidence:
    fast_return: float
    downside: float
    below_ma20: float
    volatility_ratio: float


def _causal_close(frame: pd.DataFrame, point: pd.Timestamp) -> pd.Series:
    if not isinstance(frame.index, pd.DatetimeIndex) or "close" not in frame:
        raise ValueError("Sentinel frames require DatetimeIndex and close")
    values = pd.to_numeric(frame.loc[:point, "close"], errors="coerce").dropna()
    values = values[values > 0.0]
    return values.astype(float)


def _name_evidence(frame: pd.DataFrame, point: pd.Timestamp) -> _NameEvidence | None:
    close = _causal_close(frame, point)
    if len(close) < _MINIMUM_HISTORY or close.index[-1].normalize() != point:
        return None
    fast_return = float(close.iloc[-1] / close.iloc[-(_FAST_SESSIONS + 1)] - 1.0)
    below_ma20 = float(close.iloc[-1] < float(close.tail(_MA_SESSIONS).mean()))
    returns = close.pct_change(fill_method=None).dropna()
    recent = float(returns.tail(5).std(ddof=0))
    prior = float(returns.iloc[-20:-5].std(ddof=0)) if len(returns) >= 20 else 0.0
    if prior <= 1e-12:
        volatility_ratio = 3.0 if recent > 1e-12 else 1.0
    else:
        volatility_ratio = min(3.0, recent / prior)
    return _NameEvidence(
        fast_return=fast_return,
        downside=float(fast_return < 0.0),
        below_ma20=below_ma20,
        volatility_ratio=volatility_ratio,
    )


def _median_correlation(
    reference_panel: Mapping[str, pd.DataFrame],
    point: pd.Timestamp,
) -> float:
    series: dict[str, pd.Series] = {}
    for symbol in sorted(reference_panel):
        close = _causal_close(reference_panel[symbol], point)
        if len(close) >= _MINIMUM_HISTORY and close.index[-1].normalize() == point:
            series[symbol] = close.pct_change(fill_method=None).tail(20)
    if len(series) < 4:
        return 0.0
    returns = pd.DataFrame(series).dropna(how="all")
    correlation = returns.corr(min_periods=10)
    values = correlation.where(~np.eye(len(correlation), dtype=bool)).stack()
    return float(values.median()) if not values.empty else 0.0


def _index_return(frame: pd.DataFrame, point: pd.Timestamp, sessions: int) -> float:
    close = _causal_close(frame, point)
    if len(close) < sessions + 1 or close.index[-1].normalize() != point:
        return 0.0
    return float(close.iloc[-1] / close.iloc[-(sessions + 1)] - 1.0)


def _snapshot(
    *,
    point: pd.Timestamp,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    leader_symbols: tuple[str, ...],
    capital_drawdown: float | None,
) -> tuple[
    tuple[SubindustryEvidence, ...],
    dict[str, float],
    dict[str, bool],
    dict[str, str],
]:
    names: dict[str, _NameEvidence] = {}
    grouped: dict[str, list[_NameEvidence]] = {}
    for symbol in sorted(reference_panel):
        item = _name_evidence(reference_panel[symbol], point)
        industry = point_in_time_industries.get(symbol, "unknown")
        if item is None or industry == "unknown":
            continue
        names[symbol] = item
        grouped.setdefault(industry, []).append(item)
    subindustries = tuple(
        SubindustryEvidence(
            industry=industry,
            member_count=len(members),
            fast_return=float(np.median([item.fast_return for item in members])),
            downside_breadth=float(np.mean([item.downside for item in members])),
            below_ma20=float(np.mean([item.below_ma20 for item in members])),
            volatility_ratio=float(np.median([item.volatility_ratio for item in members])),
        )
        for industry, members in sorted(grouped.items())
    )
    equal_fast = (
        float(np.mean([item.fast_return for item in subindustries]))
        if subindustries
        else 0.0
    )
    name_fast = float(np.mean([item.fast_return for item in names.values()])) if names else 0.0
    equal_downside = (
        float(np.mean([item.downside_breadth for item in subindustries]))
        if subindustries
        else 0.0
    )
    equal_below = (
        float(np.mean([item.below_ma20 for item in subindustries]))
        if subindustries
        else 0.0
    )
    synchronized = (
        float(
            np.mean(
                [
                    item.fast_return <= -0.025
                    and item.downside_breadth >= 0.60
                    and item.below_ma20 >= 0.60
                    for item in subindustries
                ]
            )
        )
        if subindustries
        else 0.0
    )
    broad_fast = _index_return(broad_frame, point, 5)
    tech_fast = _index_return(tech_frame, point, 5)
    broad_medium = _index_return(broad_frame, point, 20)
    tech_medium = _index_return(tech_frame, point, 20)
    median_correlation = _median_correlation(reference_panel, point)
    volatility_ratio = (
        float(np.mean([item.volatility_ratio for item in subindustries]))
        if subindustries
        else 1.0
    )

    held = [names[symbol] for symbol in held_symbols if symbol in names]
    held_fast = float(np.mean([item.fast_return for item in held])) if held else 0.0
    held_downside = float(np.mean([item.downside for item in held])) if held else 0.0
    leaders = [names[symbol] for symbol in leader_symbols if symbol in names]
    leader_fast = float(np.mean([item.fast_return for item in leaders])) if leaders else 0.0
    leader_below = float(np.mean([item.below_ma20 for item in leaders])) if leaders else 0.0
    capital = 0.0 if capital_drawdown is None else float(capital_drawdown)
    if not math.isfinite(capital) or capital < 0.0:
        raise ValueError("Sentinel capital drawdown must be finite and nonnegative")

    metrics = {
        "broad_fast_return": broad_fast,
        "broad_medium_return": broad_medium,
        "capital_drawdown": capital,
        "equal_subindustry_below_ma20": equal_below,
        "equal_subindustry_downside_breadth": equal_downside,
        "equal_subindustry_fast_return": equal_fast,
        "held_downside_breadth": held_downside,
        "held_fast_return": held_fast,
        "index_relative_speed": tech_fast - broad_fast,
        "latest_visible_ordinal": float(point.toordinal()),
        "leader_below_ma20": leader_below,
        "leader_fast_return": leader_fast,
        "median_correlation": median_correlation,
        "name_weighted_fast_return": name_fast,
        "synchronized_subindustry_damage": synchronized,
        "tech_fast_return": tech_fast,
        "tech_medium_return": tech_medium,
        "volatility_ratio": volatility_ratio,
    }
    votes = {
        "market_velocity": bool(
            (broad_fast <= -0.025 and tech_fast <= -0.025)
            or min(broad_fast, tech_fast) <= -0.05
        ),
        "breadth_structure": bool(
            len(subindustries) >= 2
            and (
                synchronized >= 0.40
                or (
                    equal_fast <= -0.025
                    and equal_downside >= 0.65
                    and equal_below >= 0.65
                )
            )
        ),
        "covariance_stress": bool(
            median_correlation >= 0.70 and volatility_ratio >= 1.50
        ),
        "leadership_damage": bool(
            leaders and leader_fast <= -0.03 and leader_below >= 0.67
        ),
        "live_book_damage": bool(
            held and held_fast <= -0.03 and held_downside >= 0.67
        ),
        "capital_damage": bool(capital >= 0.08),
    }
    reasons = {
        "market_velocity": "dual indices show fast causal deterioration",
        "breadth_structure": "equal-subindustry breadth and MA20 structure deteriorated",
        "covariance_stress": "cross-member correlation and volatility expanded",
        "leadership_damage": "existing active leaders deteriorated together",
        "live_book_damage": "current holdings deteriorated together",
        "capital_damage": "existing account capital drawdown crossed the Sentinel line",
    }
    return subindustries, metrics, votes, reasons


def build_market_evidence(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    leader_symbols: tuple[str, ...] = (),
    capital_drawdown: float | None = None,
) -> MarketEvidence:
    """Build causal evidence; rows after `as_of` are never visible."""

    point = pd.Timestamp(as_of).normalize()
    subindustries, metrics, votes, reasons = _snapshot(
        point=point,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        reference_panel=reference_panel,
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
        leader_symbols=leader_symbols,
        capital_drawdown=capital_drawdown,
    )
    if set(votes) != RISK_FAMILIES:
        raise RuntimeError("Sentinel evidence family coverage is incomplete")
    current_families = {family for family, triggered in votes.items() if triggered}
    first: str | None = None
    if current_families:
        common = broad_frame.index.intersection(tech_frame.index)
        common = common[common <= point][-21:]
        for candidate in common:
            _, _, historical_votes, _ = _snapshot(
                point=pd.Timestamp(candidate).normalize(),
                broad_frame=broad_frame,
                tech_frame=tech_frame,
                reference_panel=reference_panel,
                point_in_time_industries=point_in_time_industries,
                held_symbols=held_symbols,
                leader_symbols=leader_symbols,
                capital_drawdown=capital_drawdown,
            )
            if any(historical_votes[family] for family in current_families):
                first = str(pd.Timestamp(candidate).date())
                break
    return MarketEvidence(
        date=str(point.date()),
        subindustries=subindustries,
        metrics=dict(sorted(metrics.items())),
        family_votes=dict(sorted(votes.items())),
        family_reasons={
            family: reasons[family]
            for family in sorted(current_families)
        },
        first_evidence_date=first,
    )
