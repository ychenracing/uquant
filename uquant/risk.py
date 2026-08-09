"""Independent sector risk radar and the only owner of portfolio risk caps."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import cross_section_returns, scalar
from .leader import INDUSTRY, REFERENCE_UNIVERSE, credible_recovery_reserve
from .types import AccountState, LeaderScore, Risk, RiskAssessment

REFERENCE_ANCHORS = ("sz300308", "sz300394", "sz300502")


def _persistent_crisis_cap(
    severity: str,
    cfg: SystemConfig,
    *,
    strategic_active: bool,
) -> float:
    """Keep a crisis route's intended gross cap stable while repair is pending."""
    if strategic_active:
        return cfg.strategic_cohort_crisis_gross
    if severity == "INCOMPLETE_UNIVERSE":
        return cfg.incomplete_universe_crisis_gross
    if severity == "INCOMPLETE_UNIVERSE_UNBACKED":
        return 0.0
    if severity in {"SEVERE", "ANCHOR_BREAK"}:
        return 0.0
    if severity == "CONCENTRATED":
        return cfg.concentrated_crisis_gross
    return cfg.crisis_gross


def _portfolio_drawdowns(account: AccountState, equity: float) -> tuple[float, float]:
    if account.positions:
        account.operating_peak = max(account.operating_peak or equity, equity)
    else:
        # Operating drawdown belongs to the currently deployed risk cohort.
        # Once the book is flat, the next cohort starts from the preserved cash
        # equity instead of inheriting an obsolete underwater high-water mark.
        account.operating_peak = equity
    account.capital_peak = max(account.capital_peak or account.initial_cash, equity)
    operating = max(0.0, 1.0 - equity / max(account.operating_peak, 1e-12))
    capital = max(0.0, 1.0 - equity / max(account.capital_peak, 1e-12))
    return operating, capital


def assess_risk(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
) -> RiskAssessment:
    """Assess market, breadth, correlation, holding, and drawdown risk.

    This function is the sole authority for gross-exposure caps. It updates the
    account's persistent shock/recovery state and returns the evidence used by
    the portfolio allocator and daily report.
    """
    if date not in broad.index or date not in tech.index:
        raise RuntimeError("risk indices missing at decision date")
    present = [
        symbol
        for symbol in REFERENCE_UNIVERSE
        if symbol in reference_panel and date in reference_panel[symbol].index
    ]
    expected = [
        symbol
        for symbol in REFERENCE_UNIVERSE
        if symbol in reference_panel and reference_panel[symbol].index.min() <= date
    ]
    industries = {INDUSTRY.get(symbol, "unknown") for symbol in present}
    expected_industries = {
        INDUSTRY.get(symbol, "unknown") for symbol in expected
    } - {"unknown"}
    minimum_symbols = max(3, math.ceil(0.80 * len(expected)))
    minimum_industries = min(5, len(expected_industries))
    if (
        len(present) < minimum_symbols
        or len(industries - {"unknown"}) < minimum_industries
    ):
        raise RuntimeError("independent risk basket coverage is insufficient")
    market_context = {
        "broad_ret5": scalar(broad.loc[date], "ret5", 0.0),
        "tech_ret5": scalar(tech.loc[date], "ret5", 0.0),
        "broad_ret60": scalar(
            broad.loc[date], f"ret{cfg.trend_medium}", 0.0
        ),
        "tech_ret60": scalar(
            tech.loc[date], f"ret{cfg.trend_medium}", 0.0
        ),
        "broad_ret120": scalar(
            broad.loc[date], f"ret{cfg.trend_slow}", 0.0
        ),
        "tech_ret120": scalar(
            tech.loc[date], f"ret{cfg.trend_slow}", 0.0
        ),
    }
    if not cfg.risk_overlay_enabled:
        account.risk = Risk.NORMAL.value
        account.shock_state = "NONE"
        return RiskAssessment(
            state=Risk.NORMAL,
            target_gross_cap=cfg.max_gross,
            votes=0,
            evidence={
                "counterfactual_risk_overlay_disabled": True,
                **market_context,
            },
            reasons=("risk overlay disabled for causal counterfactual",),
            shock_state="NONE",
        )
    fast_returns: list[float] = []
    below_ma20: list[bool] = []
    leader_failures: list[bool] = []
    sector_returns: dict[str, list[float]] = {}
    for symbol in present:
        row = reference_panel[symbol].loc[date]
        ret5 = scalar(row, "ret5")
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        if math.isfinite(ret5):
            fast_returns.append(ret5)
            sector_returns.setdefault(INDUSTRY.get(symbol, "unknown"), []).append(ret5)
        if math.isfinite(close) and math.isfinite(ma20):
            below_ma20.append(close < ma20)
        if symbol in leaders and leaders[symbol].mature:
            leader_failures.append(ret5 < -0.06 or (math.isfinite(close) and close < ma20))
    declining = float(np.mean(np.array(fast_returns) < 0)) if fast_returns else 0.0
    below = float(np.mean(below_ma20)) if below_ma20 else 0.0
    average_fast = float(np.mean(fast_returns)) if fast_returns else 0.0
    stressed_sectors = [float(np.mean(values)) < -0.04 for values in sector_returns.values() if values]
    sector_stress = float(np.mean(stressed_sectors)) if stressed_sectors else 0.0
    returns = (
        reference_returns.loc[:date].tail(61)
        if reference_returns is not None
        else cross_section_returns(reference_panel, date)
    )
    correlation = (
        float(
            returns.tail(cfg.correlation_window)
            .corr()
            .where(~np.eye(len(returns.columns), dtype=bool))
            .stack()
            .median()
        )
        if len(returns.columns) >= 4
        else float("nan")
    )
    recent_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(10).std(ddof=0))
    normal_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(60).std(ddof=0))
    vol_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
    leader_failure = float(np.mean(leader_failures)) if leader_failures else 0.0
    operating_dd, capital_dd = _portfolio_drawdowns(account, equity)
    tech_speed = min(scalar(tech.loc[date], "ret5", 0.0), scalar(tech.loc[date], "ret10", 0.0))
    broad_speed = min(scalar(broad.loc[date], "ret5", 0.0), scalar(broad.loc[date], "ret10", 0.0))

    votes = 0
    reasons: list[str] = []
    conditions = (
        (average_fast <= cfg.risk_fast_return and declining >= cfg.risk_breadth, "sector breadth shock"),
        (below >= cfg.risk_below_ma20, "MA20 structural damage"),
        (sector_stress >= 0.50, "multi-industry synchronization"),
        (math.isfinite(correlation) and correlation >= cfg.risk_correlation, "correlation shock"),
        (vol_ratio >= cfg.risk_volatility_ratio, "volatility shock"),
        (leader_failure >= 0.50, "leader failure"),
        (tech_speed <= -0.055 or broad_speed <= -0.045, "index velocity shock"),
    )
    for active, reason in conditions:
        if active:
            votes += 1
            reasons.append(reason)

    held_damage: list[bool] = []
    held_repair: list[bool] = []
    held_ret5: list[float] = []
    for symbol, position in account.positions.items():
        frame = user_panel.get(symbol)
        if position.shares <= 0 or frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        held_ret5.append(ret5)
        ret1 = float(frame.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
        held_damage.append(
            math.isfinite(close) and math.isfinite(ma20) and close < ma20 and ret5 <= -0.05
        )
        held_repair.append(math.isfinite(ret1) and ret1 > 0)
    held_damage_ratio = float(np.mean(held_damage)) if held_damage else 0.0
    held_repair_ratio = float(np.mean(held_repair)) if held_repair else 0.0
    anchor_damage: list[bool] = []
    anchor_ret5: list[float] = []
    for symbol in REFERENCE_ANCHORS:
        frame = reference_panel.get(symbol)
        if frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        anchor_ret5.append(ret5)
        anchor_damage.append(
            math.isfinite(close)
            and math.isfinite(ma20)
            and close < ma20
            and ret5 <= -0.06
        )
    complete_anchor_basket = len(anchor_damage) == len(REFERENCE_ANCHORS)
    reference_anchor_healthy = complete_anchor_basket and all(
        scalar(reference_panel[symbol].loc[date], "close")
        > scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_medium}")
        and scalar(
            reference_panel[symbol].loc[date],
            f"ret{cfg.trend_medium}",
            -1.0,
        )
        > 0
        for symbol in REFERENCE_ANCHORS
    )
    if reference_anchor_healthy:
        account.risk_streaks["reference_anchor_armed"] = 1
    reference_anchor_armed = (
        account.risk_streaks.get("reference_anchor_armed", 0) == 1
    )
    reference_anchor_break = complete_anchor_basket and all(anchor_damage)
    anchor_break_key = "reference_anchor_break"
    account.risk_streaks[anchor_break_key] = (
        account.risk_streaks.get(anchor_break_key, 0) + 1
        if reference_anchor_break
        else 0
    )
    immediate_reference_break = bool(
        reference_anchor_armed
        and complete_anchor_basket
        and all(
            scalar(reference_panel[symbol].loc[date], "close")
            < scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_fast}")
            for symbol in REFERENCE_ANCHORS
        )
        and float(np.mean(anchor_ret5)) <= cfg.severe_shock_ret5
    )
    shock_rearmed = True
    if account.last_shock_date and user_panel:
        clock = next(iter(user_panel.values()))
        rearm_days = (
            cfg.incomplete_universe_rearm_days
            if account.candidate_tenure.get(
                "last_shock_incomplete_universe", 0
            )
            == 1
            else cfg.shock_rearm_days
        )
        shock_rearmed = (
            len(clock.loc[pd.Timestamp(account.last_shock_date) : date]) - 1
            >= rearm_days
        )
        # A fully new book is a new risk cohort.  It must not inherit the
        # previous cohort's long rearm lock after the old positions were sold.
        if account.positions and all(
            position.entry_date
            and pd.Timestamp(position.entry_date)
            > pd.Timestamp(account.last_shock_date)
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
        any(held_damage)
        and operating_dd >= cfg.portfolio_break_dd
        and votes >= cfg.portfolio_break_votes
    )
    concentrated_break = (
        shock_rearmed and not account.protected_weights and concentrated_structure_break
    )
    break_key = "concentrated_break"
    account.risk_streaks[break_key] = (
        account.risk_streaks.get(break_key, 0) + 1 if concentrated_break else 0
    )
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
        and market_context["tech_ret120"] - market_context["broad_ret120"]
        >= cfg.narrow_anchor_divergence
    )
    immediate_severe_break = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
    persistent_market_break = (
        concentrated_structure_break
        and account.risk_streaks[break_key] >= cfg.concentrated_break_confirm_days
        and (
            votes >= 3
            or (bool(held_ret5) and float(np.mean(held_ret5)) <= -0.08)
        )
    )
    strategic_active = (
        account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    )
    strategic_current_gross = sum(
        position.shares * scalar(user_panel[symbol].loc[date], "close") / equity
        for symbol, position in account.positions.items()
        if symbol in user_panel
        and date in user_panel[symbol].index
        and position.shares > 0
    )
    strategic_guard_break = (
        strategic_active
        and account.candidate_tenure.get("strategic_profit_armed", 0) == 1
        and account.candidate_tenure.get("strategic_cohort_days", 0)
        >= cfg.strategic_cohort_guard_days
        and strategic_current_gross > cfg.strategic_cohort_residual_gross
    )
    strategic_tail_key = "strategic_tail_break"
    strategic_tail_observed = (
        strategic_active and operating_dd >= cfg.strategic_cohort_tail_line
    )
    account.risk_streaks[strategic_tail_key] = (
        account.risk_streaks.get(strategic_tail_key, 0) + 1
        if strategic_tail_observed
        else 0
    )
    strategic_tail_break = (
        strategic_tail_observed
        and account.risk_streaks[strategic_tail_key]
        >= cfg.strategic_cohort_tail_confirm_days
    )
    strategic_preserve = (
        strategic_active and not strategic_guard_break and not strategic_tail_break
    )
    strategic_universe_complete = len(user_panel) >= min(3, cfg.max_positions)
    anchor_industries = {
        leaders[symbol].industry
        for symbol in account.anchor_weights
        if symbol in leaders
    }
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
        or account.candidate_tenure.get("recovery_substitution_completed", 0) == 1
    )
    recovery_anchor_elapsed = 0
    if account.recovery_anchor_date and user_panel:
        clock = next(iter(user_panel.values()))
        recovery_anchor_elapsed = (
            len(clock.loc[pd.Timestamp(account.recovery_anchor_date) : date]) - 1
        )
    incomplete_universe_tail_break = (
        shock_rearmed
        and not account.protected_weights
        and bool(account.positions)
        and not strategic_active
        and not strategic_universe_complete
        and operating_dd
        >= (
            cfg.unbacked_universe_tail_dd
            if not credible_reserve
            and account.candidate_tenure.get("tactical_active", 0) == 0
            and (
                not account.anchor_weights
                or (
                    len(account.anchor_weights) >= 1
                    and recovery_anchor_elapsed
                    >= cfg.unbacked_recovery_anchor_min_days
                )
            )
            else cfg.incomplete_universe_tail_dd
        )
    )
    account_break_confirmed = (
        shock_rearmed
        and not account.protected_weights
        and not account.anchor_weights
        and not strategic_preserve
        and (
            emergency_tail_break
            or (concentrated_structure_break and immediate_severe_break)
            or persistent_market_break
        )
    )
    reference_anchor_confirmed = (
        shock_rearmed
        and not account.protected_weights
        and bool(account.anchor_weights)
        and reference_anchor_armed
        and (
            immediate_reference_break
            or account.risk_streaks[anchor_break_key] >= 2
        )
    )
    incomplete_universe_tail_break = (
        incomplete_universe_tail_break
        and not account_break_confirmed
        and not reference_anchor_confirmed
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
        len(next(iter(user_panel.values())).loc[max(recovery_transition_dates) : date])
        - 1
        if recovery_transition_dates and user_panel
        else math.inf
    )
    capital_drawdown_relapse = (
        bool(account.positions)
        and bool(account.protected_weights)
        and capital_dd >= cfg.capital_dd_crisis
        and operating_dd >= cfg.capital_guard_relapse_dd
        and sessions_since_recovery >= cfg.capital_guard_min_recovery_days
        and (
            held_damage_ratio >= cfg.concentrated_break_ratio
            or (votes >= 2 and sector_stress >= 0.50)
        )
    )
    concentrated_confirmed = (
        account_break_confirmed
        or reference_anchor_confirmed
        or incomplete_universe_tail_break
        or (shock_rearmed and strategic_tail_break)
        or capital_drawdown_relapse
    )

    previous = Risk(account.risk)
    capital_cooldown = account.candidate_tenure.get(
        "capital_guard_cooldown", 0
    )
    if capital_cooldown > 0:
        account.candidate_tenure["capital_guard_cooldown"] = (
            capital_cooldown - 1
        )
        account.risk = Risk.CRISIS.value
        account.shock_state = "CAPITAL_GUARD_COOLDOWN"
        return RiskAssessment(
            state=Risk.CRISIS,
            target_gross_cap=0.0,
            votes=votes,
            evidence={
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
            reasons=("capital guard cooldown after failed restoration",),
            shock_state="CAPITAL_GUARD_COOLDOWN",
        )
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
        protected_structure_ratio = (
            float(np.mean(protected_structures)) if protected_structures else 0.0
        )
    normalize_key = "protected_structure_normalization"
    account.risk_streaks[normalize_key] = (
        account.risk_streaks.get(normalize_key, 0) + 1
        if protected_structure_ratio >= 0.67
        else 0
    )
    if (
        account.protected_weights
        and previous is not Risk.CRISIS
        and account.positions
        and (
            capital_dd <= 1e-12
            or account.risk_streaks[normalize_key] >= cfg.recovery_risk_confirm_days
        )
    ):
        account.protected_weights.clear()
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
                scalar(row, "ret5", -1.0) > 0
                and scalar(row, "close") > scalar(row, f"ma{cfg.trend_fast}")
            )
        protected_fast_ratio = (
            float(np.mean(protected_fast_repairs))
            if protected_fast_repairs
            else 0.0
        )
        protected_swing_ratio = (
            float(np.mean(protected_swing_repairs))
            if protected_swing_repairs
            else 0.0
        )
        shock_elapsed = 0
        if account.shock_start_date:
            clock = next(iter(user_panel.values()))
            shock_elapsed = (
                len(clock.loc[pd.Timestamp(account.shock_start_date) : date]) - 1
            )
        v_market_repair = (
            average_fast >= cfg.fast_v_recovery_return
            and declining <= cfg.fast_v_recovery_breadth
            and below <= cfg.fast_v_recovery_below_ma20
            and (
                scalar(tech.loc[date], "ret5", 0.0)
                >= cfg.fast_v_recovery_index_return
                or scalar(broad.loc[date], "ret5", 0.0)
                >= cfg.fast_v_recovery_index_return
            )
        )
        fast_v_repair = (
            shock_elapsed >= cfg.severe_shock_wait_days
            and v_market_repair
            and protected_fast_ratio >= 0.50
        )
        persistent_v_repair = (
            shock_elapsed >= cfg.persistent_v_recovery_wait_days
            and len(account.protected_weights) == 1
            and v_market_repair
            and protected_swing_ratio >= 1.0
        )
        structural_independent_repair = (
            not account.anchor_weights
            and (
                scalar(broad.loc[date], "close")
                > scalar(broad.loc[date], f"ma{cfg.trend_fast}")
                or scalar(tech.loc[date], "close")
                > scalar(tech.loc[date], f"ma{cfg.trend_fast}")
            )
            and declining <= 0.55
            and below <= 0.60
            and repair_leaders >= 2
        )
        independent_repair = structural_independent_repair or fast_v_repair
        market_repair_key = "independent_market_repair"
        account.risk_streaks[market_repair_key] = (
            account.risk_streaks.get(market_repair_key, 0) + 1
            if independent_repair
            else 0
        )
        repair_confirm_days = (
            cfg.fast_v_recovery_confirm_days
            if fast_v_repair
            else cfg.recovery_risk_confirm_days
        )
        standard_repair_ready = (
            account.risk_streaks[market_repair_key] >= repair_confirm_days
        )
        persistent_repair_key = "persistent_v_market_repair"
        account.risk_streaks[persistent_repair_key] = (
            account.risk_streaks.get(persistent_repair_key, 0) + 1
            if persistent_v_repair
            else 0
        )
        persistent_repair_ready = (
            account.risk_streaks[persistent_repair_key]
            >= cfg.fast_v_recovery_confirm_days
            and not fast_v_repair
        )
        if standard_repair_ready or persistent_repair_ready:
            persistent_repair_confirmed = (
                persistent_repair_ready and not standard_repair_ready
            )
            expedited_repair = fast_v_repair or persistent_repair_confirmed
            account.protected_weights.clear()
            account.shock_start_date = ""
            account.shock_severity = "NORMAL"
            account.risk = Risk.CAUTION.value
            account.candidate_tenure["fast_v_recovery"] = int(expedited_repair)
            account.shock_state = (
                "FAST_V_RECOVERY"
                if expedited_repair
                else "ROTATION_RECOVERY"
            )
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
                    cfg.fast_v_recovery_gross
                    if expedited_repair
                    else cfg.recovery_target_gross,
                ),
                votes=votes,
                evidence={
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
                shock_state=(
                    "FAST_V_RECOVERY"
                    if expedited_repair
                    else "ROTATION_RECOVERY"
                ),
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
            clock_symbol = next(iter(account.protected_weights))
            clock = user_panel.get(clock_symbol)
            severe_wait_complete = bool(
                clock is not None
                and len(clock.loc[pd.Timestamp(account.shock_start_date) : date]) - 1
                >= cfg.severe_shock_wait_days
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
            severe_wait_complete = severe_wait_complete and bool(severe_structures) and (
                float(np.mean(severe_structures)) >= 0.67
            )
        repair_key = "concentrated_repair"
        account.risk_streaks[repair_key] = (
            account.risk_streaks.get(repair_key, 0) + 1
            if repair_ratio >= 0.67 and severe_wait_complete
            else 0
        )
        if account.risk_streaks[repair_key] >= cfg.concentrated_repair_days:
            state = Risk.CAUTION
            shock = "RECOVERY"
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
                target_gross_cap=cap,
                votes=votes,
                evidence={
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
            )
        account.risk = Risk.CRISIS.value
        account.shock_state = "PERSISTENT_STRESS"
        return RiskAssessment(
            state=Risk.CRISIS,
            target_gross_cap=_persistent_crisis_cap(
                account.shock_severity,
                cfg,
                strategic_active=strategic_active,
            ),
            votes=votes,
            evidence={
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
        )

    if concentrated_confirmed:
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
        if capital_drawdown_relapse:
            account.candidate_tenure[
                "capital_guard_cooldown"
            ] = cfg.capital_guard_cooldown_days
        account.candidate_tenure["last_shock_incomplete_universe"] = int(
            incomplete_universe_tail_break and credible_reserve
        )
        severe_held_move = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
        if incomplete_universe_tail_break:
            account.shock_severity = (
                "INCOMPLETE_UNIVERSE"
                if credible_reserve
                else "INCOMPLETE_UNIVERSE_UNBACKED"
            )
        elif reference_anchor_confirmed:
            account.shock_severity = (
                "SEVERE" if immediate_reference_break else "ANCHOR_BREAK"
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
        crisis_gross = (
            cfg.strategic_cohort_crisis_gross
            if strategic_active
            else cfg.incomplete_universe_crisis_gross
            if incomplete_universe_tail_break and credible_reserve
            else 0.0
        )
        concentrated_reason = (
            "confirmed strategic cohort capital guard"
            if strategic_active
            else "capital drawdown relapse in restored holdings"
            if capital_drawdown_relapse
            else "reserve-backed incomplete-universe tail guard"
            if incomplete_universe_tail_break and credible_reserve
            else "unbacked incomplete-universe capital exit"
            if incomplete_universe_tail_break
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
        )

    observed = Risk.NORMAL
    if (
        shock_rearmed
        and not account.protected_weights
        and capital_dd >= cfg.capital_dd_crisis
        and votes >= 4
    ):
        observed = Risk.CRISIS
    elif narrow_anchor_guard:
        observed = Risk.RISK_OFF
        reasons.append("narrow-market concentrated anchor damage")
    elif (
        (capital_dd >= cfg.capital_dd_risk_off or operating_dd >= 0.10)
        and votes >= 3
        and sector_stress >= 0.50
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
    if state is Risk.CRISIS and previous is not Risk.CRISIS:
        if not account.protected_weights:
            account.protected_weights = {
                symbol: position.shares
                * scalar(user_panel[symbol].loc[date], "close")
                / equity
                for symbol, position in account.positions.items()
                if symbol in user_panel
                and date in user_panel[symbol].index
                and position.shares > 0
            }
        account.shock_start_date = str(date.date())
        account.last_shock_date = str(date.date())
        account.candidate_tenure["last_shock_incomplete_universe"] = 0
        severe_held_move = bool(held_ret5) and (
            float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
        )
        account.shock_severity = (
            "SEVERE"
            if severe_held_move and votes >= 4
            else "CONCENTRATED"
            if severe_held_move
            else "MARKET"
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
            }
        )
    account.risk = state.value
    account.shock_state = shock
    crisis_cap = (
        0.0
        if account.shock_severity in {"SEVERE", "ANCHOR_BREAK"}
        else cfg.concentrated_crisis_gross
        if account.shock_severity == "CONCENTRATED"
        else cfg.crisis_gross
    )
    cap = {
        Risk.NORMAL: cfg.max_gross,
        Risk.CAUTION: (
            min(
                cfg.max_gross,
                max(cfg.caution_gross, strategic_current_gross),
            )
            if votes >= cfg.caution_gross_min_votes
            else cfg.max_gross
        ),
        Risk.RISK_OFF: (
            cfg.risk_off_gross
            if held_damage_ratio >= cfg.concentrated_break_ratio
            else cfg.max_gross
        ),
        Risk.CRISIS: crisis_cap,
    }[state]
    if narrow_anchor_guard and state is Risk.RISK_OFF:
        cap = cfg.narrow_anchor_guard_gross
    if state is Risk.RISK_OFF and strategic_preserve:
        cap = cfg.max_gross
    elif state is Risk.CRISIS and strategic_active:
        cap = cfg.strategic_cohort_crisis_gross
    return RiskAssessment(
        state=state,
        target_gross_cap=cap,
        votes=votes,
        evidence={
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
        reasons=tuple(reasons),
        shock_state=shock,
    )
