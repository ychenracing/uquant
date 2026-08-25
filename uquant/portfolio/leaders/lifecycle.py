"""Lifecycle transitions for ordinary leader positions."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    Opportunity,
    RiskAssessment,
)

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy


def _session_clock(
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return the deterministic union of all visible user sessions."""
    clock = pd.DatetimeIndex([])
    for frame in user_panel.values():
        sessions = pd.DatetimeIndex(frame.index)
        clock = clock.union(sessions[sessions <= date])
    return clock.sort_values()


def _leader_session_distance(
    clock: pd.DatetimeIndex,
    start: str | pd.Timestamp,
    end: pd.Timestamp,
) -> int:
    bounded = clock[(clock >= pd.Timestamp(start)) & (clock <= end)]
    return max(0, len(bounded) - 1)


def _rotation_allowed(
    self: LeaderPortfolioPolicy,
    account: AccountState,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
) -> bool:
    clock = self._session_clock(user_panel, date)
    recent = [
        value
        for value in account.rotation_dates
        if pd.Timestamp(value) <= date and self._session_distance(clock, value, date) <= 20
    ]
    account.rotation_dates = recent
    return len(recent) < self.cfg.max_rotations_20d


def _confirmed_live_core(
    self: LeaderPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> bool:
    symbols = {
        position.symbol
        for position in account.positions.values()
        if position.shares > 0
        and position.lifecycle in {Lifecycle.CORE.value, Lifecycle.ADD1.value, Lifecycle.ADD2.value}
    }
    return bool(
        symbols
        and all(
            symbol in leaders
            and leaders[symbol].mature
            and leaders[symbol].confidence >= self.cfg.leader_min_confidence
            and leaders[symbol].score >= self.cfg.leader_cycle_min_score
            for symbol in symbols
        )
    )


def _frozen_cycle_arm(
    self: LeaderPortfolioPolicy,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    arm_key: str,
    streak_key: str,
) -> bool:
    live_active = [
        symbol
        for symbol in account.active_leaders
        if account.positions.get(symbol) is not None and account.positions[symbol].shares > 0
    ]
    independently_confirmed_live = bool(
        live_active
        and all(
            symbol in leaders
            and leaders[symbol].mature
            and leaders[symbol].confidence >= self.cfg.leader_min_confidence
            and leaders[symbol].score >= self.cfg.leader_cycle_min_score
            for symbol in live_active
        )
    )
    if risk.state.value in {"NORMAL", "CAUTION"} and independently_confirmed_live:
        account.candidate_tenure[arm_key] = 1
        account.candidate_tenure[streak_key] = 0
        return True
    preserved_core = False
    if risk.state.value == "NORMAL" and account.candidate_tenure.get(arm_key, 0) == 1:
        preserved_core = _confirmed_live_core(
            self,
            leaders=leaders,
            account=account,
        )
    if preserved_core:
        account.candidate_tenure[streak_key] = 0
        return True
    account.candidate_tenure[arm_key] = 0
    account.candidate_tenure[streak_key] = 0
    return False


def _leader_cycle_impulse(
    self: LeaderPortfolioPolicy,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    impulse_leader: bool,
    slow_market_legs: tuple[float, float],
) -> bool:
    evidence = risk.evidence
    return bool(
        min(slow_market_legs) >= self.cfg.leader_cycle_impulse_min_market_ret120
        and max(slow_market_legs) >= self.cfg.leader_cycle_min_market_ret120
        and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        and impulse_leader
        and risk.votes <= 1
        and float(evidence.get("ai_fast_return", -math.inf)) >= self.cfg.leader_cycle_impulse_return
        and float(evidence.get("declining_ratio", 1.0)) <= self.cfg.leader_cycle_impulse_breadth
        and float(evidence.get("below_ma20_ratio", 1.0)) <= self.cfg.leader_cycle_impulse_breadth
        and max(
            float(evidence.get("tech_speed", -math.inf)),
            float(evidence.get("broad_speed", -math.inf)),
        )
        >= self.cfg.leader_cycle_impulse_index_return
    )


def _exceptional_recovery_rearm(
    self: LeaderPortfolioPolicy,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    market_aligned: bool,
    credible: int,
) -> bool:
    return bool(
        account.candidate_tenure.get("recovery_cycle_rearm_pending", 0) == 1
        and account.candidate_tenure.get("tactical_cooldown", 0) <= 0
        and not account.positions
        and not account.pending_orders
        and risk.state.value == "NORMAL"
        and opportunity is Opportunity.STRONG_TREND
        and risk.votes <= 1
        and market_aligned
        and credible >= self.cfg.leader_cycle_min_mature
        and max(
            (
                item.score
                for item in leaders.values()
                if item.mature and item.confidence >= self.cfg.leader_min_confidence
            ),
            default=0.0,
        )
        >= 0.90
        and float(risk.evidence.get("trend_health", 0.0)) >= 0.82
    )


def _update_leader_cycle_arm(
    self: LeaderPortfolioPolicy,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    strategic_handoff_blocked: bool = False,
    strategic_handoff_ready: bool = False,
) -> bool:
    """Require broad, persistent leader evidence before generic trend capital.

    Recovery anchors and the bounded tactical rebound have their own causal
    confirmations.  The ordinary mature-leader route is broader, so it is
    armed only after several high-confidence mature leaders coexist with a
    confirmed strong trend.  Account/market risk owns the disarm decision.
    """
    arm_key = "leader_cycle_armed"
    streak_key = "leader_cycle_evidence"
    if risk.freeze_new_risk or risk.state.value in {"RISK_OFF", "CRISIS"}:
        return _frozen_cycle_arm(
            self,
            risk=risk,
            leaders=leaders,
            account=account,
            arm_key=arm_key,
            streak_key=streak_key,
        )
    if strategic_handoff_blocked:
        # A completed strategic owner records the first admissible rearm
        # session. Generic momentum must not bypass that cross-owner
        # cooldown with a streak accumulated under the old epoch.
        account.candidate_tenure[arm_key] = 0
        account.candidate_tenure[streak_key] = 0
        return False
    credible = sum(
        item.mature
        and item.confidence >= self.cfg.leader_min_confidence
        and item.score >= self.cfg.leader_cycle_min_score
        for item in leaders.values()
    )
    impulse_leader = any(
        item.mature
        and item.confidence >= self.cfg.leader_min_confidence
        and item.score >= self.cfg.leader_cycle_min_score
        for item in leaders.values()
    )
    slow_market_legs = (
        float(risk.evidence.get("broad_ret120", -math.inf)),
        float(risk.evidence.get("tech_ret120", -math.inf)),
    )
    market_aligned = bool(min(slow_market_legs) >= self.cfg.leader_cycle_min_market_ret120)
    impulse = _leader_cycle_impulse(
        self,
        opportunity=opportunity,
        risk=risk,
        impulse_leader=impulse_leader,
        slow_market_legs=slow_market_legs,
    )
    evidence = (
        market_aligned
        and opportunity is Opportunity.STRONG_TREND
        and risk.votes <= 1
        and credible >= self.cfg.leader_cycle_min_mature
    )
    exceptional_recovery_rearm = _exceptional_recovery_rearm(
        self,
        opportunity=opportunity,
        risk=risk,
        leaders=leaders,
        account=account,
        market_aligned=market_aligned,
        credible=credible,
    )
    if exceptional_recovery_rearm:
        # The completed cooldown and exceptional current evidence close a
        # recovery cycle without weakening the ordinary leader contract.
        account.candidate_tenure[arm_key] = 1
        account.candidate_tenure[streak_key] = 0
        account.candidate_tenure["recovery_cycle_rearm_pending"] = 0
        return True
    account.candidate_tenure[streak_key] = account.candidate_tenure.get(streak_key, 0) + 1 if evidence else 0
    if strategic_handoff_ready and evidence:
        # The completed cooldown is persistent cross-cycle confirmation.
        # Transfer only one admission tranche on its first healthy open
        # session; dynamic-K owns every later expansion.
        account.candidate_tenure[arm_key] = 1
        account.candidate_tenure["leader_cycle_staged_handoff"] = 1
        return True
    if account.candidate_tenure[streak_key] >= self.cfg.leader_cycle_confirm_days or impulse:
        account.candidate_tenure[arm_key] = 1
    return account.candidate_tenure.get(arm_key, 0) == 1


def _retention_score(
    symbol: str,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> float:
    """Protect proven winners when K contracts or a challenger appears."""
    position = account.positions.get(symbol)
    if position is None:
        return leaders[symbol].score
    peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
    winner_bonus = min(0.20, 0.50 * max(0.0, peak_mfe))
    return leaders[symbol].score + winner_bonus


def _leader_lifecycle_exit_confirmed(
    self: LeaderPortfolioPolicy,
    *,
    symbol: str,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> bool:
    """Reuse the existing per-symbol damage confirmation across owner gaps."""
    position = account.positions.get(symbol)
    frame = user_panel.get(symbol)
    leader = leaders.get(symbol)
    key = f"lifecycle_exit:{symbol}"
    if position is None or position.shares <= 0 or frame is None or date not in frame.index or leader is None:
        account.replacement_tenure[key] = 0
        return False
    row = frame.loc[date]
    peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
    protected_winner = peak_mfe >= 0.20
    broken = bool(
        not leader.mature
        and scalar(row, "close")
        < scalar(
            row,
            f"ma{self.cfg.trend_medium if protected_winner else self.cfg.trend_fast}",
        )
        and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) <= (-0.15 if protected_winner else -0.08)
    )
    account.replacement_tenure[key] = account.replacement_tenure.get(key, 0) + 1 if broken else 0
    held_sessions = len(frame.loc[pd.Timestamp(position.entry_date) : date]) if position.entry_date else 0
    return bool(
        account.replacement_tenure[key] >= self.cfg.replacement_confirm_days
        and held_sessions >= self.cfg.min_hold_days
    )


def _industry_handoff(
    self: LeaderPortfolioPolicy,
    *,
    challenger: LeaderScore,
    incumbent: LeaderScore,
) -> bool:
    """Confirm a cross-industry hand-off from independent breadth evidence."""
    if (
        not self.cfg.industry_rotation_enabled
        or challenger.industry == incumbent.industry
        or challenger.industry == "unknown"
        or challenger.components.get("unknown_industry", 0.0) >= 0.5
    ):
        return False
    challenger_strength = challenger.components.get("industry_rotation_strength", 0.5)
    incumbent_strength = incumbent.components.get("industry_rotation_strength", 0.5)
    challenger_confidence = challenger.components.get("industry_confidence", 0.0)
    incumbent_breadth = incumbent.components.get("industry_breadth20", 0.0)
    return bool(
        challenger_strength >= self.cfg.industry_rotation_min_score
        and challenger_confidence >= self.cfg.industry_rotation_min_confidence
        and challenger_strength - incumbent_strength >= self.cfg.industry_rotation_edge
        and (
            incumbent_strength <= self.cfg.industry_rotation_deterioration
            or incumbent_breadth <= self.cfg.industry_rotation_breadth
        )
    )


industry_handoff = _industry_handoff
leader_lifecycle_exit_confirmed = _leader_lifecycle_exit_confirmed
leader_retention_score = _retention_score
leader_rotation_allowed = _rotation_allowed
leader_session_clock = _session_clock
leader_session_distance = _leader_session_distance
update_leader_cycle_arm = _update_leader_cycle_arm
