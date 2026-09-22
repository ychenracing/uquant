"""Opportunity-axis classifier, independent from risk-budget ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .reference import ReferenceContext
from .types import AccountState, LeaderScore, Opportunity, Risk


@dataclass(frozen=True, slots=True)
class _OpportunityMarket:
    bear_trend: bool
    breadth20_ratio: float
    breadth60_ratio: float
    broad_bull: bool
    broad_ret1: float
    broad_row: pd.Series
    tech_bull: bool
    tech_ret1: float
    tech_row: pd.Series


@dataclass(frozen=True, slots=True)
class _OpportunityEvidence:
    mature_count: int
    recent_crash: bool
    regime: Opportunity
    score_gap: float
    stable: bool


def _collect_opportunity_market_evidence(
    *,
    account: AccountState,
    broad: pd.DataFrame,
    cfg: SystemConfig,
    date: pd.Timestamp,
    reference_context: ReferenceContext | None,
    reference_panel: dict[str, pd.DataFrame],
    tech: pd.DataFrame,
) -> _OpportunityMarket:
    broad_row = cast(pd.Series, broad.loc[date])
    tech_row = cast(pd.Series, tech.loc[date])
    breadth20: list[bool] = []
    breadth60: list[bool] = []
    for frame in reference_panel.values():
        if date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        if math.isfinite(close) and math.isfinite(ma20):
            breadth20.append(close > ma20)
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        if math.isfinite(close) and math.isfinite(ma60):
            breadth60.append(close > ma60)
    breadth20_ratio = (
        reference_context.breadth20
        if reference_context is not None
        else float(np.mean(breadth20))
        if breadth20
        else 0.0
    )
    breadth60_ratio = (
        reference_context.breadth60
        if reference_context is not None
        else float(np.mean(breadth60))
        if breadth60
        else 0.0
    )
    account.risk_signal_state["opportunity_breadth20"] = breadth20_ratio
    account.risk_signal_state["opportunity_breadth60"] = breadth60_ratio
    tech_bull = scalar(tech_row, "close") > scalar(tech_row, f"ma{cfg.trend_medium}") and scalar(
        tech_row, f"ma{cfg.trend_fast}"
    ) > scalar(tech_row, f"ma{cfg.trend_medium}")
    broad_bull = scalar(broad_row, "close") > scalar(broad_row, f"ma{cfg.trend_medium}") and scalar(
        broad_row, f"ma{cfg.trend_fast}"
    ) > scalar(broad_row, f"ma{cfg.trend_medium}")
    bear_trend = (
        scalar(broad_row, "close") < scalar(broad_row, f"ma{cfg.trend_medium}")
        and scalar(broad_row, f"ma{cfg.trend_fast}") < scalar(broad_row, f"ma{cfg.trend_medium}")
        and (scalar(tech_row, "close") < scalar(tech_row, f"ma{cfg.trend_medium}"))
        and (scalar(tech_row, f"ma{cfg.trend_fast}") < scalar(tech_row, f"ma{cfg.trend_medium}"))
    )
    broad_ret1 = float(broad.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
    tech_ret1 = float(tech.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
    return _OpportunityMarket(
        bear_trend=bear_trend,
        breadth20_ratio=breadth20_ratio,
        breadth60_ratio=breadth60_ratio,
        broad_bull=broad_bull,
        broad_ret1=broad_ret1,
        broad_row=broad_row,
        tech_bull=tech_bull,
        tech_ret1=tech_ret1,
        tech_row=tech_row,
    )


def _transition_opportunity_regime(
    *, account: AccountState, evidence: int, fast_flip: bool, run: int
) -> Opportunity:
    previous = Opportunity(account.opportunity)
    regime = previous
    if previous in {Opportunity.STRONG_TREND, Opportunity.TREND}:
        if evidence == -1 and run >= 3:
            regime = Opportunity.WEAK
        elif evidence == 0 and run >= 3:
            regime = Opportunity.CHOPPY
    elif fast_flip or (evidence == 1 and run >= 3):
        regime = Opportunity.TREND
    elif previous is Opportunity.CHOPPY and evidence == -1 and (run >= 3):
        regime = Opportunity.WEAK
    elif previous is Opportunity.WEAK and evidence == 0 and (run >= 3):
        regime = Opportunity.CHOPPY
    return regime


def _evaluate_opportunity_evidence(
    *,
    account: AccountState,
    bear_trend: bool,
    breadth20_ratio: float,
    breadth60_ratio: float,
    broad_bull: bool,
    cfg: SystemConfig,
    date: pd.Timestamp,
    fast_flip: bool,
    leaders: dict[str, LeaderScore],
    tech: pd.DataFrame,
    tech_bull: bool,
    tech_row: pd.Series,
) -> _OpportunityEvidence:
    evidence = 0
    if fast_flip or breadth60_ratio >= 0.65 or (breadth60_ratio >= 0.4 and (tech_bull or broad_bull)):
        evidence = 1
    elif breadth60_ratio < 0.35 and (bear_trend or scalar(tech_row, "drawdown120", 0.0) <= -0.25):
        evidence = -1
    previous_evidence = account.risk_streaks.get("opportunity_evidence", 99)
    if evidence == previous_evidence:
        run = account.risk_streaks.get("opportunity_evidence_run", 0) + 1
    else:
        run = 1
    account.risk_streaks["opportunity_evidence"] = evidence
    account.risk_streaks["opportunity_evidence_run"] = run
    ranked = sorted((item.score for item in leaders.values()), reverse=True)
    regime = _transition_opportunity_regime(account=account, evidence=evidence, fast_flip=fast_flip, run=run)
    mature_count = sum(item.mature for item in leaders.values())
    score_gap = ranked[0] - ranked[2] if len(ranked) >= 3 else ranked[0] if ranked else 0.0
    tech_history = tech.loc[:date, "close"]
    recent_crash = False
    if len(tech_history) >= 60:
        for point in tech_history.tail(cfg.recovery_crash_lookback + 1).index:
            history = tech.loc[:point, "close"].tail(60)
            if (
                len(history) >= 20
                and float(history.iloc[-1] / history.max() - 1.0) <= -cfg.recovery_crash_drawdown
            ):
                recent_crash = True
                break
    stable = (
        len(tech_history) > cfg.recovery_stabilize_days
        and float(tech_history.iloc[-1] / tech_history.iloc[-1 - cfg.recovery_stabilize_days] - 1.0) > 0
        and (breadth20_ratio >= cfg.recovery_breadth_min)
    )
    return _OpportunityEvidence(
        mature_count=mature_count,
        recent_crash=recent_crash,
        regime=regime,
        score_gap=score_gap,
        stable=stable,
    )


def classify_opportunity(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    risk: Risk,
    account: AccountState,
    cfg: SystemConfig,
    reference_context: ReferenceContext | None = None,
) -> Opportunity:
    """Classify the opportunity regime with breadth, trend, and leader evidence.

    Regime changes require persistent evidence stored in the account state, so
    daily noise cannot freely switch the allocator between incompatible modes.
    """
    market = _collect_opportunity_market_evidence(
        account=account,
        broad=broad,
        cfg=cfg,
        date=date,
        reference_context=reference_context,
        reference_panel=reference_panel,
        tech=tech,
    )
    fast_flip = (
        (market.broad_ret1 >= 0.03
        and scalar(market.broad_row, "close") > scalar(market.broad_row, f"ma{cfg.trend_fast}"))
        or (
            market.tech_ret1 >= 0.03
            and scalar(market.tech_row, "close") > scalar(market.tech_row, f"ma{cfg.trend_fast}")
        )
    )
    assessment = _evaluate_opportunity_evidence(
        account=account,
        bear_trend=market.bear_trend,
        breadth20_ratio=market.breadth20_ratio,
        breadth60_ratio=market.breadth60_ratio,
        broad_bull=market.broad_bull,
        cfg=cfg,
        date=date,
        fast_flip=fast_flip,
        leaders=leaders,
        tech=tech,
        tech_bull=market.tech_bull,
        tech_row=market.tech_row,
    )
    account.risk_streaks["recovery_stable"] = (
        account.risk_streaks.get("recovery_stable", 0) + 1
        if assessment.recent_crash and assessment.stable
        else 0
    )
    recovery_confirmed = account.risk_streaks["recovery_stable"] >= cfg.recovery_confirm_days
    if risk in {Risk.CRISIS, Risk.RISK_OFF}:
        state = Opportunity.WEAK
    elif assessment.regime in {Opportunity.TREND, Opportunity.STRONG_TREND} and (
        market.breadth60_ratio >= 0.62 and assessment.mature_count >= 2 and (assessment.score_gap >= 0.06)
    ):
        state = Opportunity.STRONG_TREND
    elif assessment.regime in {Opportunity.TREND, Opportunity.STRONG_TREND}:
        state = Opportunity.TREND
    elif recovery_confirmed:
        state = Opportunity.RECOVERY
    else:
        state = assessment.regime
    account.opportunity = state.value
    return state
