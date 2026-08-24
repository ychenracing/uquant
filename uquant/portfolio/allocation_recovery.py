"""Recovery trailing, re-arm, admission, and bounded-repair routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Risk,
    RiskAssessment,
    Target,
)
from .context import AllocationState
from .recovery.admission import recovery_admission_targets as run_recovery_admission

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _trailed_recovery_winners(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
) -> tuple[dict[str, float], list[str], bool]:
    weights_now = state.weights_now
    confirmed_recovery_trail = state.confirmed_recovery_trail
    confirmed_hard_risk_trail = state.confirmed_hard_risk_trail
    anchored_held = {
        symbol: weights_now.get(symbol, 0.0)
        for symbol in account.anchor_weights
        if weights_now.get(symbol, 0.0) > 0
    }
    trailed_winners: list[str] = []
    trail_allowed = confirmed_recovery_trail or confirmed_hard_risk_trail
    hard_trail_prefix = "hard_risk_winner_trail:"
    live_gross = sum(max(0.0, weight) for weight in weights_now.values())
    hard_trail_cap_satisfied = bool(live_gross <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12)
    if not confirmed_hard_risk_trail:
        for key in tuple(account.replacement_tenure):
            if key.startswith(hard_trail_prefix):
                account.replacement_tenure[key] = 0
    if trail_allowed:
        for symbol in sorted(anchored_held):
            recovery_position = account.positions.get(symbol)
            price = prices.get(symbol, 0.0)
            if recovery_position is None or price <= 0 or recovery_position.avg_cost <= 0:
                account.replacement_tenure[f"{hard_trail_prefix}{symbol}"] = 0
                continue
            mfe = recovery_position.highest_close / recovery_position.avg_cost - 1.0
            peak_giveback = price / max(recovery_position.highest_close, 1e-12) - 1.0
            trail_observed = bool(
                mfe >= self.cfg.recovery_winner_mfe_arm and peak_giveback <= -self.cfg.recovery_winner_trail
            )
            hard_trail_key = f"{hard_trail_prefix}{symbol}"
            hard_trail_pending = False
            if confirmed_hard_risk_trail:
                prior_hard_trail = account.replacement_tenure.get(hard_trail_key, 0)
                hard_trail_pending = prior_hard_trail > 0
                account.replacement_tenure[hard_trail_key] = (
                    prior_hard_trail + 1 if trail_observed or hard_trail_pending else 0
                )
            if not trail_observed and not hard_trail_pending:
                continue
            if (
                confirmed_hard_risk_trail
                and account.replacement_tenure[hard_trail_key] < self.cfg.concentrated_break_confirm_days
            ):
                # The outer sparse reducer still enforces the hard gross
                # cap immediately.  A permanent member exit additionally
                # waits for the next session to confirm that the same hard
                # portfolio risk persists; a prior cap trim is not that
                # second observation.
                continue
            trailed_winners.append(symbol)
    return anchored_held, trailed_winners, hard_trail_cap_satisfied


def _winner_trail_targets(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    anchored_held: dict[str, float],
    trailed_winners: list[str],
    hard_trail_cap_satisfied: bool,
) -> tuple[Target, ...] | None:
    confirmed_hard_risk_trail = state.confirmed_hard_risk_trail
    if trailed_winners:
        proposed = {
            symbol: (
                min(weight, account.anchor_weights.get(symbol, weight))
                if confirmed_hard_risk_trail and not hard_trail_cap_satisfied
                else weight
            )
            for symbol, weight in anchored_held.items()
            if symbol not in trailed_winners
        }
        reasons = {
            symbol: (
                "recovery winner peak-giveback exit"
                if symbol in trailed_winners
                else "mature anchored leader"
            )
            for symbol in anchored_held
        }
        for symbol in trailed_winners:
            account.anchor_weights.pop(symbol, None)
            account.protected_weights.pop(symbol, None)
            account.strategic_restore_weights.pop(symbol, None)
        if not account.anchor_weights:
            self._release_recovery_anchor(account)
            account.candidate_tenure["tactical_cooldown"] = max(
                account.candidate_tenure.get("tactical_cooldown", 0),
                self.cfg.tactical_rebound_cooldown_days,
            )
            account.candidate_tenure["tactical_overheat_cooldown"] = 0
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="mature anchored leader",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.RECOVERY_COHORT,
            reasons=reasons,
        )
    return None


def _trail_recovery_anchors(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
) -> tuple[dict[str, float], tuple[Target, ...] | None]:
    anchored_held, trailed_winners, hard_trail_cap_satisfied = _trailed_recovery_winners(
        self,
        risk=risk,
        account=account,
        prices=prices,
        state=state,
    )
    targets = _winner_trail_targets(
        self,
        leaders=leaders,
        account=account,
        state=state,
        anchored_held=anchored_held,
        trailed_winners=trailed_winners,
        hard_trail_cap_satisfied=hard_trail_cap_satisfied,
    )
    return anchored_held, targets


def _owner_rearm_is_open(*, risk: RiskAssessment, account: AccountState) -> bool:
    return bool(
        account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
        and bool(account.anchor_weights)
        and risk.state is Risk.NORMAL
        and risk.shock_state == "NONE"
        and not risk.freeze_new_risk
        and not bool(risk.evidence.get("freeze_new_risk", False))
        and account.capital_budget_level == 0
        and account.chronic_level == 0
        and not account.protected_weights
        and not account.strategic_restore_weights
        and not account.strategic_cohort_targets
    )


def _allocate_owner_rearm(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    anchored_held: dict[str, float],
) -> tuple[Target, ...] | None:
    if not _owner_rearm_is_open(risk=risk, account=account):
        return None
    weights_now = state.weights_now
    owner_targets = {
        symbol: min(self.cfg.max_symbol_weight, max(0.0, weight))
        for symbol, weight in account.anchor_weights.items()
        if symbol in user_panel
    }
    explicit_cap = min(
        self.cfg.max_gross,
        max(0.0, risk.target_gross_cap),
    )
    target_gross = sum(owner_targets.values())
    if target_gross > explicit_cap and target_gross > 0:
        owner_targets = {
            symbol: weight * explicit_cap / target_gross for symbol, weight in owner_targets.items()
        }
    pending_owner_buys = {
        order.symbol
        for order in account.pending_orders
        if order.side == "BUY" and order.symbol in owner_targets
    }
    rearm_submitted_key = "recovery_owner_rearm_submitted"
    previously_submitted = bool(account.candidate_tenure.get(rearm_submitted_key, 0) == 1)
    rearm_complete = bool(
        previously_submitted
        and not pending_owner_buys
        and all(
            desired - weights_now.get(symbol, 0.0) + 1e-12 < self.cfg.restoration_min_trade_weight
            or (
                desired - weights_now.get(symbol, 0.0) < self.cfg.min_trade_weight
                and weights_now.get(symbol, 0.0) >= 0.95 * desired
            )
            for symbol, desired in owner_targets.items()
        )
    )
    if rearm_complete:
        account.candidate_tenure["recovery_owner_handoff"] = 0
        account.candidate_tenure[rearm_submitted_key] = 0
        account.candidate_tenure["recovery_owner_rearm_complete"] = 1
        return self._targets(
            proposed=anchored_held,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="completed recovery owner rearm; retain price drift",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.RECOVERY_REARM,
        )
    current_owner = {symbol: max(0.0, weights_now.get(symbol, 0.0)) for symbol in owner_targets}
    buy_gaps = {
        symbol: max(0.0, desired - current_owner[symbol]) for symbol, desired in owner_targets.items()
    }
    requested = sum(buy_gaps.values())
    remaining = max(0.0, explicit_cap - sum(current_owner.values()))
    scale = min(1.0, remaining / requested) if requested > 0 else 0.0
    proposed = {
        symbol: current_owner[symbol] + buy_gaps[symbol] * scale
        for symbol in owner_targets
        if current_owner[symbol] + buy_gaps[symbol] * scale > 1e-12
    }
    account.candidate_tenure[rearm_submitted_key] = 1
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="confirmed recovery owner capital rearm",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.RECOVERY_REARM,
    )


def _recovery_market(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    anchored_held: dict[str, float],
    leader_cycle_armed: bool,
) -> tuple[int, bool, bool, bool, bool]:
    anchor_elapsed = 0
    if account.recovery_anchor_date and user_panel:
        anchor_elapsed = self._session_distance(
            self._session_clock(user_panel, date),
            account.recovery_anchor_date,
            date,
        )
    broad_ret120 = float(risk.evidence.get("broad_ret120", 0.0))
    tech_ret120 = float(risk.evidence.get("tech_ret120", 0.0))
    market_ret120_low = min(broad_ret120, tech_ret120)
    market_ret120_high = max(broad_ret120, tech_ret120)
    weak_secular_market = market_ret120_high <= self.cfg.recovery_cohort_weak_market_ret120
    transitional_recovery_market = bool(
        market_ret120_low <= self.cfg.recovery_transition_weak_leg_ret120
        and market_ret120_high <= self.cfg.recovery_transition_strong_leg_max_ret120
        and market_ret120_high - market_ret120_low >= self.cfg.recovery_transition_min_divergence
    )
    tactical_recovery_market = weak_secular_market or transitional_recovery_market
    graduation_days = (
        self.cfg.recovery_cohort_weak_graduation_days
        if weak_secular_market
        else self.cfg.recovery_cohort_graduation_days
    )
    graduation_ready = (
        bool(anchored_held)
        and account.candidate_tenure.get("recovery_cohort_graduated", 0) == 0
        and anchor_elapsed >= graduation_days
        and risk.state.value in {"NORMAL", "CAUTION"}
        and opportunity in {Opportunity.CHOPPY, Opportunity.TREND, Opportunity.STRONG_TREND}
        and (
            leader_cycle_armed
            or (
                account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
                and account.candidate_tenure.get("tactical_promoted", 0) == 0
            )
        )
    )
    return (
        anchor_elapsed,
        tactical_recovery_market,
        transitional_recovery_market,
        weak_secular_market,
        graduation_ready,
    )


def _graduate_recovery(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    graduation_ready: bool,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    if graduation_ready:
        # Graduation changes lifecycle ownership; it is not an exit for a
        # different live Core position.  Preserve the whole broker book on
        # the hand-off day so omitted targets cannot manufacture a sale.
        promoted = {symbol: weight for symbol, weight in weights_now.items() if weight > 1e-12}
        account.active_leaders = sorted(
            (symbol for symbol in promoted if symbol in leaders),
            key=lambda symbol: (-leaders[symbol].score, symbol),
        )
        self._release_recovery_anchor(account)
        return self._targets(
            proposed=promoted,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="graduated recovery cohort; retain price drift",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.RECOVERY_REARM,
        )
    return None


def _mature_recovery_anchor(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    anchored_held: dict[str, float],
    anchor_elapsed: int,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    substitution = self._recovery_anchor_substitution(
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        weights_now=weights_now,
        anchor_elapsed=anchor_elapsed,
    )
    if substitution is not None:
        return substitution
    if (
        anchored_held
        and len(anchored_held) == len(account.anchor_weights)
        and len(account.anchor_weights) >= min(3, self.cfg.max_positions)
        and (
            account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
            or anchor_elapsed > self.cfg.recovery_add_window_days
        )
        and not any(
            order.side == "BUY" and order.symbol in account.anchor_weights for order in account.pending_orders
        )
    ):
        return self._targets(
            proposed=anchored_held,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="mature anchored leader",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.RECOVERY_COHORT,
        )
    return None


def _admit_recovery(
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
    anchored_held: dict[str, float],
    tactical_recovery_market: bool,
    transitional_recovery_market: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    freeze_active = state.freeze_active
    general_core_symbols = state.general_core_symbols
    risk_neutral_recovery_handoff = state.risk_neutral_recovery_handoff
    risk_neutral_recovery_transfer = state.risk_neutral_recovery_transfer
    level1_recovery_repair = state.level1_recovery_repair
    bounded_recovery_repair = state.bounded_recovery_repair
    recovery_admission_targets = run_recovery_admission(
        self=self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        weights_now=weights_now,
        anchored_held=anchored_held,
        bounded_recovery_repair=bounded_recovery_repair,
        freeze_active=freeze_active,
        general_core_symbols=general_core_symbols,
        level1_recovery_repair=level1_recovery_repair,
        risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
        risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
        tactical_recovery_market=tactical_recovery_market,
        transitional_recovery_market=transitional_recovery_market,
        weak_secular_market=weak_secular_market,
    )
    if recovery_admission_targets is not None:
        return recovery_admission_targets
    anchored_held = {
        symbol: weights_now.get(symbol, 0.0)
        for symbol in account.anchor_weights
        if weights_now.get(symbol, 0.0) > 0
    }
    if anchored_held:
        anchored_held, capped = self._cap_underdiversified(anchored_held, account)
        return self._targets(
            proposed=anchored_held,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="under-diversified recovery cap" if capped else "mature anchored leader",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=(AttributionMechanism.RECOVERY_CAP if capped else AttributionMechanism.RECOVERY_COHORT),
        )
    return None


def _bounded_recovery_fallback(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    freeze_active = state.freeze_active
    bounded_recovery_repair = state.bounded_recovery_repair
    if freeze_active and bounded_recovery_repair:
        if any(position.shares > 0 for position in account.positions.values()):
            # The bounded exception reopens only a confirmed recovery BUY;
            # failing to find one does not manufacture an exit for a live
            # generic owner. Preserve existing exposure (and any durable
            # pending reduction) until risk or the strategy explicitly
            # emits a sell.
            return self._frozen_existing_targets(
                strategy_targets=None,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
            )
        return self._targets(
            proposed={},
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="confirmed repair has no bounded recovery candidate",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.RECOVERY_COHORT,
        )
    return None


def _allocate_recovery_route(
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
    anchored_held: dict[str, float],
    leader_cycle_armed: bool,
) -> tuple[Target, ...] | None:
    (
        anchor_elapsed,
        tactical_recovery_market,
        transitional_recovery_market,
        weak_secular_market,
        graduation_ready,
    ) = _recovery_market(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        account=account,
        anchored_held=anchored_held,
        leader_cycle_armed=leader_cycle_armed,
    )
    targets = _graduate_recovery(
        self,
        leaders=leaders,
        account=account,
        state=state,
        graduation_ready=graduation_ready,
    )
    if targets is not None:
        return targets
    targets = _mature_recovery_anchor(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
        anchored_held=anchored_held,
        anchor_elapsed=anchor_elapsed,
    )
    if targets is not None:
        return targets
    targets = _admit_recovery(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
        anchored_held=anchored_held,
        tactical_recovery_market=tactical_recovery_market,
        transitional_recovery_market=transitional_recovery_market,
        weak_secular_market=weak_secular_market,
    )
    if targets is not None:
        return targets
    return _bounded_recovery_fallback(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        state=state,
    )


def allocate_recovery(
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
    leader_cycle_armed: bool,
) -> tuple[Target, ...] | None:
    """Evaluate recovery owners without changing their mutation order."""

    anchored_held, targets = _trail_recovery_anchors(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
    )
    if targets is not None:
        return targets
    targets = _allocate_owner_rearm(
        self,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
        anchored_held=anchored_held,
    )
    if targets is not None:
        return targets
    return _allocate_recovery_route(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
        anchored_held=anchored_held,
        leader_cycle_armed=leader_cycle_armed,
    )
