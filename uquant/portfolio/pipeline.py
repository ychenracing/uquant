"""Mechanical Task 8 owner extracted from the immutable allocator."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ..features import scalar
from ..portfolio_core import (
    current_weights,
)
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
from .recovery.admission import _recovery_admission_targets

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _allocate_strategy(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
) -> tuple[Target, ...]:
    """Select one strategy route and return targets before final hard caps."""

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
    risk_neutral_recovery_handoff = bool(
        opportunity is Opportunity.RECOVERY
        and freeze_active
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.shock_state
        in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
        and risk.votes <= 1
        and float(risk.evidence.get("transition_damage", math.inf))
        <= self.cfg.transition_damage_repair
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
    risk_neutral_recovery_expansion = bool(
        opportunity is Opportunity.RECOVERY
        and freeze_active
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.shock_state
        in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
        and risk.votes <= 1
        and float(risk.evidence.get("transition_damage", math.inf))
        <= self.cfg.transition_damage_repair
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
    risk_neutral_recovery_transfer = bool(
        risk_neutral_recovery_handoff or risk_neutral_recovery_expansion
    )
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
    user_repair_industries = {
        score.industry
        for score in leaders.values()
        if score.industry != "unknown"
        and score.confidence >= self.cfg.leader_min_confidence
    }
    incumbent_repair_industries = {
        leaders[symbol].industry
        for symbol in account.anchor_weights
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    independent_user_repair_industries = (
        user_repair_industries - incumbent_repair_industries
    )
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
        and len(independent_user_repair_industries)
        < self.cfg.strategic_cohort_min_size
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
        if account.positions.get(symbol) is not None
        and account.positions[symbol].shares > 0
    )
    required_damage_ratio = (
        min(2, live_anchor_count) / live_anchor_count
        if live_anchor_count > 0
        else 1.0
    )
    unbroken_recovery_epoch = bool(
        not account.last_shock_date
        or (
            account.recovery_anchor_date
            and pd.Timestamp(account.recovery_anchor_date)
            > pd.Timestamp(account.last_shock_date)
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
        and float(risk.evidence.get("held_damage_ratio", 0.0))
        >= required_damage_ratio - 1e-12
        and float(risk.evidence.get("sector_stress_ratio", 0.0)) >= 0.50
    )
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
                or (
                    account.tactical_anchor_symbol
                    and position.symbol != account.tactical_anchor_symbol
                )
                or position.symbol not in user_panel
            ):
                continue
            pnl = prices.get(position.symbol, 0.0) / max(position.avg_cost, 1e-12) - 1.0
            held_sessions = len(
                user_panel[position.symbol].loc[
                    pd.Timestamp(position.entry_date) : date
                ]
            )
            promotable = bool(
                account.candidate_tenure.get("tactical_promotable", 0) == 1
                and account.tactical_anchor_symbol == position.symbol
            )
            # A caution freeze may not suppress an already-earned profit
            # exit.  A merely time-expired losing probe still waits for
            # the freeze to clear; otherwise the exception turns a risk
            # hold into a forced loss and can erase the recovery owner.
            tactical_expiry_due = bool(
                not promotable and pnl >= self.cfg.tactical_frozen_take_profit
            )
            break
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
    strategic_discovery_open = bool(
        opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        and risk.state is Risk.NORMAL
        and not freeze_active
        and not (
            account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
            and bool(account.anchor_weights)
        )
    )
    strategic_observation_open = bool(
        opportunity
        in {
            Opportunity.CHOPPY,
            Opportunity.WEAK,
            Opportunity.TREND,
            Opportunity.STRONG_TREND,
        }
        and risk.state is Risk.NORMAL
        and not freeze_active
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
        if strategic_live or strategic_observation_open
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
    strategic_handoff_pending = bool(
        account.strategic_epochs_completed > 0
        and account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
        and account.candidate_tenure.get("leader_cycle_handoff_epoch", 0)
        < account.strategic_epochs_completed
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
        strategic_handoff_blocked=(
            strategic_handoff_pending and not strategic_handoff_ready
        ),
        strategic_handoff_ready=strategic_handoff_ready,
    )
    cooldown = account.candidate_tenure.get("tactical_cooldown", 0)
    if cooldown > 0:
        remaining_cooldown = cooldown - 1
        if (
            account.candidate_tenure.get("tactical_overheat_cooldown", 0) == 1
            and not account.positions
            and any(
                date in frame.index
                and scalar(frame.loc[date], "ret5", -1.0)
                >= self.cfg.fast_v_recovery_return
                and scalar(frame.loc[date], "ret20", 0.0)
                <= self.cfg.tactical_rebound_breadth_max_ret20
                and scalar(frame.loc[date], "ret60", -1.0)
                >= self.cfg.tactical_rebound_min_ret60
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

    if (
        account.protected_weights
        and risk.state.value in {"NORMAL", "CAUTION"}
        and not risk_neutral_recovery_handoff
    ):
        # Restoration intent survives transient FAILED_REPAIR observations.
        # The CAUTION freeze above still blocks a buy while continuous
        # damage is active; once independent repair clears it, retaining
        # the protected target prevents a one-day state label from
        # permanently stranding the book at crisis gross.
        account.candidate_tenure["post_shock_recovery"] = int(account.shock_severity == "SEVERE")
        proposed = {
            symbol: min(self.cfg.max_symbol_weight, weight)
            for symbol, weight in account.protected_weights.items()
            if symbol in user_panel and symbol not in account.strategic_cohort_targets
        }
        pending_replacement_members: set[str] = set()
        pending_recovery_alternative = any(
            key.startswith("recovery_admission:") and tenure > 0
            for key, tenure in account.replacement_tenure.items()
        )
        if pending_recovery_alternative and proposed:
            # An independently observed alternative is already inside the
            # existing recovery-admission confirmation. Rebuying every
            # underweight secondary now, only to fund its imminent owner
            # handoff, is deterministic churn. Restore the conviction lead
            # while retaining secondary capital at its live weight until
            # the existing admission/substitution process resolves.
            protected_lead = max(
                proposed,
                key=lambda symbol: (proposed[symbol], symbol),
            )
            for symbol, desired in tuple(proposed.items()):
                current = weights_now.get(symbol, 0.0)
                if symbol != protected_lead and current < desired - 1e-12:
                    proposed[symbol] = current
                    pending_replacement_members.add(symbol)
        total = sum(proposed.values())
        explicit_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
        current_gross = sum(max(0.0, weight) for weight in weights_now.values())
        if total > explicit_cap and total > 0 and current_gross <= explicit_cap + 1e-12:
            proposed = {symbol: weight * explicit_cap / total for symbol, weight in proposed.items()}
        fully_repaired = bool(
            risk.state.value == "NORMAL"
            and risk.shock_state == "NONE"
            and not risk.freeze_new_risk
            and account.capital_budget_level == 0
            and account.chronic_level == 0
        )
        # RiskAssessment is the only owner of the day's gross cap.  A book
        # already below the allowance may BUY saved intent up to that cap.
        # An overweight book keeps full targets here so the single outer
        # reducer can apply global tranche priority to the required SELL.
        # ``protected_weights`` remains alive until the risk engine has
        # observed structural normalization, but it must not remain a
        # permanent rebalancing target.  Once the one economic restore is
        # filled (including every capacity-limited child fill), switch to
        # the same drift-tolerant hold semantics as a mature anchor.  A
        # later independent crisis resets this marker when it captures a
        # fresh protected book.
        restore_complete_key = "post_shock_restore_complete"
        restore_submitted_key = "post_shock_restore_submitted"
        restore_deferred_key = "post_shock_restore_deferred_expansion"
        restore_previously_submitted = (
            account.candidate_tenure.get(restore_submitted_key, 0) == 1
        )
        restore_expansion_deferred = (
            account.candidate_tenure.get(restore_deferred_key, 0) == 1
        )
        pending_restore_buys = {
            order.symbol
            for order in account.pending_orders
            if order.side == "BUY" and order.symbol in proposed
        }
        restore_confirmation_ready = bool(
            account.risk_streaks.get("protected_structure_normalization", 0)
            >= self.cfg.recovery_risk_confirm_days
        )
        if (
            restore_expansion_deferred
            and restore_confirmation_ready
            and not pending_restore_buys
        ):
            # The existing recovery confirmation has now caught up with
            # the first bounded step. Reopen one final submission against
            # the saved intent instead of treating that step as complete.
            account.candidate_tenure[restore_submitted_key] = 0
            account.candidate_tenure[restore_deferred_key] = 0
            restore_previously_submitted = False
            restore_expansion_deferred = False
        executable_buy_gap = {
            symbol: max(
                0.0,
                (desired - weights_now.get(symbol, 0.0)) * equity,
            )
            for symbol, desired in proposed.items()
        }
        restoration_trade_threshold = {
            symbol: (
                self.cfg.protected_restore_min_trade_weight
                if desired >= self.cfg.core_admission_weight
                else self.cfg.restoration_min_trade_weight
            )
            * equity
            for symbol, desired in proposed.items()
        }
        restoration_completion_threshold = self.cfg.min_trade_weight * equity
        economic_restore_complete = bool(
            proposed
            and not pending_restore_buys
            and (
                (
                    restore_previously_submitted
                    and not restore_expansion_deferred
                )
                or (
                    fully_repaired
                    and all(
                        gap + 1e-12 * equity
                        < restoration_trade_threshold[symbol]
                        or (
                            gap < restoration_completion_threshold
                            and weights_now.get(symbol, 0.0)
                            >= 0.95 * proposed[symbol]
                        )
                        for symbol, gap in executable_buy_gap.items()
                    )
                )
            )
        )
        restore_submission_has_buy = bool(
            pending_restore_buys
            or any(
                gap + 1e-12 * equity
                >= restoration_trade_threshold[symbol]
                for symbol, gap in executable_buy_gap.items()
            )
        )
        if (
            restore_expansion_deferred
            and not restore_confirmation_ready
            and not pending_restore_buys
            and not economic_restore_complete
        ):
            return self._targets(
                proposed={
                    symbol: weights_now.get(symbol, 0.0)
                    for symbol in proposed
                    if weights_now.get(symbol, 0.0) > 1e-12
                },
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="awaiting confirmed recovery before restore expansion",
                origin_subsystem=OriginSubsystem.RECOVERY,
                mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
            )
        if proposed and (
            synchronized_protected_restore or restore_submission_has_buy
        ):
            # Pending capacity-limited children may finish. A generic
            # bounded step waits for the existing recovery confirmation
            # before any cap expansion; a synchronized or already-
            # confirmed step then keeps the original one-shot semantics.
            account.candidate_tenure[restore_submitted_key] = 1
            account.candidate_tenure[restore_deferred_key] = int(
                not synchronized_protected_restore
                and not restore_confirmation_ready
            )
        if economic_restore_complete:
            account.candidate_tenure[restore_complete_key] = 1
            account.candidate_tenure[restore_submitted_key] = 0
            account.candidate_tenure[restore_deferred_key] = 0
        restoration_sell_mechanisms = {
            symbol: AttributionMechanism.RECOVERY_COHORT
            for symbol in account.positions
            if weights_now.get(symbol, 0.0) > proposed.get(symbol, 0.0) + 1e-12
        }
        if account.candidate_tenure.get(restore_complete_key, 0) == 1:
            return self._targets(
                proposed={
                    symbol: weights_now.get(symbol, 0.0)
                    for symbol in proposed
                    if weights_now.get(symbol, 0.0) > 1e-12
                },
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="completed post-shock restoration; retain price drift",
                origin_subsystem=OriginSubsystem.RECOVERY,
                mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
                mechanisms=restoration_sell_mechanisms,
            )
        # Once risk has fully reopened, restoration is buy-only.  A
        # winner that drifted above its saved target receives a sticky
        # hold reason while lagging members retain the one saved BUY
        # target. During an incomplete repair the severity cap remains a
        # genuine risk reduction and must not receive this exemption.
        restore_reasons: dict[str, str] | None = (
            {
                symbol: "post-shock restoration; retain winner drift"
                for symbol, desired in proposed.items()
                if weights_now.get(symbol, 0.0) >= desired - 1e-12
            }
            if fully_repaired
            else None
        )
        if pending_replacement_members:
            restore_reasons = dict(restore_reasons or {})
            restore_reasons.update(
                {
                    symbol: "post-shock restoration; retain pending replacement capital"
                    for symbol in pending_replacement_members
                }
            )
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="confirmed post-shock restoration",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
            reasons=restore_reasons,
            mechanisms=restoration_sell_mechanisms,
        )

    # A strategic anchor is deliberately sticky: price drift is allowed to
    # concentrate winners, while account risk remains the sole cut authority.
    anchored_held = {
        symbol: weights_now.get(symbol, 0.0)
        for symbol in account.anchor_weights
        if weights_now.get(symbol, 0.0) > 0
    }
    trailed_winners: list[str] = []
    trail_allowed = confirmed_recovery_trail or confirmed_hard_risk_trail
    hard_trail_prefix = "hard_risk_winner_trail:"
    live_gross = sum(max(0.0, weight) for weight in weights_now.values())
    hard_trail_cap_satisfied = bool(
        live_gross
        <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
    )
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
                mfe >= self.cfg.recovery_winner_mfe_arm
                and peak_giveback <= -self.cfg.recovery_winner_trail
            )
            hard_trail_key = f"{hard_trail_prefix}{symbol}"
            hard_trail_pending = False
            if confirmed_hard_risk_trail:
                prior_hard_trail = account.replacement_tenure.get(hard_trail_key, 0)
                hard_trail_pending = prior_hard_trail > 0
                account.replacement_tenure[hard_trail_key] = (
                    prior_hard_trail + 1
                    if trail_observed or hard_trail_pending
                    else 0
                )
            if not trail_observed and not hard_trail_pending:
                continue
            if (
                confirmed_hard_risk_trail
                and account.replacement_tenure[hard_trail_key]
                < self.cfg.concentrated_break_confirm_days
            ):
                # The outer sparse reducer still enforces the hard gross
                # cap immediately.  A permanent member exit additionally
                # waits for the next session to confirm that the same hard
                # portfolio risk persists; a prior cap trim is not that
                # second observation.
                continue
            trailed_winners.append(symbol)
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
    owner_rearm_open = bool(
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
    if owner_rearm_open:
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
                symbol: weight * explicit_cap / target_gross
                for symbol, weight in owner_targets.items()
            }
        pending_owner_buys = {
            order.symbol
            for order in account.pending_orders
            if order.side == "BUY" and order.symbol in owner_targets
        }
        rearm_submitted_key = "recovery_owner_rearm_submitted"
        previously_submitted = bool(
            account.candidate_tenure.get(rearm_submitted_key, 0) == 1
        )
        rearm_complete = bool(
            previously_submitted
            and not pending_owner_buys
            and all(
                desired - weights_now.get(symbol, 0.0) + 1e-12
                < self.cfg.restoration_min_trade_weight
                or (
                    desired - weights_now.get(symbol, 0.0)
                    < self.cfg.min_trade_weight
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
        current_owner = {
            symbol: max(0.0, weights_now.get(symbol, 0.0))
            for symbol in owner_targets
        }
        buy_gaps = {
            symbol: max(0.0, desired - current_owner[symbol])
            for symbol, desired in owner_targets.items()
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
    if graduation_ready:
        # Graduation changes lifecycle ownership; it is not an exit for a
        # different live Core position.  Preserve the whole broker book on
        # the hand-off day so omitted targets cannot manufacture a sale.
        promoted = {
            symbol: weight
            for symbol, weight in weights_now.items()
            if weight > 1e-12
        }
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
            order.side == "BUY" and order.symbol in account.anchor_weights
            for order in account.pending_orders
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

    # A recovery label must not evict a healthy trend core. During a
    # possible V-repair it freezes new risk and lets the existing lifecycle
    # continue; recovery-cohort construction is reserved for an empty book
    # or an already established strategic anchor.
    recovery_admission_targets = _recovery_admission_targets(
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
            mechanism=(
                AttributionMechanism.RECOVERY_CAP
                if capped
                else AttributionMechanism.RECOVERY_COHORT
            ),
        )

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

    if leader_cycle_armed:
        leader_targets = self._leader_targets(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            prices=prices,
        )
        if leader_targets is not None:
            return leader_targets

    live_symbols = {
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0
    }
    confirmed_live_core = {
        symbol
        for symbol in live_symbols
        if account.positions[symbol].lifecycle
        in {
            Lifecycle.CORE.value,
            Lifecycle.ADD1.value,
            Lifecycle.ADD2.value,
        }
        and symbol in leaders
        and leaders[symbol].mature
        and leaders[symbol].confidence >= self.cfg.leader_min_confidence
        and leaders[symbol].score >= self.cfg.leader_cycle_min_score
        and symbol in user_panel
        and date in user_panel[symbol].index
        and self._structure_ok(user_panel[symbol], date)
    }
    if (
        live_symbols
        and confirmed_live_core == live_symbols
        and risk.state is Risk.NORMAL
        and not freeze_active
    ):
        # Re-arming controls *new* generic leader risk.  A one-session
        # evidence gap in that owner must not turn two currently mature,
        # structurally intact Core holdings into an all-cash liquidation.
        # Hold only the marked broker book; the normal confirmation streak
        # still has to finish before any admission, add, or rotation.
        return self._targets(
            proposed={symbol: weights_now[symbol] for symbol in live_symbols},
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="confirmed live leader continuity while owner rearms",
            origin_subsystem=OriginSubsystem.LEADER,
            mechanism=AttributionMechanism.LEADER_SELECTION,
            lifecycles={
                symbol: Lifecycle(account.positions[symbol].lifecycle)
                for symbol in live_symbols
            },
        )

    live_generic_core = {
        symbol
        for symbol in live_symbols
        if account.positions[symbol].lifecycle
        in {
            Lifecycle.CORE.value,
            Lifecycle.ADD1.value,
            Lifecycle.ADD2.value,
        }
    }
    cohort_prefix = "slow_market_owner_cohort:"
    for key in tuple(account.replacement_tenure):
        if (
            key.startswith(cohort_prefix)
            and key[len(cohort_prefix) :] not in live_symbols
        ):
            account.replacement_tenure[key] = 0
    slow_market_owner_trigger = bool(
        live_symbols
        and live_generic_core == live_symbols
        and risk.state is Risk.NORMAL
        and not freeze_active
        and opportunity is Opportunity.STRONG_TREND
        and len(live_symbols) <= self.cfg.leader_cycle_min_mature
        and min(
            float(risk.evidence.get("broad_ret120", -math.inf)),
            float(risk.evidence.get("tech_ret120", -math.inf)),
        )
        < self.cfg.leader_cycle_min_market_ret120
    )
    if slow_market_owner_trigger:
        for symbol in live_symbols:
            account.replacement_tenure[f"{cohort_prefix}{symbol}"] = 1
    slow_market_owner_active = any(
        account.replacement_tenure.get(f"{cohort_prefix}{symbol}", 0) == 1
        for symbol in live_symbols
    )
    if (
        live_symbols
        and live_generic_core == live_symbols
        and slow_market_owner_active
        and risk.state is Risk.NORMAL
        and not freeze_active
    ):
        # A minimum viable leader cohort can face a strong short-term
        # impulse while one slow index leg remains just below the ordinary
        # owner threshold. Once that exact handoff occurs, keep its live
        # cohort under the existing per-symbol exit confirmation until
        # every marked member has left the broker book.
        confirmed_exits = {
            symbol
            for symbol in live_symbols
            if self._leader_lifecycle_exit_confirmed(
                symbol=symbol,
                date=date,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
            )
        }
        retained = live_symbols - confirmed_exits
        if retained:
            account.active_leaders = [
                symbol
                for symbol in account.active_leaders
                if symbol not in confirmed_exits
            ]
            return self._targets(
                proposed={symbol: weights_now[symbol] for symbol in retained},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason="live core retained through slow-market owner handoff",
                origin_subsystem=OriginSubsystem.LEADER,
                mechanism=AttributionMechanism.LEADER_SELECTION,
                lifecycles={
                    symbol: Lifecycle(account.positions[symbol].lifecycle)
                    for symbol in live_symbols
                },
                reasons={
                    symbol: "leader lifecycle exit: confirmed structural deterioration"
                    for symbol in confirmed_exits
                },
                mechanisms={
                    symbol: AttributionMechanism.LEADER_LIFECYCLE_EXIT
                    for symbol in confirmed_exits
                },
            )

    # With no independently confirmed recovery leader the robust action is
    # cash. This prevents a broad input pool from turning into a generic,
    # high-churn momentum strategy merely because more symbols were supplied.
    return self._targets(
        proposed={},
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="no independently confirmed leader",
        origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_LIFECYCLE_EXIT,
    )
