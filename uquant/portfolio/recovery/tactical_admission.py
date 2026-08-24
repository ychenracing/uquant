"""Empty-book tactical recovery admission stages."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...types import AccountState, LeaderScore, Opportunity, RiskAssessment, Target
from .targets import (
    controlled_oversold_rebound_targets,
    overextended_pullback_targets,
)

if TYPE_CHECKING:
    from ...portfolio_leaders import LeaderPortfolioPolicy

    type RecoveryPortfolioPolicy = LeaderPortfolioPolicy

type ReboundEvidence = tuple[LeaderScore, float, float, float, float, bool]


def _tactical_admission_is_open(
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    account: AccountState,
    level1_recovery_repair: bool,
    bounded_recovery_repair: bool,
) -> bool:
    return bool(
        not account.positions
        and not account.anchor_weights
        and account.candidate_tenure.get("tactical_active", 0) == 0
        and account.candidate_tenure.get("tactical_cooldown", 0) == 0
        and (
            not bool(risk.evidence.get("freeze_new_risk", False))
            or level1_recovery_repair
            or bounded_recovery_repair
        )
        and opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}
        and risk.state.value in {"NORMAL", "CAUTION"}
    )


def _deep_recovery_qualified(
    self: RecoveryPortfolioPolicy,
    *,
    frame: pd.DataFrame,
    date: pd.Timestamp,
    required_notional: float,
    ret1: float,
    ret5: float,
    ret20: float,
    ret120: float,
) -> bool:
    return bool(
        ret120 <= -0.35
        and ret20 >= -0.12
        and ret5 >= -0.06
        and ret1 <= -0.05
        and self._liquidity_confirmed(frame, date)
        and self._capacity_confirmed(frame, date, required_notional)
    )


def _pullback_structure(
    self: RecoveryPortfolioPolicy,
    *,
    frame: pd.DataFrame,
    date: pd.Timestamp,
    required_notional: float,
    close: float,
    ma120: float,
    ret20: float,
    ret120: float,
) -> bool:
    return bool(
        ret20 <= self.cfg.tactical_rebound_breadth_max_ret20
        and math.isfinite(close)
        and math.isfinite(ma120)
        and math.isfinite(ret120)
        and close >= ma120
        and self._liquidity_confirmed(frame, date)
        and self._capacity_confirmed(frame, date, required_notional)
    )


def _fast_rebound_qualified(
    self: RecoveryPortfolioPolicy,
    *,
    frame: pd.DataFrame,
    date: pd.Timestamp,
    account: AccountState,
    required_notional: float,
    close: float,
    ma120: float,
    ret5: float,
    ret20: float,
) -> bool:
    return bool(
        account.candidate_tenure.get("fast_v_recovery", 0) == 1
        and ret5 >= 0.10
        and ret20 < 0
        and math.isfinite(close)
        and math.isfinite(ma120)
        and close >= ma120
        and self._liquidity_confirmed(frame, date)
        and self._capacity_confirmed(frame, date, required_notional)
    )


def _scan_tactical_evidence(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[
    list[tuple[LeaderScore, float, float]],
    list[ReboundEvidence],
    list[tuple[LeaderScore, float, float]],
    bool,
]:
    deep_recovery: list[tuple[LeaderScore, float, float]] = []
    rebound_evidence: list[ReboundEvidence] = []
    fast_rebound: list[tuple[LeaderScore, float, float]] = []
    overextended_pullback = False
    required_notional = account.initial_cash * self.cfg.tactical_probe_weight * 0.90
    for symbol, score in leaders.items():
        if symbol not in user_panel or date not in user_panel[symbol].index:
            continue
        frame = user_panel[symbol]
        row = frame.loc[date]
        close = scalar(row, "close")
        ma120 = scalar(row, f"ma{self.cfg.trend_slow}")
        ret5 = scalar(row, "ret5", -1.0)
        ret20 = scalar(row, f"ret{self.cfg.trend_fast}", -1.0)
        ret60 = scalar(row, f"ret{self.cfg.trend_medium}", -1.0)
        ret120 = scalar(row, f"ret{self.cfg.trend_slow}", math.nan)
        ret1 = float(frame.loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
        if _deep_recovery_qualified(
            self,
            frame=frame,
            date=date,
            required_notional=required_notional,
            ret1=ret1,
            ret5=ret5,
            ret20=ret20,
            ret120=ret120,
        ):
            deep_recovery.append((score, ret20, ret120))
        pullback_structure = _pullback_structure(
            self,
            frame=frame,
            date=date,
            required_notional=required_notional,
            close=close,
            ma120=ma120,
            ret20=ret20,
            ret120=ret120,
        )
        current_reversal = bool(
            ret5 >= self.cfg.fast_v_recovery_return and ret60 >= self.cfg.tactical_rebound_min_ret60
        )
        qualified_current_reversal = bool(
            current_reversal and score.score >= self.cfg.high_confidence_entry_score
        )
        if (
            pullback_structure
            and ret120 > self.cfg.tactical_rebound_max_ret120
            and not qualified_current_reversal
        ):
            overextended_pullback = True
        shallow_rebound = bool(pullback_structure and ret120 <= self.cfg.tactical_rebound_max_ret120)
        if shallow_rebound:
            secular = bool(
                score.confidence >= self.cfg.leader_min_confidence
                and math.isfinite(ret120)
                and ret120 >= 0.0
                and score.score >= self.cfg.recovery_reserve_min_score
            )
            rebound_evidence.append((score, ret20, ret5, ret60, ret120, secular))
        elif pullback_structure and qualified_current_reversal:
            secular = bool(
                score.confidence >= self.cfg.leader_min_confidence
                and ret120 >= 0.0
                and score.score >= self.cfg.recovery_reserve_min_score
            )
            rebound_evidence.append((score, ret20, ret5, ret60, ret120, secular))
        if _fast_rebound_qualified(
            self,
            frame=frame,
            date=date,
            account=account,
            required_notional=required_notional,
            close=close,
            ma120=ma120,
            ret5=ret5,
            ret20=ret20,
        ):
            fast_rebound.append((score, ret5, ret20))
    return deep_recovery, rebound_evidence, fast_rebound, overextended_pullback


def _rebound_candidates(
    self: RecoveryPortfolioPolicy,
    rebound_evidence: list[ReboundEvidence],
    *,
    breadth_confirmed: bool,
) -> list[LeaderScore]:
    return [
        score
        for score, ret20, ret5, ret60, ret120, _ in rebound_evidence
        if (
            ret20 <= self.cfg.tactical_rebound_max_ret20
            and ret60 >= self.cfg.tactical_rebound_min_ret60
            and (ret5 <= 0.0 or score.score >= self.cfg.high_confidence_entry_score)
        )
        or (
            ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
            and ret60 >= self.cfg.tactical_rebound_oversold_min_ret60
        )
        or (
            ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
            and ret60 >= self.cfg.recovery_transition_weak_leg_ret120
            and ret120 <= self.cfg.strategic_long_cycle_max_tech_ret120
            and score.score >= self.cfg.recovery_reserve_min_score
        )
        or (
            ret20 <= self.cfg.tactical_rebound_max_ret20
            and score.score >= self.cfg.high_confidence_entry_score
            and ret60 <= -self.cfg.recovery_crash_drawdown
        )
        or (
            ret5 >= self.cfg.fast_v_recovery_return
            and ret60 >= self.cfg.tactical_rebound_min_ret60
            and score.score >= self.cfg.high_confidence_entry_score
        )
        or breadth_confirmed
    ]


def _overextended_targets(
    self: RecoveryPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    deep_recovery: list[tuple[LeaderScore, float, float]],
    rebound_evidence: list[ReboundEvidence],
    fast_rebound: list[tuple[LeaderScore, float, float]],
    overextended_pullback: bool,
) -> tuple[Target, ...] | None:
    if not (overextended_pullback and not rebound_evidence and not deep_recovery and not fast_rebound):
        return None
    account.candidate_tenure["tactical_cooldown"] = max(
        account.candidate_tenure.get("tactical_cooldown", 0),
        self.cfg.tactical_overheat_cooldown_days,
    )
    account.candidate_tenure["tactical_overheat_cooldown"] = 1
    return overextended_pullback_targets(self=self, leaders=leaders, account=account)


def _secular_rebound_candidates(
    self: RecoveryPortfolioPolicy,
    rebound_evidence: list[ReboundEvidence],
    *,
    breadth_confirmed: bool,
) -> list[LeaderScore]:
    return [
        score
        for score, ret20, ret5, ret60, ret120, secular in rebound_evidence
        if secular
        and (
            (
                ret20 <= self.cfg.tactical_rebound_max_ret20
                and ret60 >= self.cfg.tactical_rebound_min_ret60
                and (ret5 <= 0.0 or score.score >= self.cfg.high_confidence_entry_score)
            )
            or (
                ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                and ret60 >= self.cfg.tactical_rebound_oversold_min_ret60
            )
            or (
                ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                and ret60 >= self.cfg.recovery_transition_weak_leg_ret120
                and ret120 <= self.cfg.strategic_long_cycle_max_tech_ret120
                and score.score >= self.cfg.recovery_reserve_min_score
            )
            or (
                ret20 <= self.cfg.tactical_rebound_max_ret20
                and score.score >= self.cfg.high_confidence_entry_score
                and ret60 <= -self.cfg.recovery_crash_drawdown
            )
            or (
                ret5 >= self.cfg.fast_v_recovery_return
                and ret60 >= self.cfg.tactical_rebound_min_ret60
                and score.score >= self.cfg.high_confidence_entry_score
            )
            or breadth_confirmed
        )
    ]


def _selected_tactical_targets(
    self: RecoveryPortfolioPolicy,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    deep_recovery: list[tuple[LeaderScore, float, float]],
    rebound: list[LeaderScore],
    secular_rebound: list[LeaderScore],
    fast_rebound: list[tuple[LeaderScore, float, float]],
    tactical_recovery_market: bool,
    transitional_recovery_market: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    if len(deep_recovery) < 2:
        deep_recovery = [
            item
            for item in deep_recovery
            if item[0].confidence >= self.cfg.leader_min_confidence
            and item[0].score >= self.cfg.recovery_reserve_min_score
        ]
    if not tactical_recovery_market:
        rebound = secular_rebound
        fast_rebound = []
    if transitional_recovery_market and not weak_secular_market:
        rebound = secular_rebound
        fast_rebound = []
    if not (deep_recovery or rebound or fast_rebound):
        return None
    if fast_rebound:
        pick = max(
            fast_rebound,
            key=lambda item: (item[1], item[2], item[0].score, item[0].symbol),
        )[0]
        account.candidate_tenure["tactical_promotable"] = 1
        account.tactical_anchor_symbol = pick.symbol
    elif deep_recovery:
        pick = max(
            deep_recovery,
            key=lambda item: (-item[2], item[1], item[0].score, item[0].symbol),
        )[0]
        account.candidate_tenure["tactical_promotable"] = 1
        account.tactical_anchor_symbol = pick.symbol
    else:
        pick = max(rebound, key=lambda item: (item.score, item.symbol))
        fast_v_candidate = account.candidate_tenure.get("fast_v_recovery", 0) == 1
        account.candidate_tenure["tactical_promotable"] = int(fast_v_candidate)
        account.tactical_anchor_symbol = pick.symbol if fast_v_candidate else ""
    account.candidate_tenure["tactical_active"] = 1
    return controlled_oversold_rebound_targets(
        self=self,
        pick=pick,
        risk=risk,
        leaders=leaders,
        account=account,
    )


def tactical_admission_targets(
    self: RecoveryPortfolioPolicy,
    *,
    opportunity: Opportunity,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    level1_recovery_repair: bool,
    bounded_recovery_repair: bool,
    tactical_recovery_market: bool,
    transitional_recovery_market: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    """Evaluate only the empty-book tactical recovery route."""

    if not _tactical_admission_is_open(
        opportunity=opportunity,
        risk=risk,
        account=account,
        level1_recovery_repair=level1_recovery_repair,
        bounded_recovery_repair=bounded_recovery_repair,
    ):
        return None
    deep_recovery, rebound_evidence, fast_rebound, overextended_pullback = _scan_tactical_evidence(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
    )
    overextended_targets = _overextended_targets(
        self,
        leaders=leaders,
        account=account,
        deep_recovery=deep_recovery,
        rebound_evidence=rebound_evidence,
        fast_rebound=fast_rebound,
        overextended_pullback=overextended_pullback,
    )
    if overextended_targets is not None:
        return overextended_targets
    rebound_breadth = {
        score.industry for score, _, _, _, _, _ in rebound_evidence if score.industry != "unknown"
    }
    breadth_confirmed = bool(len(rebound_breadth) >= self.cfg.tactical_rebound_min_industries)
    rebound = _rebound_candidates(
        self,
        rebound_evidence,
        breadth_confirmed=breadth_confirmed,
    )
    secular_rebound = _secular_rebound_candidates(
        self,
        rebound_evidence,
        breadth_confirmed=breadth_confirmed,
    )
    return _selected_tactical_targets(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        deep_recovery=deep_recovery,
        rebound=rebound,
        secular_rebound=secular_rebound,
        fast_rebound=fast_rebound,
        tactical_recovery_market=tactical_recovery_market,
        transitional_recovery_market=transitional_recovery_market,
        weak_secular_market=weak_secular_market,
    )
