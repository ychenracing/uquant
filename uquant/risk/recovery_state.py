"""Recovery-owner rearm and persistent crisis-cap ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..leader import credible_recovery_reserve
from ..risk_sector import SectorGuardTransition
from ..types import AccountState, LeaderScore, Risk, RiskAssessment


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Read-only outputs from the existing recovery and relapse slice."""

    credible_reserve: bool
    incomplete_universe_tail_break: bool
    reference_anchor_confirmed: bool
    capital_impaired_restoration_relapse: bool
    market_backed_restoration_relapse: bool
    terminal_market_backed_restoration_relapse: bool
    capital_drawdown_relapse: bool
    concentrated_confirmed: bool


def _reset_recovery_owner_rearm(account: AccountState) -> None:
    """Close the prior recovery-owner epoch when a new shock takes control."""

    for key in (
        "recovery_owner_handoff",
        "recovery_owner_rearm_submitted",
        "recovery_owner_rearm_complete",
        "post_shock_restore_submitted",
        "post_shock_restore_deferred_expansion",
    ):
        account.candidate_tenure[key] = 0


def _persistent_crisis_cap(
    severity: str,
    cfg: SystemConfig,
    *,
    reserve_backed: bool = False,
) -> float:
    """Keep severity—not a position label—as the persistent cap owner."""
    if severity == "INCOMPLETE_UNIVERSE":
        return cfg.incomplete_universe_crisis_gross
    if severity == "INCOMPLETE_UNIVERSE_UNBACKED":
        return 0.0
    if severity == "COHORT_BREAK":
        # An independently qualified reserve lets a mature recovery owner stay
        # inside the existing risk-off budget while it repairs or substitutes;
        # without that breadth, the same synchronized break remains a
        # concentrated crisis.  This distinction is evidence-based and never
        # depends on configured pool size.
        return cfg.risk_off_gross if reserve_backed else cfg.concentrated_crisis_gross
    if severity in {"SEVERE", "ANCHOR_BREAK"}:
        return cfg.severe_crisis_gross
    if severity == "CONCENTRATED":
        return cfg.concentrated_crisis_gross
    return cfg.market_crisis_gross


def _assess_recovery_state(
    *,
    date: pd.Timestamp,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    shock_rearmed: bool,
    strategic_active: bool,
    operating_dd: float,
    capital_dd: float,
    recovery_anchor_elapsed: int,
    emergency_tail_break: bool,
    concentrated_structure_break: bool,
    immediate_severe_break: bool,
    persistent_market_break: bool,
    reference_anchor_armed: bool,
    held_damage_ratio: float,
    votes: int,
    sector_stress: float,
    immediate_reference_break: bool,
    anchor_break_key: str,
    held_cohort_break_confirmed: bool,
    strategic_tail_break: bool,
) -> RecoveryAssessment:
    """Run recovery breadth, reserve, and restoration-relapse ownership in order."""

    live_recovery_members = {
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0 and position.lifecycle == "RECOVERY"
    }
    recovery_owner_observed = bool(
        account.anchor_weights
        or live_recovery_members
        or account.candidate_tenure.get("tactical_active", 0) == 1
    )
    recovery_book_complete = bool(
        not recovery_owner_observed
        or len(set(account.anchor_weights) | live_recovery_members) >= min(3, cfg.max_positions)
    )
    anchor_industries = {leaders[symbol].industry for symbol in account.anchor_weights if symbol in leaders}
    reserve_observed = bool(
        len(account.anchor_weights) >= 2
        and any(
            symbol not in account.anchor_weights
            and symbol in leaders
            and credible_recovery_reserve(
                score=leaders[symbol],
                frame=frame,
                date=date,
                occupied_industries=anchor_industries,
                cfg=cfg,
            )
            for symbol, frame in user_panel.items()
        )
    )
    if reserve_observed:
        account.candidate_tenure["recovery_reserve_qualified"] = 1
    credible_reserve = bool(
        account.candidate_tenure.get("recovery_reserve_qualified", 0) == 1
        or account.candidate_tenure.get("recovery_substitution_completed", 0) >= 1
    )
    incomplete_universe_tail_break = (
        shock_rearmed
        and not account.protected_weights
        and bool(account.positions)
        and not strategic_active
        and not recovery_book_complete
        and operating_dd
        >= (
            cfg.unbacked_universe_tail_dd
            if not credible_reserve
            and account.candidate_tenure.get("tactical_active", 0) == 0
            and (
                not account.anchor_weights
                or (
                    len(account.anchor_weights) >= 1
                    and recovery_anchor_elapsed >= cfg.unbacked_recovery_anchor_min_days
                )
            )
            else cfg.incomplete_universe_tail_dd
        )
    )
    account_break_confirmed = (
        shock_rearmed
        and not account.protected_weights
        and not account.anchor_weights
        and not strategic_active
        and (
            emergency_tail_break
            or (concentrated_structure_break and immediate_severe_break)
            or persistent_market_break
        )
    )
    reference_anchor_confirmed = (
        shock_rearmed
        and not account.protected_weights
        and reference_anchor_armed
        and held_damage_ratio >= cfg.concentrated_break_ratio
        and operating_dd >= cfg.incomplete_universe_tail_dd
        and votes >= 4
        and sector_stress >= 0.50
        and (immediate_reference_break or account.risk_streaks[anchor_break_key] >= 2)
    )
    incomplete_universe_tail_break = (
        incomplete_universe_tail_break and not account_break_confirmed and not reference_anchor_confirmed
    )
    # A restored cohort must not inherit immunity from the prior shock.  If
    # capital remains below its crisis line and the *new operating book* again
    # breaks structurally, cut it even when protected_weights from the previous
    # event have not yet normalized.  This closes the multi-year drawdown loop
    # without turning historical capital loss alone into a permanent cash lock.
    recovery_transition_dates = [
        pd.Timestamp(event["date"])
        for event in account.risk_events
        if event.get("from") == Risk.CRISIS.value
        and event.get("to") != Risk.CRISIS.value
        and event.get("date")
        and pd.Timestamp(event["date"]) <= date
    ]
    sessions_since_recovery = (
        len(tech.loc[max(recovery_transition_dates) : date]) - 1
        if recovery_transition_dates and user_panel
        else math.inf
    )
    last_shock_was_market_backed = bool(
        account.last_shock_date
        and any(
            event.get("date") == account.last_shock_date
            and event.get("to") == Risk.CRISIS.value
            and any(
                reason
                in {
                    "market-backed drawdown relapse in restored holdings",
                    "market-backed portfolio break in incomplete restoration",
                }
                for reason in event.get("reasons", ())
                if isinstance(reason, str)
            )
            for event in account.risk_events
        )
    )
    capital_impaired_restoration_relapse = (
        bool(account.positions)
        and bool(account.protected_weights)
        # This route is a fail-safe for an economically impaired account, not
        # a profit-giveback stop. A book still above contributed capital keeps
        # all ordinary market/cohort guards but cannot start the 60-session
        # failed-restoration cash lock solely from its high-water mark.
        and equity < account.initial_cash - 1e-12
        and capital_dd >= cfg.capital_dd_crisis
        and operating_dd >= cfg.capital_guard_relapse_dd
        and sessions_since_recovery >= cfg.capital_guard_min_recovery_days
        and (held_damage_ratio >= cfg.concentrated_break_ratio or (votes >= 2 and sector_stress >= 0.50))
    )
    market_backed_restoration_relapse = (
        bool(account.positions)
        and bool(account.protected_weights)
        # This route refines an already-cautious restoration. A normalized
        # book first passes through the generic confirmed state transition;
        # independent market evidence must not bypass that confirmation.
        and account.risk == Risk.CAUTION.value
        # Reuse the existing shock-epoch rearm before opening another ordinary
        # sell/restore loop. The only early exception is an independently
        # confirmed break of an incomplete restoration that has already
        # crossed the established portfolio-break line.
        and (
            shock_rearmed
            or (
                not last_shock_was_market_backed
                and account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
                and operating_dd >= cfg.portfolio_break_dd
            )
        )
        # Strategic cohorts retain their dedicated mature-tail guard; the
        # generic restoration guard must not turn ordinary strategic
        # high-water giveback into a failed-restoration cash lock.
        and not strategic_active
        # Anchored recovery cohorts likewise retain their dedicated mature
        # cohort guard instead of being short-circuited by this generic path.
        and not account.anchor_weights
        # A profitable restored account is not failed by high-water giveback
        # alone.  It is failed when the deployed book, the independent market
        # basket, and sector breadth all confirm the same post-recovery damage.
        and equity >= account.initial_cash - 1e-12
        and operating_dd >= cfg.capital_guard_relapse_dd
        and sessions_since_recovery >= cfg.capital_guard_min_recovery_days
        and held_damage_ratio >= cfg.concentrated_break_ratio
        and votes >= 3
        and sector_stress >= 0.50
    )
    terminal_market_backed_restoration_relapse = bool(
        market_backed_restoration_relapse
        # An incomplete restoration that has already crossed the existing
        # portfolio-break line is no longer an ordinary repair. Reuse the
        # established capital cooldown so the same damaged cohort cannot
        # churn through repeated sell/rebuy cycles.
        and account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
        and operating_dd >= cfg.portfolio_break_dd
    )
    capital_drawdown_relapse = bool(capital_impaired_restoration_relapse or market_backed_restoration_relapse)
    concentrated_confirmed = (
        account_break_confirmed
        or reference_anchor_confirmed
        or held_cohort_break_confirmed
        or incomplete_universe_tail_break
        or (shock_rearmed and strategic_tail_break and reference_anchor_confirmed)
        or capital_drawdown_relapse
    )
    return RecoveryAssessment(
        credible_reserve=credible_reserve,
        incomplete_universe_tail_break=incomplete_universe_tail_break,
        reference_anchor_confirmed=reference_anchor_confirmed,
        capital_impaired_restoration_relapse=capital_impaired_restoration_relapse,
        market_backed_restoration_relapse=market_backed_restoration_relapse,
        terminal_market_backed_restoration_relapse=terminal_market_backed_restoration_relapse,
        capital_drawdown_relapse=capital_drawdown_relapse,
        concentrated_confirmed=concentrated_confirmed,
    )


def _assess_protected_recovery(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    previous: Risk,
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
    tech_speed: float,
    broad_speed: float,
    operating_dd: float,
    capital_dd: float,
    credible_reserve: bool,
    freeze_new_risk: bool,
    overlay_cap: float,
    overlay_reduction_level: int,
    sector_guard: SectorGuardTransition,
    shock_rearmed: bool,
    strategic_active: bool,
) -> RiskAssessment | None:
    """Run the existing protected-book recovery and relapse slice in order."""

    protected_structure_ratio = 0.0
    if account.protected_weights:
        protected_structures: list[bool] = []
        for symbol in account.protected_weights:
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index:
                protected_structures.append(False)
                continue
            row = frame.loc[date]
            protected_structures.append(
                scalar(row, "close") > scalar(row, f"ma{cfg.trend_fast}")
                and scalar(row, f"ret{cfg.trend_fast}", 0.0) > 0
            )
        protected_structure_ratio = float(np.mean(protected_structures)) if protected_structures else 0.0
    normalize_key = "protected_structure_normalization"
    account.risk_streaks[normalize_key] = (
        account.risk_streaks.get(normalize_key, 0) + 1 if protected_structure_ratio >= 0.67 else 0
    )
    protected_targets = {
        symbol: min(cfg.max_symbol_weight, max(0.0, weight))
        for symbol, weight in account.protected_weights.items()
        if symbol in user_panel
    }
    protected_target_gross = sum(protected_targets.values())
    # ``recovery_target_gross`` bounds the first repaired step, not the final
    # NORMAL-state restoration.  Completion is measured against the original
    # per-symbol book, scaled only by the system's explicit max-gross limit.
    protected_full_cap = min(protected_target_gross, cfg.max_gross)
    protected_scale = (
        min(1.0, protected_full_cap / protected_target_gross) if protected_target_gross > 1e-12 else 0.0
    )
    protected_desired = {symbol: weight * protected_scale for symbol, weight in protected_targets.items()}
    protected_current = {
        symbol: position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
        for symbol, position in account.positions.items()
        if symbol in protected_desired and date in user_panel[symbol].index and position.shares > 0
    }
    pending_protected_buys = {
        order.symbol
        for order in account.pending_orders
        if order.side == "BUY" and order.symbol in protected_desired
    }
    protected_trade_threshold = {
        symbol: (
            cfg.protected_restore_min_trade_weight
            if desired >= cfg.core_admission_weight
            else cfg.restoration_min_trade_weight
        )
        for symbol, desired in protected_desired.items()
    }
    protected_completion_tolerance = cfg.min_trade_weight
    protected_restored = bool(
        account.candidate_tenure.get("post_shock_restore_complete", 0) == 1
        or protected_target_gross <= 1e-12
        or (
            not pending_protected_buys
            and all(
                desired - protected_current.get(symbol, 0.0) + 1e-12 < protected_trade_threshold[symbol]
                or (
                    protected_current.get(symbol, 0.0) >= 0.95 * desired
                    and desired - protected_current.get(symbol, 0.0) < protected_completion_tolerance
                )
                for symbol, desired in protected_desired.items()
                if desired > 1e-12
            )
        )
    )
    if (
        account.protected_weights
        and previous is not Risk.CRISIS
        and account.positions
        and account.capital_budget_level == 0
        and account.chronic_level == 0
        and overlay_cap >= protected_full_cap - 1e-12
        and protected_restored
        and (capital_dd <= 1e-12 or account.risk_streaks[normalize_key] >= cfg.recovery_risk_confirm_days)
    ):
        account.protected_weights.clear()
        account.candidate_tenure["post_shock_restore_complete"] = 0
        account.shock_start_date = ""
        account.shock_severity = "NORMAL"
        account.shock_state = "NONE"
    if previous is Risk.CRISIS and account.protected_weights:
        if account.shock_severity == "INCOMPLETE_UNIVERSE_UNBACKED" and not shock_rearmed:
            account.risk = Risk.CRISIS.value
            account.shock_state = "UNBACKED_COOLDOWN"
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
                    "held_repair_ratio": held_repair_ratio,
                    "tech_speed": tech_speed,
                    "broad_speed": broad_speed,
                    "operating_drawdown": operating_dd,
                    "capital_drawdown": capital_dd,
                },
                reasons=("unbacked universe remains in capital cooldown",),
                shock_state="UNBACKED_COOLDOWN",
                freeze_new_risk=True,
                reduction_level=3,
                severity=account.shock_severity,
            )
        repair_leaders = 0
        for symbol in user_panel:
            frame = user_panel[symbol]
            leader = leaders.get(symbol)
            if leader is None or not leader.mature or date not in frame.index:
                continue
            row = frame.loc[date]
            if (
                scalar(row, "close") > scalar(row, f"ma{cfg.trend_fast}")
                and scalar(row, f"ret{cfg.trend_fast}", 0.0) > 0
            ):
                repair_leaders += 1
        protected_fast_repairs: list[bool] = []
        protected_swing_repairs: list[bool] = []
        for symbol in account.protected_weights:
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index:
                protected_fast_repairs.append(False)
                protected_swing_repairs.append(False)
                continue
            row = frame.loc[date]
            returns1 = frame.loc[:date, "close"].pct_change(fill_method=None)
            protected_fast_repairs.append(
                bool(len(returns1))
                and math.isfinite(float(returns1.iloc[-1]))
                and float(returns1.iloc[-1]) > 0
            )
            protected_swing_repairs.append(
                scalar(row, "ret5", -1.0) > 0 and scalar(row, "close") > scalar(row, f"ma{cfg.trend_fast}")
            )
        protected_fast_ratio = float(np.mean(protected_fast_repairs)) if protected_fast_repairs else 0.0
        protected_swing_ratio = float(np.mean(protected_swing_repairs)) if protected_swing_repairs else 0.0
        shock_elapsed = 0
        if account.shock_start_date:
            shock_elapsed = len(tech.loc[pd.Timestamp(account.shock_start_date) : date]) - 1
        shock_wait_days = cfg.severe_shock_wait_days
        v_market_repair = (
            average_fast >= cfg.fast_v_recovery_return
            and declining <= cfg.fast_v_recovery_breadth
            and below <= cfg.fast_v_recovery_below_ma20
            and (
                scalar(tech.loc[date], "ret5", 0.0) >= cfg.fast_v_recovery_index_return
                or scalar(broad.loc[date], "ret5", 0.0) >= cfg.fast_v_recovery_index_return
            )
        )
        fast_v_repair = shock_elapsed >= shock_wait_days and v_market_repair and protected_fast_ratio >= 0.50
        persistent_v_repair = (
            shock_elapsed >= cfg.persistent_v_recovery_wait_days
            and len(account.protected_weights) == 1
            and v_market_repair
            and protected_swing_ratio >= 1.0
            and not sector_guard.active
        )
        structural_independent_repair = (
            not account.anchor_weights
            and (
                scalar(broad.loc[date], "close") > scalar(broad.loc[date], f"ma{cfg.trend_fast}")
                or scalar(tech.loc[date], "close") > scalar(tech.loc[date], f"ma{cfg.trend_fast}")
            )
            and declining <= 0.55
            and below <= 0.60
            and repair_leaders >= 2
        )
        independent_repair = not sector_guard.active and (structural_independent_repair or fast_v_repair)
        market_repair_key = "independent_market_repair"
        account.risk_streaks[market_repair_key] = (
            account.risk_streaks.get(market_repair_key, 0) + 1 if independent_repair else 0
        )
        repair_confirm_days = (
            cfg.fast_v_recovery_confirm_days if fast_v_repair else cfg.recovery_risk_confirm_days
        )
        standard_repair_ready = account.risk_streaks[market_repair_key] >= repair_confirm_days
        persistent_repair_key = "persistent_v_market_repair"
        account.risk_streaks[persistent_repair_key] = (
            account.risk_streaks.get(persistent_repair_key, 0) + 1 if persistent_v_repair else 0
        )
        persistent_repair_ready = (
            account.risk_streaks[persistent_repair_key] >= cfg.fast_v_recovery_confirm_days
            and not fast_v_repair
        )
        if standard_repair_ready or persistent_repair_ready:
            persistent_repair_confirmed = persistent_repair_ready and not standard_repair_ready
            expedited_repair = fast_v_repair or persistent_repair_confirmed
            account.risk = Risk.CAUTION.value
            # A repaired book starts a new operating-risk epoch.  Capital DD
            # remains anchored to the all-time peak, but a later relapse must
            # measure new damage after restoration rather than reuse the old
            # cohort's pre-crisis high-water mark.
            account.operating_peak = equity
            account.candidate_tenure["fast_v_recovery"] = int(expedited_repair)
            account.shock_state = "FAST_V_RECOVERY" if expedited_repair else "ROTATION_RECOVERY"
            repair_reason = (
                "confirmed persistent V-recovery after extended single-name protection"
                if persistent_repair_confirmed
                else "confirmed fast V-recovery breadth and index impulse"
                if fast_v_repair
                else "independent market and replacement-leader repair"
            )
            account.risk_events.append(
                {
                    "date": str(date.date()),
                    "from": previous.value,
                    "to": Risk.CAUTION.value,
                    "votes": votes,
                    "reasons": [repair_reason],
                }
            )
            return RiskAssessment(
                state=Risk.CAUTION,
                target_gross_cap=min(
                    cfg.max_gross,
                    cfg.fast_v_recovery_gross if expedited_repair else cfg.recovery_target_gross,
                    overlay_cap,
                ),
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
                    "protected_fast_repair_ratio": protected_fast_ratio,
                    "protected_swing_repair_ratio": protected_swing_ratio,
                    "replacement_leaders": repair_leaders,
                    "tech_speed": tech_speed,
                    "broad_speed": broad_speed,
                    "operating_drawdown": operating_dd,
                    "capital_drawdown": capital_dd,
                },
                reasons=(repair_reason,),
                shock_state=("FAST_V_RECOVERY" if expedited_repair else "ROTATION_RECOVERY"),
                freeze_new_risk=freeze_new_risk,
                reduction_level=max(1, overlay_reduction_level),
                severity=account.shock_severity,
            )
        protected_repairs: list[bool] = []
        for symbol in account.protected_weights:
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index:
                protected_repairs.append(False)
                continue
            ret1 = float(frame.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
            protected_repairs.append(math.isfinite(ret1) and ret1 > 0)
        repair_ratio = float(np.mean(protected_repairs)) if protected_repairs else 0.0
        severe_wait_complete = True
        if account.shock_severity in {"SEVERE", "CONCENTRATED"} and account.shock_start_date:
            severe_wait_complete = bool(
                len(tech.loc[pd.Timestamp(account.shock_start_date) : date]) - 1 >= shock_wait_days
            )
            severe_structures: list[bool] = []
            for symbol in account.protected_weights:
                frame = user_panel.get(symbol)
                if frame is None or date not in frame.index:
                    severe_structures.append(False)
                    continue
                row = frame.loc[date]
                close = scalar(row, "close")
                ma20 = scalar(row, f"ma{cfg.trend_fast}")
                ret5 = scalar(row, "ret5", -1.0)
                severe_structures.append(
                    math.isfinite(close) and math.isfinite(ma20) and close > ma20 and ret5 > 0
                )
            severe_wait_complete = (
                severe_wait_complete
                and bool(severe_structures)
                and (float(np.mean(severe_structures)) >= 0.67)
            )
        repair_key = "concentrated_repair"
        account.risk_streaks[repair_key] = (
            account.risk_streaks.get(repair_key, 0) + 1
            if repair_ratio >= 0.67 and severe_wait_complete and not sector_guard.active
            else 0
        )
        if account.risk_streaks[repair_key] >= cfg.concentrated_repair_days:
            state = Risk.CAUTION
            shock = "RECOVERY"
            account.operating_peak = equity
            recovery_gross = {
                "SEVERE": cfg.severe_recovery_gross,
                "CONCENTRATED": cfg.concentrated_recovery_gross,
            }.get(account.shock_severity, cfg.recovery_target_gross)
            cap = min(cfg.max_gross, recovery_gross)
            account.risk = state.value
            account.shock_state = shock
            account.risk_events.append(
                {
                    "date": str(date.date()),
                    "from": previous.value,
                    "to": state.value,
                    "votes": votes,
                    "reasons": ["two-day synchronized leader repair"],
                }
            )
            return RiskAssessment(
                state=state,
                target_gross_cap=min(cap, overlay_cap),
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
                    "held_repair_ratio": repair_ratio,
                    "tech_speed": tech_speed,
                    "broad_speed": broad_speed,
                    "operating_drawdown": operating_dd,
                    "capital_drawdown": capital_dd,
                },
                reasons=("two-day synchronized leader repair",),
                shock_state=shock,
                freeze_new_risk=freeze_new_risk,
                reduction_level=max(1, overlay_reduction_level),
                severity=account.shock_severity,
            )
        account.risk = Risk.CRISIS.value
        account.shock_state = "PERSISTENT_STRESS"
        return RiskAssessment(
            state=Risk.CRISIS,
            target_gross_cap=min(
                _persistent_crisis_cap(
                    account.shock_severity,
                    cfg,
                    reserve_backed=bool(credible_reserve and account.anchor_weights and not strategic_active),
                ),
                overlay_cap,
            ),
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
                "held_repair_ratio": repair_ratio,
                "tech_speed": tech_speed,
                "broad_speed": broad_speed,
                "operating_drawdown": operating_dd,
                "capital_drawdown": capital_dd,
            },
            reasons=("awaiting synchronized repair confirmation",),
            shock_state="PERSISTENT_STRESS",
            freeze_new_risk=True,
            reduction_level=3,
            severity=account.shock_severity,
        )

    return None


__all__ = (
    "RecoveryAssessment",
    "_assess_protected_recovery",
    "_assess_recovery_state",
    "_persistent_crisis_cap",
    "_reset_recovery_owner_rearm",
)
