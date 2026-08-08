"""Independent AI risk radar and the only owner of portfolio risk caps."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import cross_section_returns, scalar
from .leader import INDUSTRY, REFERENCE_UNIVERSE
from .types import AccountState, LeaderScore, Risk, RiskAssessment


def _portfolio_drawdowns(account: AccountState, equity: float) -> tuple[float, float]:
    account.operating_peak = max(account.operating_peak or equity, equity)
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
    if date not in broad.index or date not in tech.index:
        raise RuntimeError("risk indices missing at decision date")
    present = [
        symbol
        for symbol in REFERENCE_UNIVERSE
        if symbol in reference_panel and date in reference_panel[symbol].index
    ]
    industries = {INDUSTRY.get(symbol, "unknown") for symbol in present}
    if len(present) < 20 or len(industries - {"unknown"}) < 5:
        raise RuntimeError("independent risk basket coverage is insufficient")
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
        (average_fast <= cfg.risk_fast_return and declining >= cfg.risk_breadth, "AI breadth shock"),
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
    shock_rearmed = True
    if account.last_shock_date and user_panel:
        clock = next(iter(user_panel.values()))
        shock_rearmed = (
            len(clock.loc[pd.Timestamp(account.last_shock_date) : date]) - 1
            >= cfg.shock_rearm_days
        )
    concentrated_structure_break = (
        len(held_damage) >= 1
        and operating_dd >= cfg.concentrated_break_dd
        and held_damage_ratio >= cfg.concentrated_break_ratio
    )
    emergency_tail_break = (
        bool(held_damage)
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
    immediate_severe_break = bool(held_ret5) and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
    concentrated_confirmed = shock_rearmed and not account.protected_weights and (
        emergency_tail_break
        or (concentrated_structure_break and immediate_severe_break)
        or account.risk_streaks[break_key] >= cfg.concentrated_break_confirm_days
    )

    previous = Risk(account.risk)
    if (
        account.protected_weights
        and previous is not Risk.CRISIS
        and capital_dd <= 1e-12
        and account.positions
    ):
        account.protected_weights.clear()
        account.shock_start_date = ""
        account.shock_severity = "NORMAL"
    if previous is Risk.CRISIS and account.protected_weights:
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
        if account.shock_severity == "SEVERE" and account.shock_start_date:
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
            recovery_gross = (
                cfg.severe_recovery_gross
                if account.shock_severity == "SEVERE"
                else cfg.recovery_target_gross
            )
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
            target_gross_cap=cfg.concentrated_crisis_gross,
            votes=votes,
            evidence={
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
        account.shock_severity = (
            "SEVERE"
            if held_ret5 and float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
            else "NORMAL"
        )
        state = Risk.CRISIS
        shock = "SHOCK"
        account.risk = state.value
        account.shock_state = shock
        account.risk_streaks["concentrated_repair"] = 0
        account.risk_events.append(
            {
                "date": str(date.date()),
                "from": previous.value,
                "to": state.value,
                "votes": votes,
                "reasons": ["confirmed concentrated leader break"],
            }
        )
        return RiskAssessment(
            state=state,
            target_gross_cap=cfg.concentrated_crisis_gross,
            votes=votes,
            evidence={
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
            reasons=("confirmed concentrated leader break",),
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
    state = observed if account.risk_streaks[key] >= required else previous
    if state is Risk.CRISIS:
        shock = "SHOCK" if previous is not Risk.CRISIS else "PERSISTENT_STRESS"
    elif previous is Risk.CRISIS and state in {Risk.RISK_OFF, Risk.CAUTION}:
        shock = "RECOVERY"
    elif account.shock_state == "RECOVERY" and observed in {Risk.RISK_OFF, Risk.CRISIS}:
        shock = "FAILED_REPAIR"
    else:
        shock = "NONE" if state is Risk.NORMAL else account.shock_state
    if state != previous:
        account.risk_events.append(
            {
                "date": str(date.date()),
                "from": previous.value,
                "to": state.value,
                "votes": votes,
                "reasons": reasons,
            }
        )
    account.risk = state.value
    account.shock_state = shock
    cap = {
        Risk.NORMAL: cfg.max_gross,
        Risk.CAUTION: cfg.max_gross,
        Risk.RISK_OFF: cfg.max_gross if held_damage_ratio < cfg.concentrated_break_ratio else cfg.risk_off_gross,
        Risk.CRISIS: cfg.crisis_gross,
    }[state]
    return RiskAssessment(
        state=state,
        target_gross_cap=cap,
        votes=votes,
        evidence={
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
        reasons=tuple(reasons),
        shock_state=shock,
    )
