"""Freeze and tactical allocation routes in their original call order."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ..features import scalar
from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Position,
    Risk,
    RiskAssessment,
    Target,
)
from .context import AllocationState

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _allocate_frozen(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    freeze_active = state.freeze_active
    bounded_recovery_repair = state.bounded_recovery_repair
    reason_clean_caution_anchor_cap = state.reason_clean_caution_anchor_cap

    if freeze_active:
        # A confirmed recovery-anchor substitution may still identify its
        # structurally broken sell leg. The freeze overlay below clamps the
        # replacement leg to live exposure, so it cannot create a BUY.
        if risk.state is Risk.CAUTION:
            anchor_elapsed = 0
            if account.recovery_anchor_date:
                anchor_elapsed = self._session_distance(
                    self._session_clock(user_panel, date),
                    account.recovery_anchor_date,
                    date,
                )
            substitution = self._recovery_anchor_substitution(
                date=date,
                risk=risk,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
                anchor_elapsed=anchor_elapsed,
                risk_neutral_only=True,
            )
            if substitution is not None:
                return substitution
        if reason_clean_caution_anchor_cap:
            anchored_held = {
                symbol: weights_now.get(symbol, 0.0)
                for symbol in account.anchor_weights
                if weights_now.get(symbol, 0.0) > 0
            }
            capped_anchors, capped = self._cap_underdiversified(anchored_held, account)
            if capped:
                cap_targets = self._targets(
                    proposed=capped_anchors,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="under-diversified recovery cap",
                    origin_subsystem=OriginSubsystem.RECOVERY,
                    mechanism=AttributionMechanism.RECOVERY_CAP,
                )
                return self._frozen_existing_targets(
                    strategy_targets=cap_targets,
                    leaders=leaders,
                    account=account,
                    weights_now=weights_now,
                )
        # A freeze is not an implicit exit. A durable reduction remains
        # executable through every freeze, but unfinished additions stop
        # until risk explicitly reopens.  Confirmed level-1/chronic repair
        # and an independently observable empty-book crash probe are the
        # bounded exceptions; neither can reach generic leader admission.
        if not bounded_recovery_repair:
            return self._frozen_existing_targets(
                strategy_targets=None,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
            )
    return None


def _arm_leader_cycle(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> bool:
    strategic_handoff_pending = bool(
        account.strategic_epochs_completed > 0
        and account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
        and account.candidate_tenure.get("leader_cycle_handoff_epoch", 0) < account.strategic_epochs_completed
        and account.strategic_rearm_date
        and not account.positions
        and not account.pending_orders
        and not account.anchor_weights
        and not account.protected_weights
    )
    strategic_handoff_ready = bool(
        strategic_handoff_pending
        and date.normalize() >= pd.Timestamp(account.strategic_rearm_date).normalize()
    )
    leader_cycle_armed = self._update_leader_cycle_arm(
        opportunity=opportunity,
        risk=risk,
        leaders=leaders,
        account=account,
        strategic_handoff_blocked=(strategic_handoff_pending and not strategic_handoff_ready),
        strategic_handoff_ready=strategic_handoff_ready,
    )
    return leader_cycle_armed


def _advance_tactical_cooldown(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
) -> None:
    cooldown = account.candidate_tenure.get("tactical_cooldown", 0)
    if cooldown > 0:
        remaining_cooldown = cooldown - 1
        if (
            account.candidate_tenure.get("tactical_overheat_cooldown", 0) == 1
            and not account.positions
            and any(
                date in frame.index
                and scalar(frame.loc[date], "ret5", -1.0) >= self.cfg.fast_v_recovery_return
                and scalar(frame.loc[date], "ret20", 0.0) <= self.cfg.tactical_rebound_breadth_max_ret20
                and scalar(frame.loc[date], "ret60", -1.0) >= self.cfg.tactical_rebound_min_ret60
                and scalar(frame.loc[date], "close", 0.0)
                >= scalar(frame.loc[date], f"ma{self.cfg.trend_slow}", math.inf)
                for frame in user_panel.values()
            )
        ):
            # An overheat pause belongs to the falling candidate, not to
            # unrelated opportunities.  A fresh positive five-session
            # reversal with medium-term convexity closes that pause; the
            # ordinary admission gates below still decide whether to buy.
            remaining_cooldown = 0
        account.candidate_tenure["tactical_cooldown"] = remaining_cooldown
        if remaining_cooldown == 0:
            account.candidate_tenure["tactical_overheat_cooldown"] = 0


def _active_tactical_positions(
    account: AccountState,
) -> tuple[list[Position], bool]:
    tactical = (
        [
            position
            for position in account.positions.values()
            if position.shares > 0
            and position.lifecycle == Lifecycle.RECOVERY.value
            and (not account.tactical_anchor_symbol or position.symbol == account.tactical_anchor_symbol)
        ]
        if account.candidate_tenure.get("tactical_active", 0) == 1
        else []
    )
    promotable_tactical = bool(
        tactical
        and not account.anchor_weights
        and account.candidate_tenure.get("tactical_promotable", 0) == 1
        and account.tactical_anchor_symbol == tactical[0].symbol
    )
    return tactical, promotable_tactical


def _promote_tactical_recovery(
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    account: AccountState,
    weights_now: dict[str, float],
    tactical: list[Position],
    promotable_tactical: bool,
) -> None:
    if promotable_tactical and opportunity is Opportunity.RECOVERY:
        position = tactical[0]
        # A deep-crisis probe that survives until causal recovery confirmation
        # becomes the first core tranche.  Keeping the same shares avoids the
        # economically pointless sell/rebuy pair that previously inflated
        # account orders and discarded the best entry price.
        account.anchor_weights = {position.symbol: weights_now.get(position.symbol, 0.0)}
        account.recovery_anchor_date = str(date.date())
        account.candidate_tenure["recovery_reserve_qualified"] = 0
        account.candidate_tenure["recovery_substitution_pending"] = 0
        account.candidate_tenure["recovery_substitution_completed"] = 0
        account.candidate_tenure["recovery_cohort_graduated"] = 0
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promoted"] = 1
        position.lifecycle = Lifecycle.CORE.value
        for tranche in position.tranches:
            tranche.lifecycle = Lifecycle.CORE.value


def _graduate_tactical_leader(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    tactical: list[Position],
) -> list[Position]:
    tactical_leader_graduation = bool(
        tactical
        and not account.anchor_weights
        and risk.state.value in {"NORMAL", "CAUTION"}
        and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        and tactical[0].symbol in leaders
        and leaders[tactical[0].symbol].mature
        and leaders[tactical[0].symbol].confidence >= self.cfg.leader_min_confidence
        and self._structure_ok(user_panel[tactical[0].symbol], date)
    )
    if tactical_leader_graduation:
        position = tactical[0]
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promoted"] = 1
        account.tactical_anchor_symbol = ""
        if position.symbol not in account.active_leaders:
            account.active_leaders.append(position.symbol)
        position.lifecycle = Lifecycle.CORE.value
        for tranche in position.tranches:
            tranche.lifecycle = Lifecycle.CORE.value
        tactical = []
    return tactical


def _allocate_tactical_position(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    tactical: list[Position],
    promotable_tactical: bool,
) -> tuple[Target, ...] | None:
    if (
        tactical
        and not account.anchor_weights
        and risk.state.value != "CRISIS"
        and not (account.protected_weights and risk.shock_state == "RECOVERY")
    ):
        position = tactical[0]
        pnl = prices.get(position.symbol, 0.0) / max(position.avg_cost, 1e-12) - 1.0
        held_sessions = len(user_panel[position.symbol].loc[pd.Timestamp(position.entry_date) : date])
        exit_due = (
            held_sessions >= 30
            if promotable_tactical
            else pnl >= self.cfg.tactical_rebound_take_profit or held_sessions >= 12
        )
        if exit_due:
            # This is a final strategy exit, not a temporary risk trim.
            # Any saved restore intent for the same tactical position must
            # retire atomically; otherwise an already sold probe can leave
            # ``protected_weights`` alive forever and block every later
            # strategic cohort in a long replay.
            account.protected_weights.pop(position.symbol, None)
            account.strategic_restore_weights.pop(position.symbol, None)
            account.candidate_tenure["tactical_active"] = 0
            account.candidate_tenure["tactical_cooldown"] = self.cfg.tactical_rebound_cooldown_days
            account.candidate_tenure["tactical_overheat_cooldown"] = 0
            account.candidate_tenure["recovery_cycle_rearm_pending"] = 1
            account.tactical_anchor_symbol = ""
            return self._targets(
                proposed={},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="controlled rebound exit",
                origin_subsystem=OriginSubsystem.RECOVERY,
                mechanism=AttributionMechanism.TACTICAL_REBOUND,
            )
        pending_buy = next(
            (
                order
                for order in account.pending_orders
                if order.symbol == position.symbol and order.side == "BUY"
            ),
            None,
        )
        safe_partial_completion = bool(
            pending_buy is not None
            and risk.votes <= 2
            and float(risk.evidence.get("transition_damage", 1.0)) < self.cfg.transition_damage_freeze
        )
        held_target = weights_now.get(position.symbol, 0.0)
        if safe_partial_completion and pending_buy is not None:
            held_target = max(held_target, pending_buy.target_weight)
        return self._targets(
            proposed={position.symbol: held_target},
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="controlled rebound probe",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.TACTICAL_REBOUND,
        )
    return None


def _allocate_crisis(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    confirmed_hard_risk_trail: bool,
) -> tuple[Target, ...] | None:
    if risk.state.value == "CRISIS" and not confirmed_hard_risk_trail:
        if account.anchor_weights:
            account.candidate_tenure["risk_trimmed"] = 1
        # Preserve the live economic targets here.  The single outer
        # sparse reducer owns every gross-cap cut, including CRISIS; doing
        # a proportional pre-scale here manufactured one sell per symbol
        # and defeated late-add-first retention.
        proposed = {symbol: weight for symbol, weight in weights_now.items() if weight > 0}
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason=(
                "severe crisis capital protection"
                if risk.target_gross_cap <= 0
                else "graded crisis risk reduction"
            ),
            origin_subsystem=OriginSubsystem.RISK,
            mechanism=AttributionMechanism.CRISIS,
        )
    return None


def _allocate_tactical_book(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    _advance_tactical_cooldown(self, date=date, user_panel=user_panel, account=account)
    tactical, promotable_tactical = _active_tactical_positions(account)
    _promote_tactical_recovery(
        date=date,
        opportunity=opportunity,
        account=account,
        weights_now=weights_now,
        tactical=tactical,
        promotable_tactical=promotable_tactical,
    )
    tactical = _graduate_tactical_leader(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        tactical=tactical,
    )
    targets = _allocate_tactical_position(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        weights_now=weights_now,
        tactical=tactical,
        promotable_tactical=promotable_tactical,
    )
    if targets is not None:
        return targets
    return _allocate_crisis(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        weights_now=weights_now,
        confirmed_hard_risk_trail=state.confirmed_hard_risk_trail,
    )


def allocate_tactical(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
) -> tuple[tuple[Target, ...] | None, bool]:
    """Evaluate freeze and tactical routes before protected restoration."""

    targets = _allocate_frozen(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
    )
    if targets is not None:
        return targets, False
    leader_cycle_armed = _arm_leader_cycle(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        leaders=leaders,
        account=account,
    )
    targets = _allocate_tactical_book(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
    )
    return targets, leader_cycle_armed
