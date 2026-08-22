"""Sector-transition risk ownership."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..risk_sector import (
    SectorGuardTransition,
    SectorObservation,
    observe_deployed_sector,
)
from ..types import AccountState, LeaderScore, Risk, RiskAssessment
from .recovery_state import _persistent_crisis_cap, _reset_recovery_owner_rearm
from .strategic_guard import _strategic_crisis_severity


@dataclass(frozen=True, slots=True)
class RiskTransitionResolution:
    """Read-only outputs from the existing confirmed-transition slice."""

    state: Risk
    shock: str
    cap: float
    sector_guard_forced: bool
    observation: SectorObservation | None


@dataclass(frozen=True, slots=True)
class BreakConditions:
    """Read-only outputs from the existing break-confirmation input slice."""

    shock_rearmed: bool
    concentrated_structure_break: bool
    emergency_tail_break: bool
    narrow_anchor_guard: bool
    immediate_severe_break: bool
    persistent_market_break: bool
    strategic_active: bool
    recovery_anchor_elapsed: int
    held_cohort_break_confirmed: bool
    strategic_current_gross: float
    strategic_tail_break: bool


def _acute_sector_evacuation_required(
    transition: SectorGuardTransition,
    cfg: SystemConfig,
    *,
    leadership_divergence: float,
    single_holding_observation: SectorObservation | None = None,
    single_holding_is_leader: bool = False,
) -> bool:
    """Identify a newly confirmed, full-book fast collapse.

    An ordinary synchronized sector break keeps the reviewed 40% gross cap.
    Evacuation is reserved for the first observed session where both
    equal-weight and economic-weight losses cross the existing fast-risk line
    and almost all deployed capital is losing while the technology leadership
    premium independently exceeds the existing sector-guard boundary.  Waiting for the ordinary
    two-shock sector confirmation repeats the same evidence and exposes the
    entire book to a second gap before the next-open order can execute.
    No later outcome or universe identity enters.
    """
    observation = transition.observation or single_holding_observation
    single_holding_systemic_shock = bool(
        observation is not None
        and observation.symbol_count == 1
        # A single name cannot establish breadth. It may use the existing
        # first-shock owner only while it remains the structural leader of the
        # already-confirmed technology premium; this rejects an idiosyncratic
        # laggard gap while protecting a concentrated winning book.
        and single_holding_is_leader
        and observation.equal_return <= cfg.risk_fast_return
        and observation.positive_breadth == 0.0
    )
    return bool(
        observation is not None
        and (transition.shock or single_holding_systemic_shock)
        and leadership_divergence >= cfg.sector_guard_divergence
        and observation.equal_return <= cfg.risk_fast_return
        and observation.weighted_return <= cfg.risk_fast_return
        and observation.negative_exposure >= cfg.sector_weighted_negative_exposure
    )


def _assess_break_conditions(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    held_damage: list[bool],
    held_damage_ratio: float,
    held_ret5: list[float],
    operating_dd: float,
    votes: int,
    sector_stress: float,
    transition_damage: float,
    market_context: dict[str, float],
) -> BreakConditions:
    """Run the existing shock-rearm and cohort-break derivation in order."""

    shock_rearmed = True
    if account.last_shock_date and user_panel:
        rearm_days = (
            cfg.incomplete_universe_rearm_days
            if account.candidate_tenure.get("last_shock_incomplete_universe", 0) == 1
            else cfg.shock_rearm_days
        )
        shock_rearmed = len(tech.loc[pd.Timestamp(account.last_shock_date) : date]) - 1 >= rearm_days
        # A fully new book is a new risk cohort.  It must not inherit the
        # previous cohort's long rearm lock after the old positions were sold.
        if account.positions and all(
            position.entry_date and pd.Timestamp(position.entry_date) > pd.Timestamp(account.last_shock_date)
            for position in account.positions.values()
            if position.shares > 0
        ):
            shock_rearmed = True
    concentrated_structure_break = (
        len(held_damage) >= 1
        and operating_dd >= cfg.concentrated_break_dd
        and held_damage_ratio >= cfg.concentrated_break_ratio
    )
    emergency_tail_break = (
        any(held_damage) and operating_dd >= cfg.portfolio_break_dd and votes >= cfg.portfolio_break_votes
    )
    concentrated_break = shock_rearmed and not account.protected_weights and concentrated_structure_break
    break_key = "concentrated_break"
    account.risk_streaks[break_key] = account.risk_streaks.get(break_key, 0) + 1 if concentrated_break else 0
    narrow_anchor_structure_break = (
        shock_rearmed
        and not account.protected_weights
        and bool(account.anchor_weights)
        and len(held_damage) >= 2
        and sum(held_damage) >= 2
        and operating_dd >= cfg.concentrated_break_dd
    )
    narrow_anchor_guard = (
        narrow_anchor_structure_break
        and market_context["tech_ret120"] - market_context["broad_ret120"] >= cfg.narrow_anchor_divergence
    )
    immediate_severe_break = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
    persistent_market_break = (
        concentrated_structure_break
        and account.risk_streaks[break_key] >= cfg.concentrated_break_confirm_days
        and (votes >= 3 or (bool(held_ret5) and float(np.mean(held_ret5)) <= -0.08))
    )
    strategic_active = account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    recovery_anchor_elapsed = 0
    if account.recovery_anchor_date:
        recovery_anchor_elapsed = len(tech.loc[pd.Timestamp(account.recovery_anchor_date) : date]) - 1
    mature_live_cohort = bool(
        (
            strategic_active
            and account.candidate_tenure.get("strategic_cohort_days", 0) >= cfg.strategic_cohort_guard_days
        )
        or (account.anchor_weights and recovery_anchor_elapsed >= cfg.recovery_cohort_tail_guard_days)
    )
    # A strategic or recovery label is not immunity.  Confirm an all-holdings
    # structural break only after the live cohort has matured and crossed its
    # explicit tail line.  This preserves ordinary early recovery volatility
    # while protecting a seasoned book from a true synchronized failure.
    synchronized_held_cohort_break = bool(
        shock_rearmed
        and not account.protected_weights
        and mature_live_cohort
        and len(held_damage) >= 2
        and held_damage_ratio >= 1.0 - 1e-12
        and operating_dd
        >= (cfg.strategic_cohort_tail_line if strategic_active else cfg.recovery_cohort_tail_line)
        and account.risk_streaks[break_key] >= cfg.concentrated_break_confirm_days
    )
    market_backed_break_key = "market_backed_recovery_break"
    market_backed_partial_cohort_damage = bool(
        shock_rearmed
        and not account.protected_weights
        and not strategic_active
        and bool(account.anchor_weights)
        and mature_live_cohort
        # Two independently damaged holdings establish portfolio damage; the
        # broad reference basket must separately confirm that it is systemic.
        # This prevents a recovery label from waiting for the final surviving
        # member to fail after the ordinary concentrated-break confirmation
        # window has already completed.
        and len(held_damage) >= 2
        and sum(held_damage) >= 2
        and operating_dd >= cfg.concentrated_break_dd
        and votes >= 3
        and sector_stress >= 0.50
    )
    account.risk_streaks[market_backed_break_key] = (
        account.risk_streaks.get(market_backed_break_key, 0) + 1 if market_backed_partial_cohort_damage else 0
    )
    market_backed_partial_cohort_break = bool(
        account.risk_streaks[market_backed_break_key] >= cfg.concentrated_break_confirm_days
    )
    held_cohort_break_confirmed = bool(synchronized_held_cohort_break or market_backed_partial_cohort_break)
    strategic_current_gross = sum(
        position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
        for symbol, position in account.positions.items()
        if symbol in account.strategic_cohort_symbols
        and symbol in user_panel
        and date in user_panel[symbol].index
        and position.shares > 0
    )
    strategic_tail_key = "strategic_tail_break"
    strategic_tail_observed = bool(
        strategic_active
        and account.candidate_tenure.get("strategic_cohort_days", 0) >= cfg.strategic_cohort_guard_days
        and operating_dd >= cfg.strategic_cohort_tail_line
    )
    account.risk_streaks[strategic_tail_key] = (
        account.risk_streaks.get(strategic_tail_key, 0) + 1 if strategic_tail_observed else 0
    )
    strategic_tail_break = (
        strategic_tail_observed
        and account.risk_streaks[strategic_tail_key] >= cfg.strategic_cohort_tail_confirm_days
        and votes >= 4
        and sector_stress >= 0.50
        and transition_damage >= cfg.transition_damage_freeze
    )
    return BreakConditions(
        shock_rearmed=shock_rearmed,
        concentrated_structure_break=concentrated_structure_break,
        emergency_tail_break=emergency_tail_break,
        narrow_anchor_guard=narrow_anchor_guard,
        immediate_severe_break=immediate_severe_break,
        persistent_market_break=persistent_market_break,
        strategic_active=strategic_active,
        recovery_anchor_elapsed=recovery_anchor_elapsed,
        held_cohort_break_confirmed=held_cohort_break_confirmed,
        strategic_current_gross=strategic_current_gross,
        strategic_tail_break=strategic_tail_break,
    )


def _assess_acute_and_cooldown(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    market_context: dict[str, float],
    sector_guard: SectorGuardTransition,
    concentrated_confirmed: bool,
    held_ret5: list[float],
    votes: int,
    continuous_evidence: dict[str, object],
    average_fast: float,
    declining: float,
    below: float,
    sector_stress: float,
    correlation: float,
    vol_ratio: float,
    leader_failure: float,
    held_damage_ratio: float,
    held_loss_ratio: float,
    held_repair_ratio: float,
    tech_speed: float,
    broad_speed: float,
    operating_dd: float,
    capital_dd: float,
    strategic_active: bool,
    strategic_current_gross: float,
) -> RiskAssessment | tuple[Risk, bool]:
    """Run acute evacuation and capital-cooldown short circuits in order."""

    previous = Risk(account.risk)
    live_symbols = {symbol for symbol, position in account.positions.items() if position.shares > 0}
    single_holding_observation = (
        observe_deployed_sector(
            date=date,
            panel=user_panel,
            symbols=live_symbols,
            cfg=cfg,
            minimum_symbols=1,
        )
        if len(live_symbols) == 1
        else None
    )
    single_holding_is_leader = bool(
        len(live_symbols) == 1
        and all(
            symbol in user_panel
            and date in user_panel[symbol].index
            and scalar(user_panel[symbol].loc[date], "ret120", -1.0) >= market_context["tech_ret120"]
            for symbol in live_symbols
        )
    )
    acute_sector_evacuation = bool(
        _acute_sector_evacuation_required(
            sector_guard,
            cfg,
            leadership_divergence=(market_context["tech_ret120"] - market_context["broad_ret120"]),
            single_holding_observation=single_holding_observation,
            single_holding_is_leader=single_holding_is_leader,
        )
        and (sector_guard.triggered or not concentrated_confirmed)
    )
    if acute_sector_evacuation:
        # This hard execution boundary precedes every recovery/concentrated
        # early return.  A full-book fast collapse must therefore evacuate
        # even when another risk route is simultaneously true.  A first-shock
        # evacuation also advances the existing sector-guard owner so a
        # one-session zero target cannot immediately reopen the same cohort.
        if not account.sector_guard_active:
            account.sector_guard_active = True
            account.sector_guard_started = str(date.date())
            account.sector_guard_symbols = sorted(
                symbol for symbol, position in account.positions.items() if position.shares > 0
            )
            account.sector_recovery_streak = 0
        _reset_recovery_owner_rearm(account)
        if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1:
            account.protected_weights.clear()
        account.candidate_tenure["post_shock_restore_complete"] = 0
        if not account.protected_weights:
            account.protected_weights = dict(account.anchor_weights)
        if not account.protected_weights:
            account.protected_weights = {
                symbol: position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
                for symbol, position in account.positions.items()
                if symbol in user_panel and date in user_panel[symbol].index and position.shares > 0
            }
        account.shock_start_date = str(date.date())
        account.last_shock_date = str(date.date())
        account.candidate_tenure["acute_sector_evacuation"] = 1
        evacuation_state = Risk.CRISIS if previous is Risk.CRISIS or concentrated_confirmed else Risk.RISK_OFF
        if evacuation_state is Risk.CRISIS and previous is not Risk.CRISIS:
            # Acute evacuation is a hard cap overlay, not a new owner of an
            # already-established crisis route. Preserve calibrated states
            # such as unbacked incomplete-universe and cohort-break cooldowns.
            severe_held_move = bool(held_ret5) and (float(np.mean(held_ret5)) <= cfg.severe_shock_ret5)
            account.shock_severity = "SEVERE" if severe_held_move and votes >= 4 else "CONCENTRATED"
        account.risk = evacuation_state.value
        evacuation_shock = (
            account.shock_state
            if previous is Risk.CRISIS
            else "SHOCK"
            if evacuation_state is Risk.CRISIS
            else "SECTOR_GUARD"
        )
        account.shock_state = evacuation_shock
        account.risk_streaks["concentrated_repair"] = 0
        account.risk_events.append(
            {
                "date": str(date.date()),
                "from": previous.value,
                "to": evacuation_state.value,
                "votes": votes,
                "reasons": ["confirmed acute holdings collapse"],
                "severity": account.shock_severity,
                "route": "sector_guard_acute",
                "target_gross_cap": 0.0,
            }
        )
        observation = sector_guard.observation or single_holding_observation
        return RiskAssessment(
            state=evacuation_state,
            target_gross_cap=0.0,
            votes=votes,
            evidence={
                **continuous_evidence,
                **market_context,
                "ai_fast_return": average_fast,
                "declining_ratio": declining,
                "below_ma20_ratio": below,
                "sector_stress_ratio": sector_stress,
                "median_correlation": correlation,
                "volatility_ratio": vol_ratio,
                "leader_failure_ratio": leader_failure,
                "held_damage_ratio": held_damage_ratio,
                "held_loss_ratio": held_loss_ratio,
                "held_repair_ratio": held_repair_ratio,
                "tech_speed": tech_speed,
                "broad_speed": broad_speed,
                "operating_drawdown": operating_dd,
                "capital_drawdown": capital_dd,
                "strategic_cohort_active": strategic_active,
                "strategic_current_gross": strategic_current_gross,
                "sector_guard_active": account.sector_guard_active,
                "acute_sector_evacuation": True,
                "sector_guard_shock_count": sector_guard.shock_count,
                "sector_guard_active_sessions": sector_guard.active_sessions,
                "sector_guard_equal_return": (observation.equal_return if observation is not None else None),
                "sector_guard_weighted_return": (
                    observation.weighted_return if observation is not None else None
                ),
                "sector_guard_negative_exposure": (
                    observation.negative_exposure if observation is not None else None
                ),
            },
            reasons=("confirmed acute holdings collapse",),
            shock_state=account.shock_state,
            freeze_new_risk=True,
            reduction_level=3,
            severity=account.shock_severity,
        )
    capital_cooldown = account.candidate_tenure.get("capital_guard_cooldown", 0)
    if capital_cooldown > 0:
        account.candidate_tenure["capital_guard_cooldown"] = capital_cooldown - 1
        account.risk = Risk.CRISIS.value
        account.shock_state = "CAPITAL_GUARD_COOLDOWN"
        return RiskAssessment(
            state=Risk.CRISIS,
            target_gross_cap=0.0,
            votes=votes,
            evidence={
                **continuous_evidence,
                **market_context,
                "ai_fast_return": average_fast,
                "declining_ratio": declining,
                "below_ma20_ratio": below,
                "sector_stress_ratio": sector_stress,
                "median_correlation": correlation,
                "volatility_ratio": vol_ratio,
                "leader_failure_ratio": leader_failure,
                "held_damage_ratio": held_damage_ratio,
                "held_loss_ratio": held_loss_ratio,
                "held_repair_ratio": held_repair_ratio,
                "tech_speed": tech_speed,
                "broad_speed": broad_speed,
                "operating_drawdown": operating_dd,
                "capital_drawdown": capital_dd,
            },
            reasons=("capital guard cooldown after failed restoration",),
            shock_state="CAPITAL_GUARD_COOLDOWN",
            freeze_new_risk=True,
            reduction_level=3,
            severity="SEVERE",
        )
    return previous, acute_sector_evacuation


def _assess_confirmed_concentrated_break(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    previous: Risk,
    concentrated_confirmed: bool,
    votes: int,
    continuous_evidence: dict[str, object],
    market_context: dict[str, float],
    average_fast: float,
    declining: float,
    below: float,
    sector_stress: float,
    correlation: float,
    vol_ratio: float,
    leader_failure: float,
    held_damage_ratio: float,
    held_repair_ratio: float,
    held_ret5: list[float],
    tech_speed: float,
    broad_speed: float,
    operating_dd: float,
    capital_dd: float,
    strategic_active: bool,
    strategic_current_gross: float,
    overlay_cap: float,
    credible_reserve: bool,
    capital_impaired_restoration_relapse: bool,
    market_backed_restoration_relapse: bool,
    terminal_market_backed_restoration_relapse: bool,
    incomplete_universe_tail_break: bool,
    reference_anchor_confirmed: bool,
    held_cohort_break_confirmed: bool,
    capital_drawdown_relapse: bool,
    immediate_reference_break: bool,
) -> RiskAssessment | None:
    """Run the existing confirmed concentrated-break short circuit in order."""

    if concentrated_confirmed:
        _reset_recovery_owner_rearm(account)
        if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1:
            # A new independent event owns a new pre-cut economic snapshot;
            # never resurrect targets from an already completed repair.
            account.protected_weights.clear()
        account.candidate_tenure["post_shock_restore_complete"] = 0
        if not account.protected_weights:
            account.protected_weights = dict(account.anchor_weights)
        if not account.protected_weights:
            account.protected_weights = {
                symbol: position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
                for symbol, position in account.positions.items()
                if symbol in user_panel and date in user_panel[symbol].index and position.shares > 0
            }
        account.shock_start_date = str(date.date())
        account.last_shock_date = str(date.date())
        if capital_impaired_restoration_relapse or terminal_market_backed_restoration_relapse:
            account.candidate_tenure["capital_guard_cooldown"] = cfg.capital_guard_cooldown_days
        account.candidate_tenure["last_shock_incomplete_universe"] = int(
            incomplete_universe_tail_break and credible_reserve
        )
        severe_held_move = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
        if incomplete_universe_tail_break:
            account.shock_severity = (
                "INCOMPLETE_UNIVERSE" if credible_reserve else "INCOMPLETE_UNIVERSE_UNBACKED"
            )
        elif held_cohort_break_confirmed:
            account.shock_severity = "COHORT_BREAK"
        elif reference_anchor_confirmed and strategic_active:
            account.shock_severity = _strategic_crisis_severity(
                strategic_active=True,
                reference_anchor_confirmed=True,
                live_core_positions=sum(
                    position.shares > 0
                    for position in account.positions.values()
                    if position.lifecycle == "CORE"
                ),
            )
        elif reference_anchor_confirmed:
            held_industries = {
                leaders[symbol].industry
                for symbol, position in account.positions.items()
                if position.shares > 0 and symbol in leaders
            }
            account.shock_severity = (
                "SEVERE" if immediate_reference_break and len(held_industries) >= 2 else "CONCENTRATED"
            )
        elif strategic_active:
            account.shock_severity = _strategic_crisis_severity(
                strategic_active=True,
                reference_anchor_confirmed=False,
                live_core_positions=sum(
                    position.shares > 0
                    for position in account.positions.values()
                    if position.lifecycle == "CORE"
                ),
            )
        else:
            account.shock_severity = (
                "SEVERE"
                if severe_held_move and votes >= 4
                else "CONCENTRATED"
                if severe_held_move
                else "NORMAL"
            )
        state = Risk.CRISIS
        shock = "SHOCK"
        crisis_gross = min(
            _persistent_crisis_cap(
                account.shock_severity,
                cfg,
                reserve_backed=bool(credible_reserve and account.anchor_weights and not strategic_active),
            ),
            overlay_cap,
        )
        concentrated_reason = (
            "confirmed dynamic cohort structural break"
            if held_cohort_break_confirmed
            else "market-backed portfolio break in incomplete restoration"
            if terminal_market_backed_restoration_relapse
            else "market-backed drawdown relapse in restored holdings"
            if market_backed_restoration_relapse
            else "capital drawdown relapse in restored holdings"
            if capital_drawdown_relapse
            else "reserve-backed incomplete-universe tail guard"
            if incomplete_universe_tail_break and credible_reserve
            else "unbacked incomplete-universe capital exit"
            if incomplete_universe_tail_break
            else "confirmed strategic cohort capital guard"
            if strategic_active
            else "confirmed concentrated leader break"
        )
        account.risk = state.value
        account.shock_state = shock
        account.risk_streaks["concentrated_repair"] = 0
        account.risk_events.append(
            {
                "date": str(date.date()),
                "from": previous.value,
                "to": state.value,
                "votes": votes,
                "reasons": [concentrated_reason],
                "severity": account.shock_severity,
                "route": (
                    "strategic_cohort"
                    if strategic_active
                    else "incomplete_universe_reserve"
                    if incomplete_universe_tail_break and credible_reserve
                    else "incomplete_universe_unbacked"
                    if incomplete_universe_tail_break
                    else "dynamic_cohort"
                    if held_cohort_break_confirmed
                    else "reference_anchor"
                    if reference_anchor_confirmed
                    else "account_holdings"
                ),
                "target_gross_cap": crisis_gross,
            }
        )
        return RiskAssessment(
            state=state,
            target_gross_cap=crisis_gross,
            votes=votes,
            evidence={
                **continuous_evidence,
                **market_context,
                "ai_fast_return": average_fast,
                "declining_ratio": declining,
                "below_ma20_ratio": below,
                "sector_stress_ratio": sector_stress,
                "median_correlation": correlation,
                "volatility_ratio": vol_ratio,
                "leader_failure_ratio": leader_failure,
                "held_damage_ratio": held_damage_ratio,
                "held_repair_ratio": held_repair_ratio,
                "tech_speed": tech_speed,
                "broad_speed": broad_speed,
                "operating_drawdown": operating_dd,
                "capital_drawdown": capital_dd,
                "strategic_cohort_active": strategic_active,
                "strategic_current_gross": strategic_current_gross,
            },
            reasons=(concentrated_reason,),
            shock_state=shock,
            freeze_new_risk=True,
            reduction_level=3,
            severity=account.shock_severity,
        )

    return None


def _resolve_risk_transition(
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    previous: Risk,
    shock_rearmed: bool,
    capital_dd: float,
    votes: int,
    sector_stress: float,
    narrow_anchor_guard: bool,
    operating_dd: float,
    independent_damage: bool,
    reasons: list[str],
    sector_guard: SectorGuardTransition,
    held_ret5: list[float],
    credible_reserve: bool,
    strategic_active: bool,
    overlay_cap: float,
) -> RiskTransitionResolution:
    """Run the existing confirmed risk-state transition and cap slice in order."""

    observed = Risk.NORMAL
    if shock_rearmed and not account.protected_weights and capital_dd >= cfg.capital_dd_crisis and votes >= 4:
        observed = Risk.CRISIS
    elif narrow_anchor_guard:
        observed = Risk.RISK_OFF
        reasons.append("narrow-market concentrated anchor damage")
    elif (
        (capital_dd >= cfg.capital_dd_risk_off or operating_dd >= 0.10)
        and votes >= 3
        and sector_stress >= 0.50
        # Broad/index warnings without damage in the owned book are a level-1
        # freeze, not permission to manufacture a sale.  A level-2 RISK_OFF
        # reduction needs independently confirmed structural damage or an
        # already-active capital-budget reduction rung.
        and (independent_damage or account.capital_budget_level >= 2)
    ):
        observed = Risk.RISK_OFF
    elif operating_dd >= cfg.operating_dd_caution or votes >= 2:
        observed = Risk.CAUTION
    key = f"risk_{observed.value.lower()}"
    account.risk_streaks[key] = account.risk_streaks.get(key, 0) + 1
    for other in Risk:
        other_key = f"risk_{other.value.lower()}"
        if other_key != key:
            account.risk_streaks[other_key] = 0
    required = {
        Risk.NORMAL: cfg.recovery_risk_confirm_days if previous is not Risk.NORMAL else 1,
        Risk.CAUTION: cfg.caution_confirm_days,
        Risk.RISK_OFF: cfg.risk_off_confirm_days,
        Risk.CRISIS: cfg.crisis_confirm_days,
    }[observed]
    if narrow_anchor_guard and observed is Risk.RISK_OFF:
        required = 1
    state = observed if account.risk_streaks[key] >= required else previous
    if state is Risk.CRISIS:
        shock = "SHOCK" if previous is not Risk.CRISIS else "PERSISTENT_STRESS"
    elif previous is Risk.CRISIS and state in {Risk.RISK_OFF, Risk.CAUTION}:
        shock = "RECOVERY"
    elif account.shock_state == "RECOVERY" and observed in {Risk.RISK_OFF, Risk.CRISIS}:
        shock = "FAILED_REPAIR"
    else:
        shock = "NONE" if state is Risk.NORMAL else account.shock_state
    guard_reason = "confirmed synchronized holdings shock"
    sector_guard_forced = bool(sector_guard.active and state is not Risk.CRISIS)
    if sector_guard_forced:
        state = Risk.RISK_OFF
        shock = "SECTOR_GUARD"
        if guard_reason not in reasons:
            reasons.append(guard_reason)
    if previous is Risk.CRISIS and state is not Risk.CRISIS:
        # This general transition covers crisis repairs without a protected
        # snapshot.  The dedicated protected-repair returns above perform the
        # same reset before returning.
        account.operating_peak = equity
    if state is Risk.CRISIS and previous is not Risk.CRISIS:
        _reset_recovery_owner_rearm(account)
        if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1:
            account.protected_weights.clear()
        account.candidate_tenure["post_shock_restore_complete"] = 0
        if not account.protected_weights:
            account.protected_weights = {
                symbol: position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
                for symbol, position in account.positions.items()
                if symbol in user_panel and date in user_panel[symbol].index and position.shares > 0
            }
        account.shock_start_date = str(date.date())
        account.last_shock_date = str(date.date())
        account.candidate_tenure["last_shock_incomplete_universe"] = 0
        severe_held_move = bool(held_ret5) and (float(np.mean(held_ret5)) <= cfg.severe_shock_ret5)
        account.shock_severity = (
            "SEVERE" if severe_held_move and votes >= 4 else "CONCENTRATED" if severe_held_move else "MARKET"
        )
    if state != previous:
        account.risk_events.append(
            {
                "date": str(date.date()),
                "from": previous.value,
                "to": state.value,
                "votes": votes,
                "reasons": reasons,
                "severity": account.shock_severity,
                "route": "sector_guard" if sector_guard_forced else "risk_state",
            }
        )
    account.risk = state.value
    account.shock_state = shock
    crisis_cap = _persistent_crisis_cap(
        account.shock_severity,
        cfg,
        reserve_backed=bool(credible_reserve and account.anchor_weights and not strategic_active),
    )
    cap = {
        Risk.NORMAL: cfg.max_gross,
        # CAUTION is the level-1 early warning: freeze additions, scouts, and
        # rotation without manufacturing a sale.  Structural damage is
        # reduced by the capital/sector overlays above.
        Risk.CAUTION: cfg.max_gross,
        Risk.RISK_OFF: cfg.risk_off_gross,
        Risk.CRISIS: crisis_cap,
    }[state]
    if narrow_anchor_guard and state is Risk.RISK_OFF:
        cap = cfg.narrow_anchor_guard_gross
    cap = min(cap, overlay_cap)
    if sector_guard_forced:
        cap = min(cap, cfg.sector_guard_gross)
    observation = sector_guard.observation
    return RiskTransitionResolution(
        state=state,
        shock=shock,
        cap=cap,
        sector_guard_forced=sector_guard_forced,
        observation=observation,
    )


__all__ = (
    "BreakConditions",
    "RiskTransitionResolution",
    "_acute_sector_evacuation_required",
    "_assess_acute_and_cooldown",
    "_assess_break_conditions",
    "_assess_confirmed_concentrated_break",
    "_resolve_risk_transition",
)
