"""Causal discovery and qualification of strategic cohorts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from ...config import config_fingerprint
from ...models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    derive_strategic_epoch_id,
    settle_account_strategic_epoch,
)
from ...models.strategic_universe import (
    StrategicUniverseRoles,
    build_strategic_universe_roles,
)
from ...models.strategic_grant import (
    MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS,
    StrategicGrantIntent,
    StrategicGrantStatus,
    StrategicQualificationObservation,
    derive_strategic_grant_id,
)
from ...portfolio_core import PortfolioCore
from ...types import (
    AccountState,
    LeaderScore,
    RiskAssessment,
    Target,
)
from .qualification_candidates import (
    StrategicRoute,
    select_strategic_route,
)
from .quorum import (
    StrategicQuorumRoute,
    evaluate_strategic_quorum,
    strict_absolute_owner_quality,
)
from .rearm import (
    mark_strategic_cash_rearm_grant,
    observe_strategic_cash_rearm,
    set_strategic_cash_rearm_strict,
    strategic_cash_rearm_grant_open,
    strategic_cash_rearm_weight,
)
from .qualification_candidates import (
    _strategic_candidate_meets_route as _grant_candidate_meets_route,
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

        def _revalidate_strategic_grant(
            self,
            *,
            date: pd.Timestamp,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            risk: RiskAssessment,
            admission_open: bool,
            weights_now: dict[str, float],
            qualification_panel: dict[str, pd.DataFrame] | None = None,
            qualification_leaders: dict[str, LeaderScore] | None = None,
            strategic_universe: StrategicUniverseRoles | None = None,
        ) -> bool: ...

        def _observe_strategic_successor(
            self,
            *,
            date: pd.Timestamp,
            qualification_panel: dict[str, pd.DataFrame],
            qualification_leaders: dict[str, LeaderScore],
            tradable_symbols: set[str],
            account: AccountState,
            risk: RiskAssessment,
            strategic_universe: StrategicUniverseRoles | None = None,
        ) -> None: ...


StrategicPortfolioPolicy.__module__ = "uquant.portfolio_strategic"


def _reset_qualification_streaks(account: AccountState) -> None:
    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_qualification:"):
            account.replacement_tenure[key] = 0


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


def _qualification_evidence_sha256(
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


def _candidate_symbol(
    *,
    route: StrategicRoute,
    symbols: list[str],
    leaders: dict[str, LeaderScore],
) -> str:
    if route.decisive_reversal_symbol in symbols:
        return str(route.decisive_reversal_symbol)
    return min(symbols, key=lambda symbol: (-leaders[symbol].score, symbol))


def _quorum_candidate_symbols(
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


def _deployment_block_reason(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    live_general_leaders: set[str],
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
    if live_general_leaders:
        return "existing_portfolio_owner"
    if account.candidate_tenure.get("recovery_cohort_locked", 0) == 1 and account.anchor_weights:
        return "recovery_owner"
    if account.pending_orders:
        return "pending_execution"
    if account.protected_weights:
        return "protected_owner"
    if account.strategic_last_exit_date:
        last_exit = pd.Timestamp(account.strategic_last_exit_date)
        visible_sessions = sorted(
            {
                session
                for frame in user_panel.values()
                for session in frame.index
                if last_exit < session <= date
            }
        )
        if len(visible_sessions) < self.cfg.strategic_epoch_cooldown_sessions:
            return "strategic_cooldown"
    if not admission_open and not cash_rearm_open:
        return "opportunity_not_deployable"
    return ""


def _strategic_snapshots(
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


def _resolve_qualification_inputs(
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


def _observe_strategic_successor(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    qualification_panel: dict[str, pd.DataFrame],
    qualification_leaders: dict[str, LeaderScore],
    tradable_symbols: set[str],
    account: AccountState,
    risk: RiskAssessment,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> None:
    """Persist qualification streaks while an incumbent keeps all capital rights."""

    active_epoch = next(
        (
            epoch
            for epoch in account.strategic_epochs
            if epoch.epoch_id == account.active_strategic_epoch_id and epoch.active
        ),
        None,
    )
    if active_epoch is None:
        return
    eligible_symbols = set(tradable_symbols) - {active_epoch.owner_symbol}
    reference_snapshots = _strategic_snapshots(
        self,
        date=date,
        user_panel=qualification_panel,
        leaders=qualification_leaders,
    )
    candidate_snapshots = {
        symbol: snapshot
        for symbol, snapshot in reference_snapshots.items()
        if symbol in eligible_symbols and symbol in qualification_leaders
    }
    route = select_strategic_route(
        self,
        snapshots=candidate_snapshots,
        leaders={
            symbol: qualification_leaders[symbol]
            for symbol in candidate_snapshots
        },
        risk=risk,
    )
    legacy_raw, _synchronized = _qualification_evidence(
        self,
        route=route,
        snapshots=candidate_snapshots,
        leaders=qualification_leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    candidate = (
        _candidate_symbol(
            route=route,
            symbols=route_symbols,
            leaders=qualification_leaders,
        )
        if route_symbols
        else ""
    )
    if strategic_universe is None:
        strategic_universe = build_strategic_universe_roles(
            as_of=str(date.date()),
            tradable_symbols=tradable_symbols,
            qualification_reference_symbols=qualification_panel,
            risk_reference_symbols=(),
            industries={
                symbol: qualification_leaders[symbol].industry
                for symbol in qualification_panel
                if symbol in qualification_leaders
            },
            available_symbols=qualification_panel,
        )
    quorum = (
        evaluate_strategic_quorum(
            owner_symbol=candidate,
            candidate_symbols=_quorum_candidate_symbols(
                route=route,
                route_symbols=route_symbols,
            ),
            snapshots=reference_snapshots,
            leaders=qualification_leaders,
            risk=risk,
            universe=strategic_universe,
            cfg=self.cfg,
            synchronized_full_cohort=legacy_raw,
        )
        if candidate
        else None
    )
    symbols = route_symbols if quorum is not None and quorum.qualified else []
    if not symbols:
        previous = account.strategic_successor_qualification
        _admission_state, attempted_signature = _route_signature(
            route=route,
            symbols=route_symbols,
            leaders=qualification_leaders,
        )
        owner_quality_retained = bool(
            quorum is not None
            and quorum.owner_absolute_quality
            and candidate
        )
        account.strategic_successor_qualification = StrategicQualificationObservation(
            candidate_symbol=(candidate if owner_quality_retained else previous.candidate_symbol),
            qualification_signature=(
                attempted_signature if owner_quality_retained else previous.qualification_signature
            ),
            qualification_route=(route.route if owner_quality_retained else previous.qualification_route),
            qualification_evidence_sha256=(
                _qualification_evidence_sha256(
                    date=date,
                    route=route,
                    symbols=route_symbols,
                    signature=attempted_signature,
                    snapshots=candidate_snapshots,
                    leaders=qualification_leaders,
                    risk=risk,
                )
                if owner_quality_retained
                else previous.qualification_evidence_sha256
            ),
            qualification_ready=False,
            deployment_blocked=True,
            deployment_block_reason="active_epoch_read_only",
            qualification_streak=(
                previous.qualification_streak
                if owner_quality_retained
                and previous.candidate_symbol == candidate
                and previous.qualification_signature == attempted_signature
                else 0
            ),
            qualification_last_observed_session=str(date.date()),
            candidate_invalidation_reason=(
                "successor_reference_coverage_or_confirmation"
                if owner_quality_retained
                else "successor_qualification_not_ready"
            ),
            qualification_quorum=(
                quorum.route.value if quorum is not None else StrategicQuorumRoute.NONE.value
            ),
            candidate_symbols=sorted(route_symbols),
            unavailable_reference_symbols=(
                list(quorum.unavailable_references) if quorum is not None else []
            ),
        )
        return
    _admission_state, signature = _route_signature(
        route=route,
        symbols=symbols,
        leaders=qualification_leaders,
    )
    streak_key = f"strategic_successor:{signature}"
    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_successor:") and key != streak_key:
            account.replacement_tenure[key] = 0
    streak = account.replacement_tenure.get(streak_key, 0) + 1
    account.replacement_tenure[streak_key] = streak
    required_days = quorum.required_confirm_days
    account.strategic_successor_qualification = StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=signature,
        qualification_route=route.route,
        qualification_evidence_sha256=_qualification_evidence_sha256(
            date=date,
            route=route,
            symbols=symbols,
            signature=signature,
            snapshots=candidate_snapshots,
            leaders=qualification_leaders,
            risk=risk,
        ),
        qualification_ready=streak >= required_days,
        deployment_blocked=True,
        deployment_block_reason="active_epoch_read_only",
        qualification_streak=streak,
        qualification_last_observed_session=str(date.date()),
        qualification_quorum=quorum.route.value,
        candidate_symbols=sorted(symbols),
        unavailable_reference_symbols=list(quorum.unavailable_references),
        evidence_family_status={
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
        },
    )


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


@dataclass(frozen=True, slots=True)
class _QualifiedRoute:
    symbols: list[str]
    route: str
    admission_state: str
    signature: str
    decisive_reversal_symbol: str | None
    admission_authorized: bool
    quorum_route: str
    restricted_initial_weight: float | None
    cash_rearm_authorized: bool


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
        and (hard_persistent or route.route == "reversal_industry")
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


def _qualification_evidence(
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
        and (
            synchronized_before_anchor
            or (independent_risk_coverage and _independent_market_confirmation(self, risk))
        )
    )
    return raw, synchronized_before_anchor


def _route_signature(
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


def _update_route_qualification(
    self: StrategicPortfolioPolicy,
    *,
    symbols: list[str],
    signature: str,
    account: AccountState,
) -> list[str]:
    previous = set(account.strategic_previous_symbols)
    same_members = bool(previous) and set(symbols) == previous
    new_members = len(set(symbols) - previous)
    if previous and not same_members and new_members < self.cfg.strategic_epoch_min_symbol_change:
        symbols = []
        account.candidate_tenure["strategic_cohort_qualification"] = 0
    if symbols:
        for key in tuple(account.replacement_tenure):
            if key.startswith("strategic_qualification:") and key != signature:
                account.replacement_tenure[key] = 0
        account.replacement_tenure[signature] = account.replacement_tenure.get(signature, 0) + 1
        account.candidate_tenure["strategic_cohort_qualification"] = account.replacement_tenure[signature]
    else:
        _reset_qualification_streaks(account)
        account.candidate_tenure["strategic_cohort_qualification"] = 0
    return symbols


def _route_admission_open(
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
) -> _QualifiedRoute | None:
    previous_observed_session = (
        account.strategic_qualification.qualification_last_observed_session
    )
    legacy_raw, synchronized_before_anchor = _qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    candidate = (
        _candidate_symbol(route=route, symbols=route_symbols, leaders=leaders)
        if route_symbols
        else ""
    )
    quorum = evaluate_strategic_quorum(
        owner_symbol=candidate,
        candidate_symbols=_quorum_candidate_symbols(
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
    raw = bool(
        quorum is not None
        and quorum.qualified
        and (
            quorum.route is not StrategicQuorumRoute.FULL_COHORT
            or legacy_raw
        )
    )
    symbols = route_symbols if raw else []
    account.candidate_tenure["strategic_long_cycle_open"] = int(raw)
    admission_state, signature = _route_signature(
        route=route,
        symbols=symbols,
        leaders=leaders,
    )
    symbols = _update_route_qualification(
        self,
        symbols=symbols,
        signature=signature,
        account=account,
    )
    required_days = (
        quorum.required_confirm_days
        if quorum is not None
        else self.cfg.strategic_cohort_confirm_days
    )
    route_admission_open = _route_admission_open(
        self,
        route=route,
        symbols=symbols,
        snapshots=snapshots,
        admission_open=admission_open,
        synchronized_before_anchor=synchronized_before_anchor,
    )
    if not symbols:
        previous = account.strategic_qualification
        account.strategic_qualification = StrategicQualificationObservation(
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
        observe_strategic_cash_rearm(
            account=account,
            risk=risk,
            universe=strategic_universe,
            snapshots=reference_snapshots,
            leaders=leaders,
            candidate_symbol=candidate,
            qualification_ready=False,
            observed_session=str(date.date()),
            previous_observed_session=previous_observed_session,
            cfg=self.cfg,
        )
        return None
    streak = account.candidate_tenure["strategic_cohort_qualification"]
    candidate = _candidate_symbol(route=route, symbols=symbols, leaders=leaders)
    account.strategic_qualification = StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=signature,
        qualification_route=route.route,
        qualification_evidence_sha256=_qualification_evidence_sha256(
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
        qualification_quorum=(
            quorum.route.value if quorum is not None else StrategicQuorumRoute.NONE.value
        ),
        candidate_symbols=sorted(symbols),
        unavailable_reference_symbols=(
            list(quorum.unavailable_references) if quorum is not None else []
        ),
        evidence_family_status=(
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
        ),
    )
    cash_rearm_authorized = observe_strategic_cash_rearm(
        account=account,
        risk=risk,
        universe=strategic_universe,
        snapshots=reference_snapshots,
        leaders=leaders,
        candidate_symbol=candidate,
        qualification_ready=streak >= required_days,
        observed_session=str(date.date()),
        previous_observed_session=previous_observed_session,
        cfg=self.cfg,
    )
    if streak < required_days:
        return None
    return _QualifiedRoute(
        symbols,
        route.route,
        admission_state,
        signature,
        route.decisive_reversal_symbol,
        route_admission_open,
        quorum.route.value if quorum is not None else StrategicQuorumRoute.NONE.value,
        None if quorum is None else quorum.restricted_initial_weight,
        cash_rearm_authorized,
    )


def _strategic_target_weights(
    self: StrategicPortfolioPolicy,
    *,
    symbols: list[str],
    weighted_symbols: list[str],
    dominant_symbol: str | None,
    owner_symbol: str,
    quorum_route: str,
    restricted_initial_weight: float | None,
) -> dict[str, float]:
    if quorum_route in {
        StrategicQuorumRoute.STRONG_PAIR.value,
        StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
    }:
        return {
            owner_symbol: max(0.0, restricted_initial_weight or 0.0),
        }
    if dominant_symbol is not None:
        return {dominant_symbol: self.cfg.strategic_dominant_max_weight}
    if len(symbols) == 1:
        return {
            weighted_symbols[0]: min(
                self.cfg.max_symbol_weight,
                self.cfg.strategic_one_name_gross,
            )
        }
    if len(symbols) == 2:
        cohort_gross = min(self.cfg.max_gross, self.cfg.strategic_two_name_gross)
        lead_weight = min(self.cfg.max_symbol_weight, 0.60 * cohort_gross)
        return {
            weighted_symbols[0]: lead_weight,
            weighted_symbols[1]: max(0.0, cohort_gross - lead_weight),
        }
    weight = min(self.cfg.max_symbol_weight, self.cfg.max_gross / len(symbols))
    return {symbol: weight for symbol in weighted_symbols}


def _activate_strategic_cohort(
    self: StrategicPortfolioPolicy,
    *,
    qualified: _QualifiedRoute,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    date: pd.Timestamp,
    risk: RiskAssessment,
) -> None:
    live_anchors = {
        symbol
        for symbol in account.anchor_weights
        if account.positions.get(symbol) is not None and account.positions[symbol].shares > 0
    }
    locked_recovery = bool(live_anchors and account.candidate_tenure.get("recovery_cohort_locked", 0) == 1)
    if locked_recovery or live_anchors & set(qualified.symbols):
        account.candidate_tenure["strategic_deferred_to_recovery"] = 1
        return
    self._release_recovery_anchor(account)
    account.tactical_anchor_symbol = ""
    account.candidate_tenure["tactical_active"] = 0
    account.candidate_tenure["tactical_promotable"] = 0
    account.candidate_tenure["strategic_deferred_to_recovery"] = 0
    account.candidate_tenure["strategic_cohort_evaluated"] = 1
    weighted_symbols = sorted(
        qualified.symbols,
        key=lambda symbol: (-leaders[symbol].score, symbol),
    )
    dominant_symbol = (
        qualified.decisive_reversal_symbol
        if qualified.route == "reversal_industry" and len(weighted_symbols) == 2
        else None
    )
    restricted_owner = qualified.quorum_route in {
        StrategicQuorumRoute.STRONG_PAIR.value,
        StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
    } or qualified.cash_rearm_authorized
    account.strategic_cohort_symbols = (
        [account.strategic_qualification.candidate_symbol]
        if restricted_owner
        else [dominant_symbol]
        if dominant_symbol is not None
        else list(weighted_symbols)
    )
    account.strategic_cohort_targets = _strategic_target_weights(
        self,
        symbols=qualified.symbols,
        weighted_symbols=weighted_symbols,
        dominant_symbol=dominant_symbol,
        owner_symbol=account.strategic_qualification.candidate_symbol,
        quorum_route=qualified.quorum_route,
        restricted_initial_weight=qualified.restricted_initial_weight,
    )
    if qualified.cash_rearm_authorized:
        account.strategic_cohort_targets = {
            account.strategic_qualification.candidate_symbol: strategic_cash_rearm_weight(
                account=account,
                risk=risk,
                cfg=self.cfg,
            )
        }
        mark_strategic_cash_rearm_grant(account)
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_external_risk_epoch"] = 0
    account.candidate_tenure["strategic_cohort_active"] = 1
    account.candidate_tenure["strategic_cohort_completed"] = 0
    account.candidate_tenure["strategic_cohort_started"] = 0
    account.candidate_tenure["strategic_cohort_days"] = 0
    account.candidate_tenure["strategic_profit_armed"] = 0
    account.candidate_tenure["strategic_tail_armed"] = 1
    pending_epoch_number = account.strategic_epoch + 1
    account.candidate_tenure["strategic_early_cycle_epoch"] = (
        pending_epoch_number
        if qualified.symbols
        and all(
            snapshots[symbol]["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            and snapshots[symbol]["ret120"] < 0.0
            for symbol in qualified.symbols
        )
        else 0
    )
    account.candidate_tenure["strategic_dominant_epoch"] = (
        pending_epoch_number if dominant_symbol is not None else 0
    )
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = 0
    account.strategic_candidate_signature = qualified.signature
    observation = account.strategic_qualification
    previous_grant_id = (
        account.strategic_grant.grant_id
        if account.strategic_grant is not None and account.strategic_grant.terminal
        else ""
    )
    if not account.account_identity:
        identity_payload = "|".join(
            (
                float(account.initial_cash).hex(),
                account.code_hash or "unbound-production-source",
                observation.qualification_last_observed_session,
            )
        )
        account.account_identity = "account_" + hashlib.sha256(identity_payload.encode()).hexdigest()
    production_source_identity = account.code_hash or "unbound-production-source"
    candidate_weight = account.strategic_cohort_targets.get(observation.candidate_symbol, 0.0)
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_evidence_sha256=observation.qualification_evidence_sha256,
        created_session=str(date.date()),
        previous_grant_id=previous_grant_id,
        production_source_identity=production_source_identity,
    )
    grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_evidence_sha256=observation.qualification_evidence_sha256,
        created_session=str(date.date()),
        last_eligible_session=str(date.date()),
        target_weight=candidate_weight,
        status=StrategicGrantStatus.QUALIFIED.value,
        previous_grant_id=previous_grant_id,
        account_identity=account.account_identity,
        production_source_identity=production_source_identity,
        qualification_quorum=qualified.quorum_route,
    )
    previous_epoch_id = (
        account.strategic_epochs[-1].epoch_id if account.strategic_epochs else ""
    )
    if account.strategic_epochs and not account.strategic_epochs[-1].terminal:
        raise RuntimeError("new strategic grant requires the prior epoch to be terminal")
    config_identity = "config:" + config_fingerprint(self.cfg)
    epoch_id = derive_strategic_epoch_id(
        account_identity=account.account_identity,
        owner_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        grant_id=grant_id,
        opened_session=str(date.date()),
        previous_epoch_id=previous_epoch_id,
        source_identity=production_source_identity,
        config_identity=config_identity,
        evidence_sha256=observation.qualification_evidence_sha256,
    )
    full_weight = max(
        candidate_weight,
        min(self.cfg.max_symbol_weight, self.cfg.strategic_one_name_gross),
    )
    epoch = StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_quorum=qualified.quorum_route,
        grant_id=grant_id,
        opened_session=str(date.date()),
        previous_epoch_id=previous_epoch_id,
        source_identity=production_source_identity,
        config_identity=config_identity,
        evidence_sha256=observation.qualification_evidence_sha256,
        realized_status=StrategicEpochStatus.PROBE.value,
        target_weight=candidate_weight,
        full_weight=full_weight,
        account_identity=account.account_identity,
    )
    epoch.validate()
    grant.epoch_id = epoch_id
    account.strategic_grant = grant
    account.strategic_epochs.append(epoch)


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
            _release_expired_strategic_deployment(account)
    resolved_panel, resolved_leaders, resolved_universe = _resolve_qualification_inputs(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    account.strategic_tradable_universe_identity = resolved_universe.tradable_identity
    account.strategic_qualification_universe_identity = (
        resolved_universe.qualification_reference_identity
    )
    account.strategic_risk_universe_identity = resolved_universe.risk_reference_identity
    if account.active_strategic_epoch_id:
        self._observe_strategic_successor(
            date=date,
            qualification_panel=resolved_panel,
            qualification_leaders=resolved_leaders,
            tradable_symbols=set(user_panel),
            account=account,
            risk=risk,
            strategic_universe=resolved_universe,
        )
        return
    if account.candidate_tenure.get("strategic_cohort_active", 0) == 1:
        return
    live_general_leaders = {
        symbol
        for symbol in account.active_leaders
        if (position := account.positions.get(symbol)) is not None and position.shares > 0
    }
    if not _strategic_discovery_open(
        self,
        date=date,
        user_panel=user_panel,
        account=account,
        risk=risk,
    ):
        return
    reference_snapshots = _strategic_snapshots(
        self,
        date=date,
        user_panel=resolved_panel,
        leaders=resolved_leaders,
    )
    snapshots = {
        symbol: values
        for symbol, values in reference_snapshots.items()
        if symbol in user_panel and symbol in resolved_leaders
    }
    if not snapshots:
        previous = account.strategic_qualification
        candidate_temporarily_unavailable = bool(
            previous.candidate_symbol and previous.candidate_symbol in user_panel
        )
        if candidate_temporarily_unavailable:
            previous.deployment_blocked = True
            previous.deployment_block_reason = "candidate_not_tradable"
        else:
            _reset_qualification_streaks(account)
            account.candidate_tenure["strategic_cohort_qualification"] = 0
            account.candidate_tenure["strategic_long_cycle_open"] = 0
        return
    route = select_strategic_route(
        self,
        snapshots=snapshots,
        leaders=resolved_leaders,
        risk=risk,
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
    if account.strategic_qualification.candidate_symbol:
        block_reason = _deployment_block_reason(
            self,
            date=date,
            user_panel=user_panel,
            account=account,
            risk=risk,
            admission_open=(qualified.admission_authorized if qualified is not None else admission_open),
            live_general_leaders=live_general_leaders,
            cash_rearm_authorized=(
                qualified.cash_rearm_authorized if qualified is not None else False
            ),
        )
        account.strategic_qualification.deployment_blocked = bool(block_reason)
        account.strategic_qualification.deployment_block_reason = block_reason
        if block_reason == "recovery_owner":
            account.candidate_tenure["strategic_deferred_to_recovery"] = 1
    if qualified is not None:
        if account.strategic_qualification.deployment_blocked:
            return
        _activate_strategic_cohort(
            self,
            qualified=qualified,
            snapshots=snapshots,
            leaders=resolved_leaders,
            account=account,
            date=date,
            risk=risk,
        )


def _release_expired_strategic_deployment(account: AccountState) -> None:
    """Release capital authority after an expired probe is fully settled."""

    account.strategic_cohort_symbols.clear()
    account.strategic_cohort_targets.clear()
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.strategic_restore_epoch_ids.clear()
    account.strategic_candidate_signature = ""
    for key in (
        "strategic_cohort_active",
        "strategic_cohort_completed",
        "strategic_cohort_started",
        "strategic_cohort_days",
        "strategic_profit_armed",
        "strategic_tail_armed",
        "strategic_dominant_epoch",
    ):
        account.candidate_tenure[key] = 0


def _expire_strategic_grant(
    account: AccountState,
    *,
    reason: str,
    weights_now: dict[str, float],
) -> None:
    grant = account.strategic_grant
    if grant is None or grant.terminal:
        return
    grant.status = StrategicGrantStatus.EXPIRED.value
    grant.expiry_reason = reason
    had_pending_execution = any(
        order.grant_id == grant.grant_id for order in account.pending_orders
    )
    account.pending_orders = [
        order for order in account.pending_orders if order.grant_id != grant.grant_id
    ]
    held = {
        symbol: 0.0
        for symbol, position in account.positions.items()
        if position.shares > 0 and position.grant_id == grant.grant_id
    }
    account.strategic_cohort_symbols = sorted(held)
    account.strategic_cohort_targets = dict(held)
    account.candidate_tenure["strategic_cohort_active"] = int(bool(held))
    account.strategic_restore_weights.clear()
    _reset_qualification_streaks(account)
    account.candidate_tenure["strategic_cohort_qualification"] = 0
    observation = account.strategic_qualification
    observation.qualification_ready = False
    observation.deployment_blocked = True
    observation.deployment_block_reason = "qualification_invalid"
    observation.qualification_streak = 0
    observation.candidate_invalidation_reason = reason
    if not held and not had_pending_execution and grant.epoch_id:
        settled = settle_account_strategic_epoch(
            account,
            epoch_id=grant.epoch_id,
            closed_session=(
                observation.qualification_last_observed_session
                or grant.last_eligible_session
            ),
            close_reason=reason,
            expired=True,
        )
        if settled:
            _release_expired_strategic_deployment(account)


def _revalidate_strategic_grant(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    weights_now: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> bool:
    """Reconfirm a not-yet-active grant before every capital retry."""

    resolved_panel, resolved_leaders, _resolved_universe = _resolve_qualification_inputs(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    grant = account.strategic_grant
    if grant is None or grant.terminal or grant.status in {
        StrategicGrantStatus.ACTIVE.value,
        StrategicGrantStatus.COMPLETED.value,
    }:
        return True
    if grant.candidate_symbol not in user_panel:
        _expire_strategic_grant(
            account,
            reason="candidate_removed_from_allowed_universe",
            weights_now=weights_now,
        )
        return False
    candidate_frame = user_panel[grant.candidate_symbol]
    if date not in candidate_frame.index:
        account.strategic_qualification.deployment_blocked = True
        account.strategic_qualification.deployment_block_reason = "candidate_not_tradable"
        return True
    reference_snapshots = _strategic_snapshots(
        self,
        date=date,
        user_panel=resolved_panel,
        leaders=resolved_leaders,
    )
    snapshots = {
        symbol: values
        for symbol, values in reference_snapshots.items()
        if symbol in user_panel and symbol in resolved_leaders
    }
    if account.candidate_tenure.get("strategic_cash_rearm_grant", 0) == 1:
        strict_candidate = strict_absolute_owner_quality(
            symbol=grant.candidate_symbol,
            snapshots=reference_snapshots,
            leaders=resolved_leaders,
            cfg=self.cfg,
        )
        set_strategic_cash_rearm_strict(
            account,
            qualified=strict_candidate,
        )
        if not strict_candidate:
            _expire_strategic_grant(
                account,
                reason="cash_rearm_absolute_quality_failed",
                weights_now=weights_now,
            )
            return False
    route = select_strategic_route(
        self,
        snapshots=snapshots,
        leaders=resolved_leaders,
        risk=risk,
    )
    legacy_raw, synchronized_before_anchor = _qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=resolved_leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    quorum = evaluate_strategic_quorum(
        owner_symbol=grant.candidate_symbol,
        candidate_symbols=_quorum_candidate_symbols(
            route=route,
            route_symbols=route_symbols,
        ),
        snapshots=reference_snapshots,
        leaders=resolved_leaders,
        risk=risk,
        universe=_resolved_universe,
        cfg=self.cfg,
        synchronized_full_cohort=legacy_raw,
    )
    raw = bool(
        quorum.qualified
        and (
            quorum.route is not StrategicQuorumRoute.FULL_COHORT
            or legacy_raw
        )
    )
    symbols = route_symbols if raw else []
    _admission_state, signature = _route_signature(
        route=route,
        symbols=symbols,
        leaders=resolved_leaders,
    )
    candidate_still_qualified = _grant_candidate_meets_route(
        self,
        candidate_symbol=grant.candidate_symbol,
        qualification_route=grant.qualification_route,
        snapshots=snapshots,
        leaders=resolved_leaders,
        risk=risk,
    )
    if not candidate_still_qualified:
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
    if not raw and quorum.owner_absolute_quality:
        observation = account.strategic_qualification
        observation.qualification_ready = True
        observation.deployment_blocked = True
        observation.deployment_block_reason = "reference_coverage_or_confirmation"
        observation.qualification_last_observed_session = str(date.date())
        observation.qualification_quorum = grant.qualification_quorum
        observation.unavailable_reference_symbols = list(
            quorum.unavailable_references
        )
        return True
    if raw and (
        grant.candidate_symbol not in symbols or route.route != grant.qualification_route
    ):
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
    route_admission_open = bool(
        admission_open
        if not raw
        else _route_admission_open(
            self,
            route=route,
            symbols=symbols,
            snapshots=snapshots,
            admission_open=admission_open,
            synchronized_before_anchor=synchronized_before_anchor,
        )
    )
    block_reason = _deployment_block_reason(
        self,
        date=date,
        user_panel=user_panel,
        account=account,
        risk=risk,
        admission_open=route_admission_open,
        live_general_leaders=set(),
    )
    observation = account.strategic_qualification
    observation.candidate_symbol = grant.candidate_symbol
    if raw:
        observation.qualification_signature = signature
        observation.qualification_route = route.route
        observation.qualification_evidence_sha256 = _qualification_evidence_sha256(
            date=date,
            route=route,
            symbols=symbols,
            signature=signature,
            snapshots=snapshots,
            leaders=resolved_leaders,
            risk=risk,
        )
        observation.qualification_quorum = quorum.route.value
        observation.candidate_symbols = sorted(symbols)
        observation.unavailable_reference_symbols = list(
            quorum.unavailable_references
        )
    observation.qualification_ready = True
    observation.deployment_blocked = bool(block_reason)
    observation.deployment_block_reason = block_reason
    observation.qualification_last_observed_session = str(date.date())
    observation.candidate_invalidation_reason = ""
    if raw:
        grant.last_eligible_session = str(date.date())
    visible_since_route = sum(
        1
        for session in candidate_frame.index
        if pd.Timestamp(grant.last_eligible_session) < session <= date
    )
    if (
        not raw
        and (
            visible_since_route > MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
            or grant.healthy_retry_sessions
            >= MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
        )
    ):
        _expire_strategic_grant(
            account,
            reason="qualification_observation_window_elapsed",
            weights_now=weights_now,
        )
        return False
    if not block_reason:
        grant.healthy_retry_sessions = min(
            MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS,
            grant.healthy_retry_sessions + 1,
        )
    return True


initialize_strategic_cohort = _initialize_strategic_cohort
observe_strategic_successor = _observe_strategic_successor
revalidate_strategic_grant = _revalidate_strategic_grant
