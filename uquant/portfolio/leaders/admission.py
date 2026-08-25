"""Mechanical Task 8 leader owner extracted from the immutable policy."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from ...portfolio_core import effective_n
from ...portfolio_strategic import StrategicPortfolioPolicy
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    RiskAssessment,
    Target,
)


class LeaderPortfolioPolicy(StrategicPortfolioPolicy):
    """Own dynamic K, admissions, additions, satellites, and leader rotation."""

    if TYPE_CHECKING:

        def _cap_opportunity_gross(
            self,
            *,
            proposed: dict[str, float],
            gross_cap: float,
            weights_now: dict[str, float],
            leaders: dict[str, LeaderScore],
            reasons: dict[str, str],
            opportunity: Opportunity,
        ) -> dict[str, float]: ...

        def _conviction_shares(
            self, symbols: list[str], leaders: dict[str, LeaderScore], *, evidence_qualified: bool
        ) -> NDArray[np.float64]: ...

        def _conviction_evidence_qualified(
            self,
            *,
            symbols: list[str],
            leaders: dict[str, LeaderScore],
            user_panel: dict[str, pd.DataFrame],
            date: pd.Timestamp,
            high_confidence: bool,
        ) -> bool: ...

        @staticmethod
        def _session_clock(user_panel: dict[str, pd.DataFrame], date: pd.Timestamp) -> pd.DatetimeIndex: ...

        @staticmethod
        def _session_distance(
            clock: pd.DatetimeIndex, start: str | pd.Timestamp, end: pd.Timestamp
        ) -> int: ...

        def _correlations(
            self, user_panel: dict[str, pd.DataFrame], symbols: list[str], date: pd.Timestamp
        ) -> pd.DataFrame: ...

        def _admission_utility(
            self,
            *,
            candidate: LeaderScore,
            active: list[str],
            leaders: dict[str, LeaderScore],
            user_panel: dict[str, pd.DataFrame],
            date: pd.Timestamp,
            account: AccountState,
        ) -> float: ...

        def _dynamic_k(
            self,
            *,
            date: pd.Timestamp,
            opportunity: Opportunity,
            risk: RiskAssessment,
            candidates: list[LeaderScore],
            user_panel: dict[str, pd.DataFrame],
            account: AccountState,
        ) -> int: ...

        def _rotation_allowed(
            self, account: AccountState, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame]
        ) -> bool: ...

        def _update_leader_cycle_arm(
            self,
            *,
            opportunity: Opportunity,
            risk: RiskAssessment,
            leaders: dict[str, LeaderScore],
            account: AccountState,
            strategic_handoff_blocked: bool = False,
            strategic_handoff_ready: bool = False,
        ) -> bool: ...

        @staticmethod
        def _retention_score(
            symbol: str, leaders: dict[str, LeaderScore], account: AccountState
        ) -> float: ...

        def _leader_lifecycle_exit_confirmed(
            self,
            *,
            symbol: str,
            date: pd.Timestamp,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
        ) -> bool: ...

        def _industry_handoff(self, *, challenger: LeaderScore, incumbent: LeaderScore) -> bool: ...

        def _leader_targets(
            self,
            *,
            date: pd.Timestamp,
            opportunity: Opportunity,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            weights_now: dict[str, float],
            prices: dict[str, float],
        ) -> tuple[Target, ...] | None: ...


LeaderPortfolioPolicy.__module__ = "uquant.portfolio_leaders"


def _conviction_shares(
    self: LeaderPortfolioPolicy,
    symbols: list[str],
    leaders: dict[str, LeaderScore],
    *,
    evidence_qualified: bool,
) -> NDArray[np.float64]:
    """Map score dispersion only after the joint evidence gate passes."""
    scores: NDArray[np.float64] = np.asarray(
        [leaders[symbol].score for symbol in symbols],
        dtype=np.float64,
    )
    if len(scores) <= 1:
        return np.ones(len(scores), dtype=np.float64)
    if not evidence_qualified or not self.cfg.conviction_weighting_enabled or np.ptp(scores) < 0.03:
        return np.full(len(scores), 1.0 / len(scores), dtype=np.float64)
    weights: NDArray[np.float64] = np.asarray(
        np.exp(6.0 * (scores - float(scores.max()))),
        dtype=np.float64,
    )
    weights /= weights.sum()
    cap = self.cfg.max_symbol_weight
    # Capped-simplex projection redistributes excess instead of leaving
    # avoidable cash after a high-conviction first entry.
    for _ in range(len(weights) + 1):
        excess = float(np.maximum(weights - cap, 0.0).sum())
        weights = np.minimum(weights, cap)
        room = np.maximum(cap - weights, 0.0)
        if excess <= 1e-12 or float(room.sum()) <= 1e-12:
            break
        weights += excess * room / room.sum()
    return np.asarray(weights / weights.sum(), dtype=np.float64)


def _conviction_evidence_qualified(
    self: LeaderPortfolioPolicy,
    *,
    symbols: list[str],
    leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    high_confidence: bool,
) -> bool:
    """Require independent quality and diversification evidence to concentrate."""
    if not high_confidence or len(symbols) < 2:
        return False
    factor_floor = self.cfg.high_confidence_entry_breadth
    if any(
        leaders[symbol].components.get("resilience", 0.0) < factor_floor
        or leaders[symbol].components.get("relative_strength", 0.0) < factor_floor
        or leaders[symbol].components.get("liquidity", 0.0) < self.cfg.leader_min_confidence
        for symbol in symbols
    ):
        return False
    correlations = self._correlations(user_panel, symbols, date)
    pairwise: list[float] = []
    for index, left in enumerate(symbols):
        for right in symbols[index + 1 :]:
            if left not in correlations.index or right not in correlations.columns:
                continue
            value = float(cast(float, correlations.at[left, right]))
            if math.isfinite(value):
                pairwise.append(abs(value))
    return bool(pairwise and max(pairwise) <= self.cfg.risk_correlation)


def _correlations(
    self: LeaderPortfolioPolicy,
    user_panel: dict[str, pd.DataFrame],
    symbols: list[str],
    date: pd.Timestamp,
) -> pd.DataFrame:
    returns = {
        symbol: user_panel[symbol]
        .loc[:date, "close"]
        .pct_change(fill_method=None)
        .tail(self.cfg.correlation_window)
        for symbol in symbols
        if symbol in user_panel
    }
    return pd.DataFrame(returns).dropna(how="all").corr() if returns else pd.DataFrame()


def _admission_utility(
    self: LeaderPortfolioPolicy,
    *,
    candidate: LeaderScore,
    active: list[str],
    leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    account: AccountState,
) -> float:
    """Discount redundant industry/factor exposure without banning it."""
    utility = candidate.score
    same_industry = [
        leaders[symbol]
        for symbol in active
        if symbol in leaders
        and leaders[symbol].industry == candidate.industry
        and candidate.industry != "unknown"
    ]
    if same_industry:
        strongest_peer = max(item.score for item in same_industry)
        high_conviction_cluster = (
            account.candidate_tenure.get("post_shock_recovery", 0) == 1
            and candidate.score >= self.cfg.strong_cluster_min_score
            and strongest_peer - candidate.score <= self.cfg.strong_cluster_max_gap
        )
        utility -= (
            self.cfg.strong_cluster_penalty
            if high_conviction_cluster
            else self.cfg.industry_duplicate_penalty
        )
    if active:
        correlations = self._correlations(
            user_panel,
            [*active, candidate.symbol],
            date,
        )
        if candidate.symbol in correlations:
            pairwise = correlations[candidate.symbol].drop(candidate.symbol, errors="ignore")
            if not pairwise.empty:
                utility -= self.cfg.correlation_admission_penalty * max(
                    0.0,
                    float(pairwise.median()) - 0.50,
                )
    return utility


def _diversification_adjusted_k(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    candidates: list[LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    target: int,
) -> int:
    account.candidate_tenure["evidence_concentration"] = 0
    if target < 3:
        return target
    leader_map = {item.symbol: item for item in candidates}
    remaining = list(candidates)
    trial: list[LeaderScore] = []
    while remaining and len(trial) < target:
        active_symbols = [item.symbol for item in trial]
        selected = max(
            remaining,
            key=lambda item: (
                self._admission_utility(
                    candidate=item,
                    active=active_symbols,
                    leaders=leader_map,
                    user_panel=user_panel,
                    date=date,
                    account=account,
                ),
                item.score,
                item.symbol,
            ),
        )
        trial.append(selected)
        remaining.remove(selected)
    equal = {item.symbol: 1.0 / target for item in trial}
    correlations = self._correlations(user_panel, [item.symbol for item in trial], date)
    concentrated = (
        opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        and len(trial) >= 3
        and min(item.score for item in trial) >= 0.84
        and max(item.score for item in trial) - min(item.score for item in trial) <= 0.12
    )
    account.candidate_tenure["evidence_concentration"] = int(concentrated)
    return 2 if effective_n(equal, correlations) < 1.60 and not concentrated else target


def _confirmed_dynamic_k(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    candidates: list[LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    target: int,
) -> int:
    target = max(0, target)
    if account.candidate_tenure.get("leader_cycle_staged_handoff", 0) == 1:
        account.dynamic_k = min(1, target)
        account.last_k_change_date = str(date.date())
        return account.dynamic_k
    if account.dynamic_k <= 0:
        account.dynamic_k = target
        account.last_k_change_date = str(date.date())
        return account.dynamic_k
    target_key = "dynamic_k_target"
    streak_key = "dynamic_k_target_streak"
    if account.candidate_tenure.get(target_key, -1) == target:
        account.candidate_tenure[streak_key] = account.candidate_tenure.get(streak_key, 0) + 1
    else:
        account.candidate_tenure[target_key] = target
        account.candidate_tenure[streak_key] = 1
    clock = self._session_clock(user_panel, date)
    elapsed = (
        self._session_distance(clock, account.last_k_change_date, date)
        if account.last_k_change_date
        else self.cfg.dynamic_k_change_interval
    )
    change_interval = (
        self.cfg.dynamic_k_expand_interval
        if target > account.dynamic_k
        else self.cfg.dynamic_k_change_interval
    )
    if (
        target != account.dynamic_k
        and account.candidate_tenure[streak_key] >= self.cfg.dynamic_k_confirm_days
        and elapsed >= change_interval
    ):
        if target > account.dynamic_k:
            account.dynamic_k += 1
        elif opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}:
            account.dynamic_k -= 1
        account.last_k_change_date = str(date.date())
    return max(0, min(account.dynamic_k, len(candidates), self.cfg.max_positions))


def _dynamic_k(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    candidates: list[LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
) -> int:
    """Update the confirmed target holding count under regime and risk limits."""

    regime_cap = {
        Opportunity.STRONG_TREND: 4,
        Opportunity.TREND: 3,
        Opportunity.CHOPPY: 2,
        Opportunity.WEAK: 1,
        Opportunity.RECOVERY: 3,
    }[opportunity]
    target = min(self.cfg.max_positions, regime_cap, len(candidates))
    if risk.freeze_new_risk or risk.state.value in {"RISK_OFF", "CRISIS"}:
        target = min(target, 2)
    if len(candidates) >= 3 and candidates[0].score - candidates[2].score >= 0.18:
        target = min(target, 2)
    target = _diversification_adjusted_k(
        self,
        date=date,
        opportunity=opportunity,
        candidates=candidates,
        user_panel=user_panel,
        account=account,
        target=target,
    )
    return _confirmed_dynamic_k(
        self,
        date=date,
        opportunity=opportunity,
        candidates=candidates,
        user_panel=user_panel,
        account=account,
        target=target,
    )


admission_utility = _admission_utility
conviction_evidence_qualified = _conviction_evidence_qualified
conviction_shares = _conviction_shares
dynamic_leader_count = _dynamic_k
leader_correlations = _correlations
