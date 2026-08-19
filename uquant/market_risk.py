"""Pure point-in-time market evidence shared by base risk and Sentinel."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .config import SystemConfig

EVIDENCE_FAMILY_MEMBERS: dict[str, tuple[str, ...]] = {
    "market_velocity": ("index_velocity",),
    "breadth_structure": (
        "sector_breadth_shock",
        "below_ma20_structure",
        "multi_industry_sync",
    ),
    "covariance_stress": ("correlation_shock", "volatility_shock"),
    "leadership_damage": ("leader_failure", "anchor_break"),
    "live_book_damage": ("live_book_damage",),
    "capital_damage": ("capital_damage",),
}


def evidence_family_votes(indicators: Mapping[str, bool]) -> dict[str, bool]:
    """Cap correlated indicators at one vote per independent evidence family."""

    return {
        family: any(bool(indicators.get(member, False)) for member in members)
        for family, members in EVIDENCE_FAMILY_MEMBERS.items()
    }


@dataclass(frozen=True, slots=True)
class BaseMarketFamilySnapshot:
    """Account-free market evidence shared by base risk and causal history."""

    indicator_active: Mapping[str, bool]
    family_active: Mapping[str, bool]

    def with_leadership(self, *, leader_failure: bool) -> dict[str, bool]:
        """Restore the formal assessor's historical reason ordering."""

        indicators = dict(self.indicator_active)
        index_velocity = indicators.pop("index_velocity")
        indicators["leader_failure"] = leader_failure
        indicators["index_velocity"] = index_velocity
        return indicators


def build_base_market_family_snapshot(
    *,
    average_fast_return: float,
    declining_ratio: float,
    below_ma20_ratio: float,
    sector_stress_ratio: float,
    median_correlation: float,
    volatility_ratio: float,
    tech_speed: float,
    broad_speed: float,
    cfg: SystemConfig,
) -> BaseMarketFamilySnapshot:
    """Evaluate only point-in-time market families with production thresholds."""

    indicators = {
        "sector_breadth_shock": (
            average_fast_return <= cfg.risk_fast_return
            and declining_ratio >= cfg.risk_breadth
        ),
        "below_ma20_structure": below_ma20_ratio >= cfg.risk_below_ma20,
        "multi_industry_sync": sector_stress_ratio >= 0.50,
        "correlation_shock": (
            math.isfinite(median_correlation)
            and median_correlation >= cfg.risk_correlation
        ),
        "volatility_shock": volatility_ratio >= cfg.risk_volatility_ratio,
        "index_velocity": tech_speed <= -0.055 or broad_speed <= -0.045,
    }
    all_families = evidence_family_votes(indicators)
    families = {
        family: all_families[family]
        for family in (
            "breadth_structure",
            "covariance_stress",
            "market_velocity",
        )
    }
    return BaseMarketFamilySnapshot(
        indicator_active=MappingProxyType(indicators),
        family_active=MappingProxyType(families),
    )
