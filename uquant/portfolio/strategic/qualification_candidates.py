"""Strategic qualification candidate and route ranking stages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from ...config import SystemConfig
from ...types import LeaderScore, RiskAssessment


class StrategicQualificationPolicy(Protocol):
    cfg: SystemConfig


def _known_industry(
    self: StrategicQualificationPolicy,
    *,
    symbol: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> bool:
    return bool(
        symbol in leaders
        and leaders[symbol].components.get("unknown_industry", 1.0) < 0.5
        and snapshots[symbol]["industry_confidence"] >= self.cfg.unknown_industry_confidence
    )


def _established_candidates(
    self: StrategicQualificationPolicy,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> list[str]:
    return sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["secular_score"] >= self.cfg.strategic_secular_min_score
            and values["secular_confidence"] >= self.cfg.strategic_secular_min_confidence
            and values["ret20"] >= self.cfg.strategic_long_cycle_min_ret20
            and values["ret60"] >= self.cfg.strategic_long_cycle_min_ret60
            and values["ret120"] >= self.cfg.strategic_long_cycle_min_ret120
            and values["leader_score"] >= self.cfg.leader_mature_score
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["momentum60"] >= self.cfg.strategic_current_factor_floor
            and values["momentum120"] >= self.cfg.strategic_current_factor_floor
            and values["relative_strength"] >= self.cfg.strategic_current_factor_floor
            and values["trend_persistence"] >= 2 / 3
            and _known_industry(self, symbol=symbol, snapshots=snapshots, leaders=leaders)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["secular_score"],
            -snapshots[symbol]["secular_confidence"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["persistent_ret240"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )


def _transition_candidates(
    self: StrategicQualificationPolicy,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> list[str]:
    return sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["transition_score"] >= self.cfg.strategic_transition_min_score
            and values["leader_score"] >= self.cfg.leader_emerging_score
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["ret20"] > 0.0
            and values["ret60"] > 0.0
            and values["ret120"] > 0.0
            and values["relative_strength"] >= self.cfg.strategic_transition_min_component
            and values["breakout_quality"] >= self.cfg.strategic_transition_min_component
            and values["trend_persistence"] >= 2 / 3
            and _known_industry(self, symbol=symbol, snapshots=snapshots, leaders=leaders)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["transition_score"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["ret60"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )


def _impulse_candidates(
    self: StrategicQualificationPolicy,
    *,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> list[str]:
    return sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["history"] >= self.cfg.strategic_transition_impulse_min_history
            and values["transition_score"] >= self.cfg.strategic_transition_impulse_min_score
            and values["leader_score"] >= self.cfg.strategic_transition_impulse_min_leader_score
            and values["secular_score"] >= self.cfg.strategic_transition_impulse_min_secular_score
            and values["secular_confidence"] >= self.cfg.strategic_transition_impulse_min_secular_confidence
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["ret20"] >= self.cfg.strategic_transition_impulse_min_ret20
            and values["ret60"] >= self.cfg.strategic_transition_impulse_min_ret60
            and values["ret120"] >= self.cfg.strategic_transition_impulse_min_ret120
            and values["ret120"] <= self.cfg.strategic_transition_impulse_max_ret120
            and float(risk.evidence.get("broad_ret20", 0.0))
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and float(risk.evidence.get("tech_ret20", 0.0))
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and values["trend_persistence"] >= 2 / 3
            and _known_industry(self, symbol=symbol, snapshots=snapshots, leaders=leaders)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["transition_score"],
            -snapshots[symbol]["ret20"],
            -snapshots[symbol]["leader_score"],
            symbol,
        ),
    )


def _persistent_candidates(
    self: StrategicQualificationPolicy,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> list[str]:
    return sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            and values["ret120"] <= self.cfg.strategic_persistent_max_ret120
            and _known_industry(self, symbol=symbol, snapshots=snapshots, leaders=leaders)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["persistent_ret240"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )


def _reversal_candidates(
    self: StrategicQualificationPolicy,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> list[str]:
    return sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["ret240"] <= self.cfg.strategic_reversal_max_ret240
            and values["ret5"] >= self.cfg.strategic_reversal_min_ret5
            and _known_industry(self, symbol=symbol, snapshots=snapshots, leaders=leaders)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["ret20"],
            -snapshots[symbol]["ret5"],
            -snapshots[symbol]["leader_score"],
            symbol,
        ),
    )


def _synchronized_groups(
    self: StrategicQualificationPolicy,
    *,
    candidates: list[str],
    primary_component: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> list[list[str]]:
    by_industry: dict[str, list[str]] = {}
    for symbol in candidates:
        by_industry.setdefault(leaders[symbol].industry, []).append(symbol)
    groups = [
        symbols[: self.cfg.strategic_cohort_size]
        for symbols in by_industry.values()
        if len(symbols) >= self.cfg.strategic_cohort_min_size
    ]
    groups.sort(
        key=lambda symbols: (
            -float(pd.Series([snapshots[s][primary_component] for s in symbols]).median()),
            -float(pd.Series([snapshots[s]["leader_score"] for s in symbols]).median()),
            leaders[symbols[0]].industry,
        )
    )
    return groups


@dataclass(frozen=True, slots=True)
class StrategicRoute:
    symbols: list[str]
    route: str
    decisive_reversal_symbol: str | None
    synchronized_reversal: bool
    reversal_groups: list[list[str]]
    anchor_state_observed: bool
    anchors_not_yet_armed: bool


def _decisive_reversal(
    self: StrategicQualificationPolicy,
    *,
    synchronized: bool,
    reversal_groups: list[list[str]],
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    anchor_state_observed: bool,
) -> tuple[str | None, list[str]]:
    decisive: str | None = None
    pair: list[str] = []
    if anchor_state_observed and synchronized:
        pair = sorted(
            reversal_groups[0][:2],
            key=lambda symbol: (-leaders[symbol].score, symbol),
        )
        if len(pair) == 2:
            lead, runner = pair
            lead_evidence = snapshots[lead]
            runner_evidence = snapshots[runner]
            if (
                lead_evidence["leader_score"] - runner_evidence["leader_score"]
                >= self.cfg.strategic_dominant_min_leader_gap
                and lead_evidence["ret60"] - runner_evidence["ret60"]
                >= self.cfg.strategic_dominant_min_leader_gap
                and lead_evidence["leader_score"] >= self.cfg.strategic_secular_min_score
                and lead_evidence["trend_persistence"] >= 2 / 3
                and runner_evidence["trend_persistence"] < 2 / 3
                and lead_evidence["short_relative_strength"] >= self.cfg.strategic_transition_min_component
                and lead_evidence["breakout_quality"] >= self.cfg.strategic_transition_min_component
            ):
                decisive = lead
    return decisive, pair


def _established_route_durable(
    self: StrategicQualificationPolicy,
    *,
    symbols: list[str],
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
) -> bool:
    all_mature = all(leaders[symbol].mature for symbol in symbols if symbol in leaders)
    return bool(
        not all_mature
        or float(pd.Series([snapshots[s]["persistent_ret240"] for s in symbols]).median())
        >= self.cfg.strategic_established_min_median_ret240
    )


def select_strategic_route(
    self: StrategicQualificationPolicy,
    *,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> StrategicRoute:
    established = _established_candidates(self, snapshots, leaders)
    transition = _transition_candidates(self, snapshots, leaders)
    impulse = _impulse_candidates(self, snapshots=snapshots, leaders=leaders, risk=risk)
    persistent = _persistent_candidates(self, snapshots, leaders)
    reversal = _reversal_candidates(self, snapshots, leaders)
    high_quality_groups = _synchronized_groups(
        self,
        candidates=transition,
        primary_component="transition_score",
        snapshots=snapshots,
        leaders=leaders,
    )
    established_groups = _synchronized_groups(
        self,
        candidates=established,
        primary_component="secular_score",
        snapshots=snapshots,
        leaders=leaders,
    )
    impulse_groups = _synchronized_groups(
        self,
        candidates=impulse,
        primary_component="transition_score",
        snapshots=snapshots,
        leaders=leaders,
    )
    persistent_groups = _synchronized_groups(
        self,
        candidates=persistent,
        primary_component="persistent_ret240",
        snapshots=snapshots,
        leaders=leaders,
    )
    reversal_groups = _synchronized_groups(
        self,
        candidates=reversal,
        primary_component="ret20",
        snapshots=snapshots,
        leaders=leaders,
    )
    impulse_groups.sort(
        key=lambda symbols: (
            -float(pd.Series([snapshots[s]["ret20"] for s in symbols]).median()),
            -float(pd.Series([snapshots[s]["leader_score"] for s in symbols]).median()),
            leaders[symbols[0]].industry,
        )
    )
    synchronized = bool(
        reversal_groups
        and float(pd.Series([snapshots[s]["ret20"] for s in reversal_groups[0][:2]]).median())
        >= self.cfg.strategic_reversal_min_median_ret20
        and float(risk.evidence.get("tech_ret120", math.inf)) <= self.cfg.strategic_reversal_max_tech_ret120
    )
    anchor_observed = "risk_anchor_symbols" in risk.evidence
    anchors_not_armed = bool(anchor_observed and not risk.evidence.get("risk_anchor_symbols", []))
    decisive, decisive_pair = _decisive_reversal(
        self,
        synchronized=synchronized,
        reversal_groups=reversal_groups,
        snapshots=snapshots,
        leaders=leaders,
        anchor_state_observed=anchor_observed,
    )
    if decisive is not None:
        symbols, route = decisive_pair, "reversal_industry"
    elif persistent_groups:
        symbols, route = persistent_groups[0], "persistent_industry"
    elif high_quality_groups:
        symbols, route = high_quality_groups[0], "transition"
    elif established_groups:
        symbols, route = established_groups[0], "established"
    elif len(established) >= self.cfg.strategic_cohort_min_size:
        symbols, route = established[: self.cfg.strategic_cohort_size], "established"
    elif impulse_groups:
        symbols, route = impulse_groups[0], "transition_impulse"
    elif anchor_observed and synchronized:
        symbols, route = reversal_groups[0][:2], "reversal_industry"
    elif len(established) >= 2:
        symbols, route = established[:2], "established"
    elif established:
        symbols, route = established[:1], "established"
    elif len(transition) >= 2:
        symbols, route = transition[:2], "transition"
    else:
        symbols, route = [], "none"
    if route == "established" and not _established_route_durable(
        self,
        symbols=symbols,
        snapshots=snapshots,
        leaders=leaders,
    ):
        symbols, route = [], "none"
    return StrategicRoute(
        symbols,
        route,
        decisive,
        synchronized,
        reversal_groups,
        anchor_observed,
        anchors_not_armed,
    )


def _strategic_candidate_meets_route(
    self: StrategicQualificationPolicy,
    *,
    candidate_symbol: str,
    qualification_route: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> bool:
    """Check one grant candidate against its original route's absolute gates."""

    if candidate_symbol not in snapshots or candidate_symbol not in leaders:
        return False
    if qualification_route == "established":
        candidates = _established_candidates(self, snapshots, leaders)
    elif qualification_route == "transition":
        candidates = _transition_candidates(self, snapshots, leaders)
    elif qualification_route == "transition_impulse":
        candidates = _impulse_candidates(
            self,
            snapshots=snapshots,
            leaders=leaders,
            risk=risk,
        )
    elif qualification_route == "persistent_industry":
        candidates = _persistent_candidates(self, snapshots, leaders)
    elif qualification_route == "reversal_industry":
        candidates = _reversal_candidates(self, snapshots, leaders)
    else:
        return False
    return candidate_symbol in candidates


__all__ = (
    "StrategicRoute",
    "select_strategic_route",
)
