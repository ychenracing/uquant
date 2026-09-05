"""Causal discovery and qualification of strategic cohorts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pandas as pd

from ...models.strategic_epoch import (
    settle_account_strategic_epoch,
)
from ...models.strategic_grant import (
    StrategicGrantStatus,
    StrategicQualificationObservation,
)
from ...models.strategic_universe import (
    StrategicUniverseRoles,
    build_strategic_universe_roles,
)
from ...portfolio_core import PortfolioCore
from ...types import (
    AccountState,
    LeaderScore,
    RiskAssessment,
    Target,
)
from .ownership import (
    activate_strategic_cohort,
    release_expired_strategic_deployment,
)
from .qualification_candidates import (
    QualifiedStrategicRoute,
    StrategicRoute,
    observe_strategic_candidate_eligibility,
    strategic_candidate_confirmation,
    strategic_route_candidates,
)
from .qualification_candidates import (
    reset_strategic_qualification_streaks as _reset_strategic_qualification_streaks,
)
from .quorum import (
    StrategicQuorumResult,
    StrategicQuorumRoute,
    evaluate_strategic_quorum,
    strict_absolute_owner_quality,
)
from .rearm import (
    observe_flat_book_capital_repair_state,
    observe_strategic_cash_rearm,
    strategic_cash_rearm_grant_open,
)


class StrategicPortfolioPolicy(PortfolioCore):
    """Discover, protect, trail, and retire a causal strategic cohort."""

    if TYPE_CHECKING:

        def _bounded_strategic_restore_risk_open(
            self, *, risk: RiskAssessment, account: AccountState
        ) -> bool: ...

        @staticmethod
        def _retire_strategic_member(account: AccountState, symbol: str) -> None: ...

        def _initialize_strategic_cohort(
            self,
            *,
            date: pd.Timestamp,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            risk: RiskAssessment,
            admission_open: bool = True,
            qualification_panel: dict[str, pd.DataFrame] | None = None,
            qualification_leaders: dict[str, LeaderScore] | None = None,
            strategic_universe: StrategicUniverseRoles | None = None,
        ) -> None: ...

        def _strategic_cohort_targets(
            self,
            *,
            date: pd.Timestamp,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            prices: dict[str, float],
            weights_now: dict[str, float],
            admission_open: bool = True,
            qualification_panel: dict[str, pd.DataFrame] | None = None,
            qualification_leaders: dict[str, LeaderScore] | None = None,
            strategic_universe: StrategicUniverseRoles | None = None,
        ) -> tuple[Target, ...] | None: ...

    def _strategic_qualification_snapshots(
        self,
        *,
        date: pd.Timestamp,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
    ) -> dict[str, dict[str, float]]:
        return strategic_qualification_snapshots(
            self,
            date=date,
            user_panel=user_panel,
            leaders=leaders,
        )


StrategicPortfolioPolicy.__module__ = "uquant.portfolio_strategic"


def _strategic_discovery_open(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    risk: RiskAssessment,
) -> bool:
    """Record that qualification observation ran for the visible session."""

    del date, user_panel, risk
    qualification_key = "strategic_cohort_qualification"
    long_cycle_open_key = "strategic_long_cycle_open"
    account.candidate_tenure["strategic_long_cycle_initial_check"] = 1
    account.candidate_tenure.setdefault(qualification_key, 0)
    account.candidate_tenure.setdefault(long_cycle_open_key, 0)
    return True


def strategic_qualification_evidence_sha256(
    *,
    date: pd.Timestamp,
    route: StrategicRoute,
    symbols: list[str],
    signature: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> str:
    def finite_payload(values: dict[str, float]) -> dict[str, str]:
        return {
            key: float(value).hex()
            for key, value in sorted(values.items())
            if math.isfinite(float(value))
        }

    payload = {
        "candidate_snapshots": {
            symbol: finite_payload(snapshots[symbol]) for symbol in sorted(symbols)
        },
        "leaders": {
            symbol: {
                "confidence": float(leaders[symbol].confidence).hex(),
                "industry": leaders[symbol].industry,
                "score": float(leaders[symbol].score).hex(),
            }
            for symbol in sorted(symbols)
            if symbol in leaders
        },
        "market_confirmation": {
            key: risk.evidence.get(key)
            for key in (
                "breadth20",
                "broad_ret20",
                "broad_ret120",
                "risk_anchor_group_count",
                "tech_ret20",
                "tech_ret120",
            )
        },
        "route": route.route,
        "session": str(date.date()),
        "signature": signature,
        "symbols": sorted(symbols),
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def strategic_candidate_symbol(
    *,
    route: StrategicRoute,
    symbols: list[str],
    leaders: dict[str, LeaderScore],
) -> str:
    if route.owner_symbol:
        if route.owner_symbol not in symbols:
            raise ValueError("strategic route owner is outside its witness set")
        return route.owner_symbol
    if route.decisive_reversal_symbol in symbols:
        return str(route.decisive_reversal_symbol)
    return min(symbols, key=lambda symbol: (-leaders[symbol].score, symbol))


def strategic_quorum_candidate_symbols(
    *,
    route: StrategicRoute,
    route_symbols: list[str],
) -> tuple[str, ...]:
    """Use a synchronized witness group without granting it target authority."""

    route_set = set(route_symbols)
    witness_groups = [
        tuple(group)
        for group in route.reversal_groups
        if route_set <= set(group)
    ]
    if route.synchronized_reversal and witness_groups:
        return min(
            witness_groups,
            key=lambda group: (-len(group), tuple(sorted(group))),
        )
    return tuple(route_symbols)


def strategic_deployment_block_reason(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    cash_rearm_authorized: bool = False,
) -> str:
    cash_rearm_open = bool(
        cash_rearm_authorized
        or strategic_cash_rearm_grant_open(
            account=account,
            risk=risk,
            cfg=self.cfg,
        )
    )
    risk_block = _strategic_risk_deployment_block(
        risk=risk,
        account=account,
        cash_rearm_open=cash_rearm_open,
    )
    if risk_block:
        return risk_block
    if not admission_open and not cash_rearm_open:
        return "opportunity_not_deployable"
    return ""


def _strategic_risk_deployment_block(
    *,
    risk: RiskAssessment,
    account: AccountState,
    cash_rearm_open: bool,
) -> str:
    if risk.state.value == "RISK_OFF":
        return "risk_off"
    if risk.state.value == "CRISIS":
        return "crisis"
    if (
        risk.freeze_new_risk
        or bool(risk.evidence.get("freeze_new_risk", False))
    ) and not cash_rearm_open:
        return "freeze_new_risk"
    if risk.state.value == "CAUTION" and risk.votes >= 2:
        return "risk_caution"
    if risk.target_gross_cap <= 0.0:
        return "target_gross_cap"
    if account.capital_budget_level > 0 and not cash_rearm_open:
        return "capital_budget"
    if account.chronic_level > 0 and not cash_rearm_open:
        return "chronic_damage"
    return ""


def strategic_qualification_snapshots(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
) -> dict[str, dict[str, float]]:
    snapshots: dict[str, dict[str, float]] = {}
    for symbol, frame in user_panel.items():
        if date not in frame.index:
            continue
        history = frame.loc[:date, "close"].dropna()
        if (
            len(history) < 121
            or "amount" not in frame.columns
            or not self._liquidity_confirmed(frame, date)
        ):
            continue
        rolling240 = history / history.shift(240) - 1.0
        persistent = rolling240.dropna().tail(self.cfg.strategic_cohort_confirm_days)
        leader = leaders.get(symbol)
        components = leader.components if leader is not None else {}
        transition_score = (
            0.20 * components.get("short_relative_strength", 0.0)
            + 0.20 * components.get("breakout_quality", 0.0)
            + 0.15 * components.get("acceleration", 0.0)
            + 0.15 * components.get("momentum60", 0.0)
            + 0.10 * components.get("relative_strength", 0.0)
            + 0.10 * components.get("industry_rotation_strength", 0.0)
            + 0.10 * components.get("trend_persistence", 0.0)
        )
        snapshots[symbol] = {
            "history": float(len(history)),
            "ret240": float(rolling240.iloc[-1]) if not persistent.empty else -math.inf,
            "persistent_ret240": float(persistent.median()) if not persistent.empty else -math.inf,
            "ret20": float(history.iloc[-1] / history.iloc[-21] - 1.0),
            "ret5": float(history.iloc[-1] / history.iloc[-6] - 1.0),
            "ret60": float(history.iloc[-1] / history.iloc[-61] - 1.0),
            "ret120": float(history.iloc[-1] / history.iloc[-121] - 1.0),
            "leader_score": leader.score if leader is not None else 0.0,
            "leader_confidence": leader.confidence if leader is not None else 0.0,
            "secular_score": components.get("secular_score", 0.0),
            "secular_confidence": components.get("secular_confidence", 0.0),
            "industry_confidence": components.get("industry_inference_confidence", 0.0),
            "momentum60": components.get("momentum60", 0.0),
            "momentum120": components.get("momentum120", 0.0),
            "relative_strength": components.get("relative_strength", 0.0),
            "short_relative_strength": components.get("short_relative_strength", 0.0),
            "trend_persistence": components.get("trend_persistence", 0.0),
            "breakout_quality": components.get("breakout_quality", 0.0),
            "transition_score": transition_score,
            "liquidity_confirmation": 1.0,
        }
    return snapshots


def resolve_strategic_qualification_inputs(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    qualification_panel: dict[str, pd.DataFrame] | None,
    qualification_leaders: dict[str, LeaderScore] | None,
    strategic_universe: StrategicUniverseRoles | None,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, LeaderScore],
    StrategicUniverseRoles,
]:
    panel = qualification_panel if qualification_panel is not None else user_panel
    scores = qualification_leaders if qualification_leaders is not None else leaders
    universe = strategic_universe
    if universe is None:
        universe = build_strategic_universe_roles(
            as_of=str(date.date()),
            tradable_symbols=user_panel,
            qualification_reference_symbols=panel,
            risk_reference_symbols=(),
            industries={
                symbol: scores[symbol].industry if symbol in scores else "unknown"
                for symbol in panel
            },
            available_symbols=panel,
        )
    return panel, scores, universe



def _independent_market_confirmation(
    self: StrategicPortfolioPolicy,
    risk: RiskAssessment,
) -> bool:
    return bool(
        float(risk.evidence.get("breadth20", self.cfg.high_confidence_entry_breadth))
        >= self.cfg.high_confidence_entry_breadth
        and float(risk.evidence.get("broad_ret20", 0.0))
        >= self.cfg.strategic_transition_impulse_min_market_ret20
        and float(risk.evidence.get("tech_ret20", 0.0))
        >= self.cfg.strategic_transition_impulse_min_market_ret20
        and max(
            float(risk.evidence.get("broad_ret120", 0.0)),
            float(risk.evidence.get("tech_ret120", 0.0)),
        )
        > self.cfg.recovery_transition_weak_leg_ret120
        and max(
            float(
                risk.evidence.get(
                    "broad_ret120",
                    risk.evidence.get("tech_ret120", math.inf),
                )
            ),
            float(risk.evidence.get("tech_ret120", math.inf)),
        )
        <= self.cfg.strategic_long_cycle_max_tech_ret120
    )


def _strategic_cohort_quality(
    self: StrategicPortfolioPolicy,
    *,
    symbols: list[str],
    snapshots: dict[str, dict[str, float]],
    partial_supported: bool,
    synchronized_reversal: bool,
) -> bool:
    count = len(symbols)
    return bool(
        count >= 3
        or (
            count == 2
            and partial_supported
            and (
                synchronized_reversal
                or all(
                    snapshots[symbol]["leader_score"] >= self.cfg.strategic_two_name_min_score
                    for symbol in symbols
                )
            )
        )
        or (
            count == 1
            and partial_supported
            and snapshots[symbols[0]]["leader_score"] >= self.cfg.strategic_one_name_min_score
            and snapshots[symbols[0]]["secular_score"] >= self.cfg.strategic_one_name_min_secular_score
            and snapshots[symbols[0]]["leader_confidence"] >= self.cfg.leader_min_confidence
        )
    )


def _synchronized_before_anchor(
    self: StrategicPortfolioPolicy,
    *,
    route: StrategicRoute,
    symbols: list[str],
    industries: set[str],
    hard_persistent: bool,
    admission_state: str,
) -> bool:
    return bool(
        route.anchors_not_yet_armed
        and (hard_persistent or (route.route == "reversal_industry" and route.synchronized_reversal))
        and len(industries) == 1
        and (
            len(symbols) >= self.cfg.strategic_cohort_min_size
            or (admission_state == "EMERGING_SECULAR" and bool(route.reversal_groups))
        )
    )


def _negative_long_cycle_backed(
    *,
    route: StrategicRoute,
    symbols: list[str],
    snapshots: dict[str, dict[str, float]],
) -> bool:
    return bool(
        all(snapshots[symbol]["ret120"] > 0.0 for symbol in symbols)
        or route.anchor_state_observed
        or route.synchronized_reversal
        or route.route == "transition_impulse"
    )


def strategic_qualification_evidence(
    self: StrategicPortfolioPolicy,
    *,
    route: StrategicRoute,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
) -> tuple[bool, bool]:
    symbols = route.symbols
    admission_state = (
        "SECULAR"
        if route.route in {"established", "persistent_industry"}
        else "EMERGING_SECULAR"
        if route.route in {"transition", "transition_impulse", "reversal_industry"}
        else "NONE"
    )
    hard_persistent = bool(
        route.route == "persistent_industry"
        and symbols
        and all(
            snapshots[symbol]["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            for symbol in symbols
        )
    )
    industries = {
        leaders[symbol].industry
        for symbol in symbols
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    independent_risk_coverage = bool(
        int(risk.evidence.get("risk_anchor_group_count", self.cfg.strategic_cohort_min_size))
        >= self.cfg.strategic_cohort_min_size
    )
    synchronized_before_anchor = _synchronized_before_anchor(
        self,
        route=route,
        symbols=symbols,
        industries=industries,
        hard_persistent=hard_persistent,
        admission_state=admission_state,
    )
    negative_backed = _negative_long_cycle_backed(
        route=route,
        symbols=symbols,
        snapshots=snapshots,
    )
    quality = _strategic_cohort_quality(
        self,
        symbols=symbols,
        snapshots=snapshots,
        partial_supported=bool(route.synchronized_reversal),
        synchronized_reversal=route.synchronized_reversal,
    )
    raw = bool(
        quality
        and negative_backed
        and (route.route != "reversal_industry" or route.synchronized_reversal)
        and (
            synchronized_before_anchor
            or (independent_risk_coverage and _independent_market_confirmation(self, risk))
        )
    )
    return raw, synchronized_before_anchor


def strategic_route_signature(
    *,
    route: StrategicRoute,
    symbols: list[str],
    leaders: dict[str, LeaderScore],
) -> tuple[str, str]:
    admission_state = (
        "SECULAR"
        if route.route in {"established", "persistent_industry"}
        else "EMERGING_SECULAR"
        if route.route in {"transition", "transition_impulse", "reversal_industry"}
        else "NONE"
    )
    body = ",".join(
        f"{symbol}:{leaders[symbol].industry if symbol in leaders else 'unknown'}"
        for symbol in sorted(symbols)
    )
    return admission_state, f"strategic_qualification:{admission_state}:{body}:evidence={route.route}"


def strategic_route_admission_open(
    self: StrategicPortfolioPolicy,
    *,
    route: StrategicRoute,
    symbols: list[str],
    snapshots: dict[str, dict[str, float]],
    admission_open: bool,
    synchronized_before_anchor: bool,
) -> bool:
    hard_persistent = bool(
        route.route == "persistent_industry"
        and symbols
        and all(
            snapshots[symbol]["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            for symbol in symbols
        )
    )
    return bool(
        admission_open
        or (
            (hard_persistent or route.synchronized_reversal)
            and route.anchors_not_yet_armed
            and synchronized_before_anchor
        )
    )


def _strategic_route_quorum(
    self: StrategicPortfolioPolicy, *, route: StrategicRoute,
    snapshots: dict[str, dict[str, float]], leaders: dict[str, LeaderScore],
    risk: RiskAssessment, reference_snapshots: dict[str, dict[str, float]],
    strategic_universe: StrategicUniverseRoles,
) -> tuple[StrategicQuorumResult | None, bool]:
    """Assess one witness set without changing confirmation or deployment state."""
    legacy_raw, synchronized_before_anchor = strategic_qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    candidate = (
        strategic_candidate_symbol(route=route, symbols=route_symbols, leaders=leaders)
        if route_symbols
        else ""
    )
    quorum = evaluate_strategic_quorum(
        owner_symbol=candidate,
        candidate_symbols=strategic_quorum_candidate_symbols(
            route=route,
            route_symbols=route_symbols,
        ),
        snapshots=reference_snapshots,
        leaders=leaders,
        risk=risk,
        universe=strategic_universe,
        cfg=self.cfg,
        synchronized_full_cohort=legacy_raw,
    ) if candidate else None
    if quorum is not None and (
        not quorum.qualified
        or (quorum.route is StrategicQuorumRoute.FULL_COHORT and not legacy_raw)
    ):
        quorum = None
    return quorum, synchronized_before_anchor


def _route_confirmation(
    *, account: AccountState, candidate: str, route: str, quorum: StrategicQuorumResult,
) -> int:
    streak = strategic_candidate_confirmation(account=account, symbol=candidate, route=route)
    if quorum.route is StrategicQuorumRoute.ABSOLUTE_SINGLE:
        streak = min(streak, strategic_candidate_confirmation(
            account=account, symbol=candidate, route="independent_core"))
    return streak


def strategic_candidate_certificates(
    self: StrategicPortfolioPolicy, *, snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore], risk: RiskAssessment, account: AccountState,
    reference_snapshots: dict[str, dict[str, float]], strategic_universe: StrategicUniverseRoles,
) -> list[tuple[StrategicRoute, StrategicQuorumResult, int]]:
    """Read all current certificates without allocating or replacing an owner."""
    evaluated: list[tuple[tuple[int, int, float, int, str, str, tuple[str, ...]],
                          tuple[StrategicRoute, StrategicQuorumResult, int]]] = []
    for route in strategic_route_candidates(self, snapshots=snapshots, leaders=leaders, risk=risk):
        quorum, _ = _strategic_route_quorum(
            self, route=route, snapshots=snapshots, leaders=leaders, risk=risk,
            reference_snapshots=reference_snapshots, strategic_universe=strategic_universe,
        )
        if quorum is None:
            continue
        candidate = strategic_candidate_symbol(route=route, symbols=route.symbols, leaders=leaders)
        streak = _route_confirmation(account=account, candidate=candidate, route=route.route, quorum=quorum)
        witnesses = strategic_quorum_candidate_symbols(route=route, route_symbols=route.symbols)
        key = (-int(streak >= quorum.required_confirm_days),
               -int(route.decisive_reversal_symbol == candidate), -leaders[candidate].score,
               -len(witnesses), candidate, route.route, tuple(sorted(route.symbols)))
        evaluated.append((key, (route, quorum, streak)))
    return [certificate for _, certificate in sorted(evaluated, key=lambda item: item[0])]


def _select_qualified_strategic_route(
    self: StrategicPortfolioPolicy, *, snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore], risk: RiskAssessment, account: AccountState,
    reference_snapshots: dict[str, dict[str, float]], strategic_universe: StrategicUniverseRoles,
) -> StrategicRoute:
    evaluated = strategic_candidate_certificates(
        self, snapshots=snapshots, leaders=leaders, risk=risk, account=account,
        reference_snapshots=reference_snapshots, strategic_universe=strategic_universe,
    )
    return evaluated[0][0] if evaluated else StrategicRoute(
        [], "none", None, False, [], "risk_anchor_symbols" in risk.evidence, False)


def current_core_qualification(
    self: StrategicPortfolioPolicy, *, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore], account: AccountState, risk: RiskAssessment,
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> dict[str, dict[str, Any]]:
    """Expose confirmed evidence to the same cash book, never grant authority."""
    if not self.cfg.strategic_dynamic_enabled:
        return {}
    panel, scores, universe = resolve_strategic_qualification_inputs(
        date=date, user_panel=user_panel, leaders=leaders, qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders, strategic_universe=strategic_universe,
    )
    references = strategic_qualification_snapshots(
        self, date=date, user_panel={symbol: frame for symbol, frame in panel.items()
                                    if symbol in universe.available_symbols}, leaders=scores,
    )
    snapshots = {symbol: values for symbol, values in references.items() if symbol in user_panel and symbol in leaders}
    evidence: dict[str, dict[str, Any]] = {}
    for route, quorum, streak in strategic_candidate_certificates(
        self, snapshots=snapshots, leaders=scores, risk=risk, account=account,
        reference_snapshots=references, strategic_universe=universe,
    ):
        owner = strategic_candidate_symbol(route=route, symbols=route.symbols, leaders=scores)
        if streak < quorum.required_confirm_days or owner in evidence:
            continue
        _, signature = strategic_route_signature(route=route, symbols=route.symbols, leaders=scores)
        witnesses = strategic_quorum_candidate_symbols(route=route, route_symbols=route.symbols)
        evidence[owner] = {
            "block": "READY", "qualification_route": route.route, "qualification_quorum": quorum.route.value,
            "required_confirmation": quorum.required_confirm_days, "confirmations": {route.route: streak},
            "qualification_signature": signature, "witnesses": sorted(witnesses), "as_of": str(date.date()),
            "qualification_evidence_sha256": strategic_qualification_evidence_sha256(
                date=date, route=route, symbols=list(witnesses), signature=signature,
                snapshots=references, leaders=scores, risk=risk,
            ),
        }
    return evidence


def _qualify_strategic_route(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    route: StrategicRoute,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    reference_snapshots: dict[str, dict[str, float]],
    strategic_universe: StrategicUniverseRoles,
) -> QualifiedStrategicRoute | None:
    quorum, synchronized_before_anchor = _strategic_route_quorum(
        self, route=route, snapshots=snapshots, leaders=leaders, risk=risk,
        reference_snapshots=reference_snapshots, strategic_universe=strategic_universe,
    )
    route_symbols = list(route.symbols)
    candidate = strategic_candidate_symbol(route=route, symbols=route_symbols, leaders=leaders) if route_symbols else ""
    raw = quorum is not None
    symbols = route_symbols if raw else []
    account.candidate_tenure["strategic_long_cycle_open"] = int(raw)
    admission_state, signature = strategic_route_signature(
        route=route,
        symbols=symbols,
        leaders=leaders,
    )
    account.candidate_tenure["strategic_cohort_qualification"] = (
        _route_confirmation(account=account, candidate=candidate, route=route.route, quorum=quorum)
        if symbols and quorum is not None else 0
    )
    required_days = (
        quorum.required_confirm_days
        if quorum is not None
        else self.cfg.strategic_cohort_confirm_days
    )
    route_admission_open = strategic_route_admission_open(
        self,
        route=route,
        symbols=symbols,
        snapshots=snapshots,
        admission_open=admission_open,
        synchronized_before_anchor=synchronized_before_anchor,
    )
    if not symbols:
        _record_failed_strategic_qualification(
            date=date,
            account=account,
        )
        return None
    streak = account.candidate_tenure["strategic_cohort_qualification"]
    _record_ready_strategic_qualification(
        date=date,
        route=route,
        symbols=symbols,
        signature=signature,
        snapshots=snapshots,
        leaders=leaders,
        account=account,
        risk=risk,
        quorum=quorum,
        required_days=required_days,
    )
    if streak < required_days:
        return None
    return QualifiedStrategicRoute(
        symbols,
        route.route,
        admission_state,
        signature,
        route.decisive_reversal_symbol,
        route_admission_open,
        quorum.route.value if quorum is not None else StrategicQuorumRoute.NONE.value,
        None if quorum is None else quorum.restricted_initial_weight,
        False,
    )


def _record_failed_strategic_qualification(
    *,
    date: pd.Timestamp,
    account: AccountState,
) -> None:
    previous = account.strategic_qualification
    account.strategic_qualification = (
        StrategicQualificationObservation(
            candidate_symbol=previous.candidate_symbol,
            qualification_signature=previous.qualification_signature,
            qualification_route=previous.qualification_route,
            qualification_evidence_sha256=previous.qualification_evidence_sha256,
            qualification_ready=False,
            deployment_blocked=True,
            deployment_block_reason="qualification_not_ready",
            qualification_streak=0,
            qualification_last_observed_session=str(date.date()),
            candidate_invalidation_reason="absolute_qualification_failed",
        )
        if previous.candidate_symbol
        else StrategicQualificationObservation(
            deployment_blocked=True,
            deployment_block_reason="qualification_not_ready",
            candidate_invalidation_reason="absolute_qualification_failed",
        )
    )


def _record_ready_strategic_qualification(
    *,
    date: pd.Timestamp,
    route: StrategicRoute,
    symbols: list[str],
    signature: str,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    quorum: StrategicQuorumResult | None,
    required_days: int,
) -> None:
    candidate = strategic_candidate_symbol(
        route=route,
        symbols=symbols,
        leaders=leaders,
    )
    streak = account.candidate_tenure["strategic_cohort_qualification"]
    quorum_route = (
        quorum.route.value
        if quorum is not None
        else StrategicQuorumRoute.NONE.value
    )
    unavailable = (
        list(quorum.unavailable_references) if quorum is not None else []
    )
    evidence_status = (
        {
            "INDUSTRY_CONFIRMATION": (
                "CONFIRMED" if quorum.industry_confirmation else "FAILED"
            ),
            "MARKET_CONFIRMATION": (
                "CONFIRMED" if quorum.market_confirmation else "FAILED"
            ),
            "OWNER_ABSOLUTE_QUALITY": (
                "CONFIRMED" if quorum.owner_absolute_quality else "FAILED"
            ),
            "ROBUSTNESS_CONFIRMATION": (
                "CONFIRMED" if quorum.robustness_confirmation else "DEGRADED"
            ),
        }
        if quorum is not None
        else {}
    )
    account.strategic_qualification = StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=signature,
        qualification_route=route.route,
        qualification_evidence_sha256=strategic_qualification_evidence_sha256(
            date=date,
            route=route,
            symbols=symbols,
            signature=signature,
            snapshots=snapshots,
            leaders=leaders,
            risk=risk,
        ),
        qualification_ready=streak >= required_days,
        qualification_streak=streak,
        qualification_last_observed_session=str(date.date()),
        qualification_quorum=quorum_route,
        candidate_symbols=sorted(symbols),
        unavailable_reference_symbols=unavailable,
        evidence_family_status=evidence_status,
    )




def _observe_resolved_strategic_candidates(
    self: StrategicPortfolioPolicy, *, date: pd.Timestamp, account: AccountState,
    risk: RiskAssessment, panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore],
    universe: StrategicUniverseRoles,
) -> dict[str, dict[str, float]]:
    if account.candidate_tenure.get("strategic_repair_observed_session", 0) != date.toordinal():
        _observe_strategic_universe_and_repair(self, date=date, account=account, risk=risk, universe=universe)
        account.candidate_tenure["strategic_repair_observed_session"] = date.toordinal()
    panel = {symbol: frame for symbol, frame in panel.items() if symbol in universe.available_symbols}
    snapshots = strategic_qualification_snapshots(self, date=date, user_panel=panel, leaders=leaders)
    observe_strategic_candidate_eligibility(date=date, snapshots=snapshots, leaders=leaders,
                                           risk=risk, account=account, cfg=self.cfg,
                                           independent_core_symbols=frozenset(
                                               symbol for symbol in snapshots if strict_absolute_owner_quality(
                                                   symbol=symbol, snapshots=snapshots, leaders=leaders, cfg=self.cfg)))
    return snapshots


def observe_strategic_candidates(
    self: StrategicPortfolioPolicy, *, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore], account: AccountState, risk: RiskAssessment,
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> dict[str, dict[str, int]]:
    """Observe account repair and all candidates before any grant/owner early return."""
    panel, scores, universe = resolve_strategic_qualification_inputs(
        date=date, user_panel=user_panel, leaders=leaders, qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders, strategic_universe=strategic_universe,
    )
    snapshots = _observe_resolved_strategic_candidates(
        self, date=date, account=account, risk=risk, panel=panel, leaders=scores, universe=universe,
    )
    return observe_strategic_candidate_eligibility(date=date, snapshots=snapshots, leaders=scores,
                                                  risk=risk, account=account, cfg=self.cfg,
                                                  independent_core_symbols=frozenset(
                                                      symbol for symbol in snapshots if strict_absolute_owner_quality(
                                                          symbol=symbol, snapshots=snapshots, leaders=scores, cfg=self.cfg)))


def _initialize_strategic_cohort(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool = True,
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> None:
    """Discover and activate a persistent long-cycle cohort causally."""

    if not self.cfg.strategic_dynamic_enabled:
        return
    _settle_terminal_strategic_grant(account=account, date=date)
    resolved_panel, resolved_leaders, resolved_universe = resolve_strategic_qualification_inputs(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    reference_snapshots = _observe_resolved_strategic_candidates(
        self, date=date, account=account, risk=risk, panel=resolved_panel,
        leaders=resolved_leaders, universe=resolved_universe,
    )
    if account.active_strategic_epoch_id:
        return
    if account.candidate_tenure.get("strategic_cohort_active", 0) == 1:
        return
    if not _strategic_discovery_open(
        self,
        date=date,
        user_panel=user_panel,
        account=account,
        risk=risk,
    ):
        return
    snapshots = {
        symbol: values
        for symbol, values in reference_snapshots.items()
        if symbol in user_panel and symbol in resolved_leaders
    }
    if not snapshots:
        _record_unavailable_strategic_candidate(account=account, user_panel=user_panel)
        return
    route = _select_qualified_strategic_route(
        self, snapshots=snapshots, leaders=resolved_leaders, risk=risk, account=account,
        reference_snapshots=reference_snapshots, strategic_universe=resolved_universe,
    )
    qualified = _qualify_strategic_route(
        self,
        date=date,
        route=route,
        snapshots=snapshots,
        leaders=resolved_leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
        reference_snapshots=reference_snapshots,
        strategic_universe=resolved_universe,
    )
    qualified = _observe_strategic_deployment(
        self,
        date=date,
        user_panel=user_panel,
        leaders=resolved_leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
        reference_snapshots=reference_snapshots,
        universe=resolved_universe,
        qualified=qualified,
    )
    if qualified is None or account.strategic_qualification.deployment_blocked:
        return
    activate_strategic_cohort(
        self,
        qualified=qualified,
        snapshots=snapshots,
        leaders=resolved_leaders,
        account=account,
        date=date,
        risk=risk,
        user_panel=user_panel,
    )


def _settle_terminal_strategic_grant(
    *,
    account: AccountState,
    date: pd.Timestamp,
) -> None:
    grant = account.strategic_grant
    if (
        grant is not None
        and grant.status
        in {
            StrategicGrantStatus.EXPIRED.value,
            StrategicGrantStatus.CANCELLED.value,
        }
        and grant.epoch_id
    ):
        settled = settle_account_strategic_epoch(
            account,
            epoch_id=grant.epoch_id,
            closed_session=str(date.date()),
            close_reason=grant.expiry_reason or "strategic_grant_expired",
            expired=True,
        )
        if settled:
            release_expired_strategic_deployment(account)


def _observe_strategic_universe_and_repair(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
) -> None:
    account.strategic_tradable_universe_identity = universe.tradable_identity
    account.strategic_qualification_universe_identity = (
        universe.qualification_reference_identity
    )
    account.strategic_risk_universe_identity = universe.risk_reference_identity
    observe_flat_book_capital_repair_state(
        account=account,
        risk=risk,
        universe=universe,
        observed_session=str(date.date()),
        cfg=self.cfg,
    )


def _record_unavailable_strategic_candidate(
    *,
    account: AccountState,
    user_panel: dict[str, pd.DataFrame],
) -> None:
    previous = account.strategic_qualification
    candidate_temporarily_unavailable = bool(
        previous.candidate_symbol and previous.candidate_symbol in user_panel
    )
    if candidate_temporarily_unavailable:
        previous.deployment_blocked = True
        previous.deployment_block_reason = "candidate_not_tradable"
        return
    _reset_strategic_qualification_streaks(account)
    account.candidate_tenure["strategic_cohort_qualification"] = 0
    account.candidate_tenure["strategic_long_cycle_open"] = 0


def _observe_strategic_deployment(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    reference_snapshots: dict[str, dict[str, float]],
    universe: StrategicUniverseRoles,
    qualified: QualifiedStrategicRoute | None,
) -> QualifiedStrategicRoute | None:
    if account.strategic_qualification.candidate_symbol:
        block_reason = strategic_deployment_block_reason(
            self,
            date=date,
            user_panel=user_panel,
            account=account,
            risk=risk,
            admission_open=(qualified.admission_authorized if qualified is not None else admission_open),
                cash_rearm_authorized=False,
        )
        account.strategic_qualification.deployment_blocked = bool(block_reason)
        account.strategic_qualification.deployment_block_reason = block_reason
        cash_rearm_authorized = observe_strategic_cash_rearm(
            account=account,
            risk=risk,
            universe=universe,
            snapshots=reference_snapshots,
            leaders=leaders,
            candidate_symbol=account.strategic_qualification.candidate_symbol,
            qualification_ready=account.strategic_qualification.qualification_ready,
            observed_session=str(date.date()),
            previous_observed_session="",
            cfg=self.cfg,
        )
        if qualified is not None and cash_rearm_authorized:
            qualified = replace(qualified, cash_rearm_authorized=True)
            block_reason = strategic_deployment_block_reason(
                self,
                date=date,
                user_panel=user_panel,
                account=account,
                risk=risk,
                admission_open=qualified.admission_authorized,
                        cash_rearm_authorized=True,
            )
            account.strategic_qualification.deployment_blocked = bool(block_reason)
            account.strategic_qualification.deployment_block_reason = block_reason
    return qualified



initialize_strategic_cohort = _initialize_strategic_cohort
strategic_route_confirmation = _route_confirmation
