"""Causal discovery and qualification of strategic cohorts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

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
        ) -> bool: ...


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


def _deployment_block_reason(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    live_general_leaders: set[str],
) -> str:
    if risk.state.value == "RISK_OFF":
        return "risk_off"
    if risk.state.value == "CRISIS":
        return "crisis"
    if risk.freeze_new_risk or bool(risk.evidence.get("freeze_new_risk", False)):
        return "freeze_new_risk"
    if risk.state.value == "CAUTION" and risk.votes >= 2:
        return "risk_caution"
    if risk.target_gross_cap <= 0.0:
        return "target_gross_cap"
    if account.capital_budget_level > 0:
        return "capital_budget"
    if account.chronic_level > 0:
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
    if not admission_open:
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
        }
    return snapshots


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
) -> _QualifiedRoute | None:
    raw, synchronized_before_anchor = _qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    symbols = list(route.symbols) if raw else []
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
        self.cfg.strategic_cohort_confirm_days
        if route.synchronized_reversal or len(symbols) >= self.cfg.strategic_cohort_size
        else self.cfg.strategic_two_name_confirm_days
        if len(symbols) == 2
        else self.cfg.strategic_one_name_confirm_days
        if len(symbols) == 1
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
    )


def _strategic_target_weights(
    self: StrategicPortfolioPolicy,
    *,
    symbols: list[str],
    weighted_symbols: list[str],
    dominant_symbol: str | None,
) -> dict[str, float]:
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
    account.strategic_cohort_symbols = (
        [dominant_symbol] if dominant_symbol is not None else list(weighted_symbols)
    )
    account.strategic_cohort_targets = _strategic_target_weights(
        self,
        symbols=qualified.symbols,
        weighted_symbols=weighted_symbols,
        dominant_symbol=dominant_symbol,
    )
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
    account.strategic_epoch += 1
    account.candidate_tenure["strategic_early_cycle_epoch"] = (
        account.strategic_epoch
        if qualified.symbols
        and all(
            snapshots[symbol]["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            and snapshots[symbol]["ret120"] < 0.0
            for symbol in qualified.symbols
        )
        else 0
    )
    account.candidate_tenure["strategic_dominant_epoch"] = (
        account.strategic_epoch if dominant_symbol is not None else 0
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
    account.strategic_grant = StrategicGrantIntent(
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
    )


def _initialize_strategic_cohort(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool = True,
) -> None:
    """Discover and activate a persistent long-cycle cohort causally."""

    if not self.cfg.strategic_dynamic_enabled:
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
    snapshots = _strategic_snapshots(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
    )
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
        leaders=leaders,
        risk=risk,
    )
    qualified = _qualify_strategic_route(
        self,
        date=date,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
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
            leaders=leaders,
            account=account,
            date=date,
        )


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
    account.pending_orders = [
        order for order in account.pending_orders if order.grant_id != grant.grant_id
    ]
    held = {
        symbol: weights_now.get(symbol, 0.0)
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
) -> bool:
    """Reconfirm a not-yet-active grant before every capital retry."""

    grant = account.strategic_grant
    if grant is None or grant.terminal or grant.status == StrategicGrantStatus.ACTIVE.value:
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
    snapshots = _strategic_snapshots(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
    )
    route = select_strategic_route(
        self,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    raw, synchronized_before_anchor = _qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    symbols = list(route.symbols) if raw else []
    _admission_state, signature = _route_signature(
        route=route,
        symbols=symbols,
        leaders=leaders,
    )
    candidate_still_qualified = _grant_candidate_meets_route(
        self,
        candidate_symbol=grant.candidate_symbol,
        qualification_route=grant.qualification_route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    if not candidate_still_qualified:
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
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
            leaders=leaders,
            risk=risk,
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
revalidate_strategic_grant = _revalidate_strategic_grant
