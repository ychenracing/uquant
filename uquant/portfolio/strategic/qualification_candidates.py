"""Strategic qualification candidate and route ranking stages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from ...config import SystemConfig
from ...types import AccountState, LeaderScore, RiskAssessment


class StrategicQualificationPolicy(Protocol):
    @property
    def cfg(self) -> SystemConfig: ...


def reset_strategic_qualification_streaks(account: AccountState) -> None:
    """Invalidate the grant candidate while retaining unrelated absolute evidence."""

    if account.strategic_grant is not None:
        reset_strategic_candidate_eligibility(account=account, symbol=account.strategic_grant.candidate_symbol)

    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_qualification:"):
            account.replacement_tenure[key] = 0


def reset_strategic_candidate_eligibility(*, account: AccountState, symbol: str) -> None:
    """Require fresh local confirmation after an invalidated grant or completed exit."""
    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_eligibility:") and key.endswith(f":{symbol}"):
            account.replacement_tenure[key] = 0



def strategic_candidate_confirmation(*, account: AccountState, symbol: str, route: str) -> int:
    """Current absolute eligibility streak, never cohort or rank confirmation."""
    return account.replacement_tenure.get(f"strategic_eligibility:{route}:{symbol}", 0)


def observe_strategic_candidate_eligibility(
    *, date: pd.Timestamp, snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore], risk: RiskAssessment, account: AccountState,
    cfg: SystemConfig,
    independent_core_symbols: frozenset[str] = frozenset(),
) -> dict[str, dict[str, int]]:
    """Observe every absolute route predicate once per session, including during freezes."""
    session = date.toordinal()
    previous = account.candidate_tenure.get("strategic_eligibility_session", 0)
    if session < previous:
        raise ValueError("strategic eligibility observations must be causal")
    routes = ("established", "transition", "transition_impulse", "persistent_industry", "reversal_industry")
    current: dict[str, dict[str, int]] = {}
    eligible_keys: set[str] = set()
    for symbol in sorted(snapshots):
        for route in routes:
            if not strategic_candidate_meets_route(candidate_symbol=symbol, qualification_route=route,
                                                   snapshots={symbol: snapshots[symbol]}, leaders=leaders,
                                                   risk=risk, cfg=cfg):
                continue
            key = f"strategic_eligibility:{route}:{symbol}"
            eligible_keys.add(key)
            if session != previous:
                account.replacement_tenure[key] = account.replacement_tenure.get(key, 0) + 1
            current.setdefault(symbol, {})[route] = account.replacement_tenure.get(key, 0)
        if symbol in independent_core_symbols and symbol in current:
            key = f"strategic_eligibility:independent_core:{symbol}"
            eligible_keys.add(key)
            if session != previous:
                account.replacement_tenure[key] = account.replacement_tenure.get(key, 0) + 1
            current[symbol]["independent_core"] = account.replacement_tenure.get(key, 0)
    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_eligibility:") and key not in eligible_keys:
            account.replacement_tenure[key] = 0
    account.candidate_tenure["strategic_eligibility_session"] = session
    return current


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


@dataclass(frozen=True, slots=True)
class StrategicRoute:
    symbols: list[str]
    route: str
    decisive_reversal_symbol: str | None
    synchronized_reversal: bool
    reversal_groups: list[list[str]]
    anchor_state_observed: bool
    anchors_not_yet_armed: bool
    owner_symbol: str = ""


@dataclass(frozen=True, slots=True)
class QualifiedStrategicRoute:
    """One currently qualified production route ready for deployment checks."""

    symbols: list[str]
    route: str
    admission_state: str
    signature: str
    decisive_reversal_symbol: str | None
    admission_authorized: bool
    quorum_route: str
    restricted_initial_weight: float | None
    cash_rearm_authorized: bool


def decisive_reversal(
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


def established_route_durable(
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


def strategic_route_candidates(
    self: StrategicQualificationPolicy,
    *,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> tuple[StrategicRoute, ...]:
    """Enumerate absolute evidence without giving any route capital priority."""
    families = {
        "established": _established_candidates(self, snapshots, leaders),
        "transition": _transition_candidates(self, snapshots, leaders),
        "transition_impulse": _impulse_candidates(
            self, snapshots=snapshots, leaders=leaders, risk=risk),
        "persistent_industry": _persistent_candidates(self, snapshots, leaders),
        "reversal_industry": _reversal_candidates(self, snapshots, leaders),
    }
    anchor_observed = "risk_anchor_symbols" in risk.evidence
    anchors_not_armed = bool(anchor_observed and not risk.evidence.get("risk_anchor_symbols", []))
    choices: dict[tuple[str, str, tuple[str, ...]], StrategicRoute] = {}
    for route, candidates in families.items():
        for owner in candidates:
            peers = [symbol for symbol in candidates if leaders[symbol].industry == leaders[owner].industry]
            selected = {owner, *[symbol for symbol in peers if symbol != owner][:
                self.cfg.strategic_cohort_size - 1]}
            local_group = ([symbol for symbol in peers if symbol in selected]
                           if len(peers) >= self.cfg.strategic_cohort_min_size else [])
            synchronized = bool(
                route == "reversal_industry" and local_group
                and float(pd.Series([snapshots[s]["ret20"] for s in local_group[:2]]).median())
                >= self.cfg.strategic_reversal_min_median_ret20
                and float(risk.evidence.get("tech_ret120", math.inf)) <= self.cfg.strategic_reversal_max_tech_ret120
            )
            reversal_groups = [local_group] if route == "reversal_industry" and local_group else []
            decisive, decisive_pair = decisive_reversal(
                self, synchronized=synchronized, reversal_groups=reversal_groups,
                snapshots=snapshots, leaders=leaders, anchor_state_observed=anchor_observed,
            )
            witnesses = [[owner]]
            if local_group:
                witnesses.append(local_group)
            others = [symbol for symbol in candidates if symbol != owner]
            if route in {"established", "transition"} and others:
                witnesses.append([owner, others[0]])
            if route == "established" and len(candidates) >= self.cfg.strategic_cohort_min_size:
                witnesses.append([owner, *others[:self.cfg.strategic_cohort_size - 1]])
            if route == "reversal_industry" and anchor_observed and synchronized and decisive == owner:
                witnesses.append(decisive_pair)
            for symbols in witnesses:
                if route == "established" and not established_route_durable(
                    self, symbols=symbols, snapshots=snapshots, leaders=leaders,
                ):
                    continue
                key = (owner, route, tuple(sorted(symbols)))
                choices[key] = StrategicRoute(
                    list(symbols), route, decisive if decisive == owner else None, synchronized,
                    reversal_groups, anchor_observed, anchors_not_armed, owner,
                )
    return tuple(choices[key] for key in sorted(choices))


def strategic_candidate_meets_route(
    *,
    candidate_symbol: str,
    qualification_route: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> bool:
    """Check one grant candidate against its original route's absolute gates."""

    policy = _QualificationConfig(cfg)
    if candidate_symbol not in snapshots or candidate_symbol not in leaders:
        return False
    try:
        if qualification_route == "established":
            candidates = _established_candidates(policy, snapshots, leaders)
        elif qualification_route == "transition":
            candidates = _transition_candidates(policy, snapshots, leaders)
        elif qualification_route == "transition_impulse":
            candidates = _impulse_candidates(
                policy,
                snapshots=snapshots,
                leaders=leaders,
                risk=risk,
            )
        elif qualification_route == "persistent_industry":
            candidates = _persistent_candidates(policy, snapshots, leaders)
        elif qualification_route == "reversal_industry":
            candidates = _reversal_candidates(policy, snapshots, leaders)
        else:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return candidate_symbol in candidates


@dataclass(frozen=True, slots=True)
class _QualificationConfig:
    cfg: SystemConfig


def _strategic_candidate_meets_route(
    self: StrategicQualificationPolicy,
    *,
    candidate_symbol: str,
    qualification_route: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> bool:
    """Compatibility wrapper for the former policy-bound helper."""

    return strategic_candidate_meets_route(
        candidate_symbol=candidate_symbol,
        qualification_route=qualification_route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
        cfg=self.cfg,
    )


__all__ = (
    "StrategicRoute",
    "strategic_candidate_meets_route",
    "strategic_route_candidates",
)
