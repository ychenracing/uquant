"""Opening allocation conditions and strategic-owner handoff."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ..portfolio_core import current_weights
from ..types import AccountState, LeaderScore, Lifecycle, Opportunity, Risk, RiskAssessment, Target
from .context import AllocationState

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _initialize_allocation(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
    prices: dict[str, float],
) -> tuple[dict[str, float], float, bool, bool, set[str]]:
    weights_now, equity = current_weights(account, prices)
    self._release_stale_recovery_anchor(
        risk=risk,
        account=account,
        weights_now=weights_now,
    )
    failed_restoration = bool(
        risk.state is Risk.CRISIS
        and any(
            marker in risk.reasons
            for marker in (
                "capital drawdown relapse in restored holdings",
                "market-backed portfolio break in incomplete restoration",
                "capital guard cooldown after failed restoration",
            )
        )
    )
    if failed_restoration:
        # A failed economic restore is a final lifecycle break, not another
        # temporary cap.  Settle every restoration owner before the
        # strategic early-return path can recapture and later resurrect the
        # same cohort.
        self._release_recovery_anchor(account)
        account.protected_weights.clear()
        for symbol in tuple(account.strategic_cohort_targets):
            self._retire_strategic_member(account, symbol)
        account.candidate_tenure["post_shock_restore_complete"] = 0
    freeze_active = bool(
        risk.freeze_new_risk
        or risk.evidence.get("freeze_new_risk", False)
        or risk.state in {Risk.RISK_OFF, Risk.CRISIS}
    )
    repair_observation = bool(
        risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.reduction_level <= 1
        and risk.votes <= 1
        and float(risk.evidence.get("transition_damage", math.inf)) <= self.cfg.transition_damage_repair
    )
    general_core_symbols = {
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0
        and position.lifecycle
        in {
            Lifecycle.CORE.value,
            Lifecycle.ADD1.value,
            Lifecycle.ADD2.value,
            Lifecycle.SATELLITE.value,
        }
    }
    return weights_now, equity, freeze_active, repair_observation, general_core_symbols


def _risk_neutral_handoff(
    self: PortfolioAllocator,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    account: AccountState,
    weights_now: dict[str, float],
    freeze_active: bool,
    general_core_symbols: set[str],
) -> bool:
    return bool(
        opportunity is Opportunity.RECOVERY
        and freeze_active
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.shock_state in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
        and risk.votes <= 1
        and float(risk.evidence.get("transition_damage", math.inf)) <= self.cfg.transition_damage_repair
        and bool(account.last_shock_date)
        and account.capital_budget_level >= 1
        and bool(general_core_symbols)
        and not account.pending_orders
        and not account.anchor_weights
        and set(account.protected_weights) <= general_core_symbols
        and not account.strategic_restore_weights
        and not account.strategic_cohort_targets
        and sum(max(0.0, weight) for weight in weights_now.values())
        <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
    )


def _risk_neutral_expansion(
    self: PortfolioAllocator,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    account: AccountState,
    weights_now: dict[str, float],
    freeze_active: bool,
) -> bool:
    return bool(
        opportunity is Opportunity.RECOVERY
        and freeze_active
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.shock_state in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
        and risk.votes <= 1
        and float(risk.evidence.get("transition_damage", math.inf)) <= self.cfg.transition_damage_repair
        and bool(account.last_shock_date)
        and account.capital_budget_level >= 1
        and account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
        and bool(account.anchor_weights)
        and not account.pending_orders
        and not account.protected_weights
        and not account.strategic_restore_weights
        and not account.strategic_cohort_targets
        and sum(max(0.0, weight) for weight in weights_now.values())
        <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
    )


def _recovery_transfer_conditions(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
    freeze_active: bool,
    repair_observation: bool,
    risk_neutral_recovery_handoff: bool,
    risk_neutral_recovery_expansion: bool,
) -> tuple[bool, bool, bool, bool]:
    risk_neutral_recovery_transfer = bool(risk_neutral_recovery_handoff or risk_neutral_recovery_expansion)
    level1_recovery_repair = bool(
        freeze_active
        and repair_observation
        and account.capital_budget_level == 1
        and account.capital_budget_repair_streak >= 2
    )
    protected_level1_restore = bool(
        freeze_active
        and repair_observation
        and risk.state is Risk.CAUTION
        and risk.shock_state in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
        and account.capital_budget_level == 1
        and account.capital_budget_repair_streak >= 1
        and bool(account.protected_weights or account.strategic_restore_weights)
    )
    synchronized_protected_restore = bool(
        freeze_active
        and risk.state is Risk.CAUTION
        and risk.shock_state == "RECOVERY"
        and "two-day synchronized leader repair" in risk.reasons
        and account.capital_budget_level <= 1
        and account.chronic_level <= 1
        and bool(account.protected_weights)
    )
    return (
        risk_neutral_recovery_transfer,
        level1_recovery_repair,
        protected_level1_restore,
        synchronized_protected_restore,
    )


def _repair_locked_cohort(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    synchronized_protected_restore: bool,
) -> bool:
    user_repair_industries = {
        score.industry
        for score in leaders.values()
        if score.industry != "unknown" and score.confidence >= self.cfg.leader_min_confidence
    }
    incumbent_repair_industries = {
        leaders[symbol].industry
        for symbol in account.anchor_weights
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    independent_user_repair_industries = user_repair_industries - incumbent_repair_industries
    unsupported_locked_restore = bool(
        synchronized_protected_restore
        and account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
        # A homogeneous recovery cohort is already three-name internal
        # confirmation even when the wider opportunity set contains
        # unrelated industries. Requiring leaders outside that cohort
        # would make restoration depend on symbols that never owned the
        # crash decision. Genuinely mixed-industry cohorts still require
        # independent user-side breadth before missing members are rebought.
        and len(incumbent_repair_industries) != 1
        and len(independent_user_repair_industries) < self.cfg.strategic_cohort_min_size
    )
    if unsupported_locked_restore:
        # Reference sentinels can confirm that market stress repaired, but
        # they cannot manufacture breadth inside the user's opportunity
        # set. A concentrated cohort whose missing members lack three
        # independent user industries remains bounded in place: keep the
        # live survivor, discard only stale rebuy rights, and require later
        # expansion to clear ordinary admission again.
        live_anchors = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in account.anchor_weights
            if weights_now.get(symbol, 0.0) > 1e-12
        }
        removed_anchors = set(account.anchor_weights) - set(live_anchors)
        account.anchor_weights = live_anchors
        account.protected_weights.clear()
        for symbol in removed_anchors:
            account.strategic_restore_weights.pop(symbol, None)
        account.candidate_tenure["recovery_cohort_locked"] = 0
        account.candidate_tenure["post_shock_restore_complete"] = 1
        account.candidate_tenure["recovery_substitution_pending"] = 0
        synchronized_protected_restore = False
    return synchronized_protected_restore


def _caution_recovery_trail(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
    freeze_active: bool,
) -> tuple[bool, bool, int]:
    bounded_caution_recovery_probe = bool(
        freeze_active
        and risk.state is Risk.CAUTION
        and account.capital_budget_level == 0
        and account.chronic_level == 0
        and not account.positions
        and not account.anchor_weights
        and not account.protected_weights
        and not account.strategic_restore_weights
    )
    live_anchor_count = sum(
        1
        for symbol in account.anchor_weights
        if account.positions.get(symbol) is not None and account.positions[symbol].shares > 0
    )
    required_damage_ratio = min(2, live_anchor_count) / live_anchor_count if live_anchor_count > 0 else 1.0
    unbroken_recovery_epoch = bool(
        not account.last_shock_date
        or (
            account.recovery_anchor_date
            and pd.Timestamp(account.recovery_anchor_date) > pd.Timestamp(account.last_shock_date)
        )
    )
    confirmed_recovery_trail = bool(
        freeze_active
        and risk.state is Risk.CAUTION
        and risk.votes >= 2
        and bool(account.anchor_weights)
        and not account.protected_weights
        # Once risk has broken and restored this same cohort, the risk
        # state machine owns any later relapse; the pre-shock winner trail
        # must not liquidate the durable restore a second time.
        and unbroken_recovery_epoch
        and float(risk.evidence.get("held_damage_ratio", 0.0)) >= required_damage_ratio - 1e-12
        and float(risk.evidence.get("sector_stress_ratio", 0.0)) >= 0.50
    )
    return bounded_caution_recovery_probe, confirmed_recovery_trail, live_anchor_count


def _hard_recovery_trail(
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    freeze_active: bool,
    live_anchor_count: int,
) -> tuple[bool, bool]:
    hard_risk_trail_signal = bool(
        freeze_active
        and risk.state in {Risk.RISK_OFF, Risk.CRISIS}
        and bool(account.anchor_weights)
        and any(
            marker in risk.reasons
            for marker in (
                "confirmed synchronized holdings shock",
                "confirmed dynamic cohort structural break",
            )
        )
    )
    anchor_industries = {
        leaders[symbol].industry
        for symbol in account.anchor_weights
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    if hard_risk_trail_signal and len(anchor_industries) >= 2:
        account.candidate_tenure["cross_industry_hard_risk_trail"] = 1
    confirmed_hard_risk_trail = bool(
        freeze_active
        and risk.state in {Risk.RISK_OFF, Risk.CRISIS}
        and bool(account.anchor_weights)
        and account.candidate_tenure.get("cross_industry_hard_risk_trail", 0) == 1
    )
    reason_clean_caution_anchor_cap = bool(
        freeze_active
        and risk.state is Risk.CAUTION
        and not risk.reasons
        and account.capital_budget_level == 0
        and account.chronic_level == 0
        and live_anchor_count == 1
        and bool(account.recovery_anchor_date)
        and not account.protected_weights
        and not account.strategic_restore_weights
    )
    return confirmed_hard_risk_trail, reason_clean_caution_anchor_cap


def _tactical_expiry_due(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    prices: dict[str, float],
    freeze_active: bool,
) -> bool:
    tactical_expiry_due = False
    if (
        freeze_active
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and not account.anchor_weights
        and account.candidate_tenure.get("tactical_active", 0) == 1
        and not (account.protected_weights and risk.shock_state == "RECOVERY")
    ):
        for position in account.positions.values():
            if (
                position.shares <= 0
                or position.lifecycle != Lifecycle.RECOVERY.value
                or (account.tactical_anchor_symbol and position.symbol != account.tactical_anchor_symbol)
                or position.symbol not in user_panel
            ):
                continue
            pnl = prices.get(position.symbol, 0.0) / max(position.avg_cost, 1e-12) - 1.0
            held_sessions = len(  # noqa: F841 - immutable statement-order projection
                user_panel[position.symbol].loc[pd.Timestamp(position.entry_date) : date]
            )
            promotable = bool(
                account.candidate_tenure.get("tactical_promotable", 0) == 1
                and account.tactical_anchor_symbol == position.symbol
            )
            # A caution freeze may not suppress an already-earned profit
            # exit.  A merely time-expired losing probe still waits for
            # the freeze to clear; otherwise the exception turns a risk
            # hold into a forced loss and can erase the recovery owner.
            tactical_expiry_due = bool(not promotable and pnl >= self.cfg.tactical_frozen_take_profit)
            break
    return tactical_expiry_due


def _bounded_recovery_conditions(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
    freeze_active: bool,
    repair_observation: bool,
    level1_recovery_repair: bool,
    protected_level1_restore: bool,
    synchronized_protected_restore: bool,
    bounded_caution_recovery_probe: bool,
    risk_neutral_recovery_transfer: bool,
    confirmed_recovery_trail: bool,
    confirmed_hard_risk_trail: bool,
    tactical_expiry_due: bool,
) -> tuple[bool, bool, bool]:
    bounded_recovery_repair = bool(
        level1_recovery_repair
        or protected_level1_restore
        or synchronized_protected_restore
        or bounded_caution_recovery_probe
        or risk_neutral_recovery_transfer
        # This exception can only submit strategy-owned SELLs from the
        # already deployed recovery book; it never opens new exposure.
        or confirmed_recovery_trail
        or confirmed_hard_risk_trail
        or tactical_expiry_due
        or (
            freeze_active
            and repair_observation
            and account.capital_budget_level <= 1
            and account.chronic_level >= 1
            and account.chronic_repair_streak >= 2
        )
    )
    strategic_live = account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    bounded_strategic_restore = bool(
        freeze_active
        and strategic_live
        and self._bounded_strategic_restore_risk_open(risk=risk, account=account)
    )
    return bounded_recovery_repair, strategic_live, bounded_strategic_restore


def _strategic_allocation(
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
    strategic_live: bool,
    bounded_strategic_restore: bool,
) -> tuple[Target, ...] | None:
    freeze_active = state.freeze_active
    weights_now = state.weights_now
    strategic_discovery_open = bool(
        opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        and risk.state is Risk.NORMAL
        and not freeze_active
        and not (
            account.candidate_tenure.get("recovery_cohort_locked", 0) == 1 and bool(account.anchor_weights)
        )
    )
    # RECOVERY remains owned by the crash-recovery policy. CHOPPY/WEAK are
    # observation-only for ordinary factor cohorts; the strategic policy
    # may admit there only through its separately confirmed persistent or
    # synchronized-reversal industry route. A live strategic cohort is
    # evaluated through every regime so its exits remain durable.
    strategic = (
        self._strategic_cohort_targets(
            date=date,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            prices=prices,
            weights_now=weights_now,
            admission_open=strategic_discovery_open,
        )
        # Qualification observation is read-only and therefore runs through
        # freeze, capital-budget, and risk-off states. Deployment remains
        # guarded inside the strategic policy and by the allocator's cap.
        if self.cfg.strategic_dynamic_enabled
        else None
    )
    if strategic is not None:
        if freeze_active and not bounded_strategic_restore:
            return self._frozen_existing_targets(
                strategy_targets=strategic,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
            )
        return strategic
    return None


def prepare_allocation(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
) -> tuple[AllocationState, tuple[Target, ...] | None]:
    weights_now, equity, freeze_active, repair_observation, general_core_symbols = _initialize_allocation(
        self, risk=risk, account=account, prices=prices
    )
    risk_neutral_recovery_handoff = _risk_neutral_handoff(
        self,
        opportunity=opportunity,
        risk=risk,
        account=account,
        weights_now=weights_now,
        freeze_active=freeze_active,
        general_core_symbols=general_core_symbols,
    )
    risk_neutral_recovery_expansion = _risk_neutral_expansion(
        self,
        opportunity=opportunity,
        risk=risk,
        account=account,
        weights_now=weights_now,
        freeze_active=freeze_active,
    )
    (
        risk_neutral_recovery_transfer,
        level1_recovery_repair,
        protected_level1_restore,
        synchronized_protected_restore,
    ) = _recovery_transfer_conditions(
        self,
        risk=risk,
        account=account,
        freeze_active=freeze_active,
        repair_observation=repair_observation,
        risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
        risk_neutral_recovery_expansion=risk_neutral_recovery_expansion,
    )
    synchronized_protected_restore = _repair_locked_cohort(
        self,
        leaders=leaders,
        account=account,
        weights_now=weights_now,
        synchronized_protected_restore=synchronized_protected_restore,
    )
    (
        bounded_caution_recovery_probe,
        confirmed_recovery_trail,
        live_anchor_count,
    ) = _caution_recovery_trail(
        self,
        risk=risk,
        account=account,
        freeze_active=freeze_active,
    )
    confirmed_hard_risk_trail, reason_clean_caution_anchor_cap = _hard_recovery_trail(
        risk=risk,
        leaders=leaders,
        account=account,
        freeze_active=freeze_active,
        live_anchor_count=live_anchor_count,
    )
    tactical_expiry_due = _tactical_expiry_due(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        account=account,
        prices=prices,
        freeze_active=freeze_active,
    )
    bounded_recovery_repair, strategic_live, bounded_strategic_restore = _bounded_recovery_conditions(
        self,
        risk=risk,
        account=account,
        freeze_active=freeze_active,
        repair_observation=repair_observation,
        level1_recovery_repair=level1_recovery_repair,
        protected_level1_restore=protected_level1_restore,
        synchronized_protected_restore=synchronized_protected_restore,
        bounded_caution_recovery_probe=bounded_caution_recovery_probe,
        risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
        confirmed_recovery_trail=confirmed_recovery_trail,
        confirmed_hard_risk_trail=confirmed_hard_risk_trail,
        tactical_expiry_due=tactical_expiry_due,
    )
    state = AllocationState(
        weights_now=weights_now,
        equity=equity,
        freeze_active=freeze_active,
        general_core_symbols=general_core_symbols,
        risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
        risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
        level1_recovery_repair=level1_recovery_repair,
        synchronized_protected_restore=synchronized_protected_restore,
        bounded_recovery_repair=bounded_recovery_repair,
        confirmed_recovery_trail=confirmed_recovery_trail,
        confirmed_hard_risk_trail=confirmed_hard_risk_trail,
        reason_clean_caution_anchor_cap=reason_clean_caution_anchor_cap,
    )
    return state, _strategic_allocation(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
        strategic_live=strategic_live,
        bounded_strategic_restore=bounded_strategic_restore,
    )
