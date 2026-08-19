"""Independent sector risk radar and the only owner of portfolio risk caps."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import cross_section_returns, scalar
from .leader import INDUSTRY, REFERENCE_UNIVERSE, credible_recovery_reserve
from .reference import ReferenceContext
from .risk_sector import (
    SectorGuardTransition,
    SectorObservation,
    observe_deployed_sector,
    update_sector_guard,
)
from .risk_sentinel.integration import integrate_freeze_only, sentinel_cap_for_level
from .risk_sentinel.models import SentinelAssessment, SentinelLevel
from .risk_sentinel.service import apply_causal_hysteresis
from .types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment

# Compatibility export only. Production anchors live in AccountState and are
# selected from reference evidence; no symbol receives a static risk role.
REFERENCE_ANCHORS: tuple[str, ...] = ()

EVIDENCE_FAMILY_MEMBERS: dict[str, tuple[str, ...]] = {
    "market_velocity": ("index_velocity",),
    "breadth_structure": (
        "sector_breadth_shock",
        "below_ma20_structure",
        "multi_industry_sync",
    ),
    "covariance_stress": ("correlation_shock", "volatility_shock"),
    "leadership_damage": ("leader_failure", "anchor_break"),
    "live_book_damage": ("live_book_damage",),
    "capital_damage": ("capital_damage",),
}


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
        and observation.negative_exposure
        >= cfg.sector_weighted_negative_exposure
    )


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


def _evidence_family_votes(indicators: Mapping[str, bool]) -> dict[str, bool]:
    """Cap correlated indicators at one vote per independent evidence family."""
    return {
        family: any(bool(indicators.get(member, False)) for member in members)
        for family, members in EVIDENCE_FAMILY_MEMBERS.items()
    }


def _strategic_grace_supported(
    *,
    account: AccountState,
) -> bool:
    """Protect only an evidenced early-cycle strategic reset."""
    return bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_early_cycle_epoch", -1)
        == account.strategic_epoch
    )


def _strategic_damage_guard_required(
    *,
    account: AccountState,
    operating_drawdown: float,
    transition_damage: float,
    votes: int,
    cfg: SystemConfig,
) -> bool:
    """Trim an immature concentrated handoff while preserving its lifecycle owner."""
    guard_already_claimed = bool(
        account.strategic_epoch > 0
        and account.strategic_epoch
        in {
            account.candidate_tenure.get(
                "strategic_damage_guard_active_epoch", -1
            ),
            account.candidate_tenure.get(
                "strategic_damage_guard_complete_epoch", -1
            ),
            account.candidate_tenure.get("strategic_damage_trim_epoch", -1),
        }
    )
    external_risk_already_claimed = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get(
            "strategic_external_risk_epoch", -1
        )
        == account.strategic_epoch
    )
    emerging = account.strategic_candidate_signature.startswith(
        "strategic_qualification:EMERGING_SECULAR:"
    )
    grace_days = (
        cfg.capital_budget_emerging_cohort_grace_days
        if emerging
        else cfg.capital_budget_new_cohort_grace_days
    )
    return bool(
        account.candidate_tenure.get("strategic_cohort_active", 0) == 1
        and account.candidate_tenure.get("strategic_cohort_started", 0) == 1
        and not guard_already_claimed
        and not external_risk_already_claimed
        and account.candidate_tenure.get("strategic_cohort_days", 0) < grace_days
        and operating_drawdown >= cfg.strategic_damage_guard_dd
        # This is an early-warning owner.  Once ordinary operating caution is
        # reached, the independent capital ladder already owns the reduction;
        # a second, tighter strategic cap would double-count the same damage.
        and operating_drawdown < cfg.operating_dd_caution
        and transition_damage >= cfg.strategic_damage_guard_transition
        # The live-book drawdown and transition-damage thresholds are already
        # two separate causal gates.  Require one corroborating evidence
        # family, but do not make a small configured universe wait for a
        # second correlated family while its actually funded book is falling.
        and votes >= 1
    )


def _strategic_damage_guard_persists(
    account: AccountState,
    cfg: SystemConfig,
) -> bool:
    """Keep the cap active only while a concentrated handoff is unfinished."""
    positive_targets = [
        float(weight)
        for weight in account.strategic_cohort_targets.values()
        if float(weight) > 1e-12
    ]
    return bool(
        len(positive_targets) <= 1
        or max(positive_targets, default=0.0) > cfg.max_symbol_weight + 1e-12
    )


def _strategic_guard_level2_overlay_required(account: AccountState) -> bool:
    """Let an active strategic guard own a bounded level-2 refinement."""
    return bool(
        account.strategic_epoch > 0
        and account.capital_budget_level >= 2
        and account.candidate_tenure.get(
            "strategic_damage_guard_active_epoch", -1
        )
        == account.strategic_epoch
        and account.candidate_tenure.get(
            "strategic_damage_guard_complete_epoch", -1
        )
        != account.strategic_epoch
    )


def _strategic_damage_guard_active(account: AccountState) -> bool:
    """Keep a claimed guard authoritative until the strategy records repair."""
    return bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get(
            "strategic_damage_guard_active_epoch", -1
        )
        == account.strategic_epoch
        and account.candidate_tenure.get(
            "strategic_damage_guard_complete_epoch", -1
        )
        != account.strategic_epoch
    )


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
        return (
            cfg.risk_off_gross
            if reserve_backed
            else cfg.concentrated_crisis_gross
        )
    if severity in {"SEVERE", "ANCHOR_BREAK"}:
        return cfg.severe_crisis_gross
    if severity == "CONCENTRATED":
        return cfg.concentrated_crisis_gross
    return cfg.market_crisis_gross


def _strategic_crisis_severity(
    *,
    strategic_active: bool,
    reference_anchor_confirmed: bool,
    live_core_positions: int,
) -> str:
    """Classify strategic crises by live-book concentration, not its label."""

    if not strategic_active:
        return "NORMAL"
    if live_core_positions <= 1:
        return "CONCENTRATED"
    del reference_anchor_confirmed
    return "MARKET"


def _dynamic_anchor_candidate(leaders: dict[str, LeaderScore], cfg: SystemConfig) -> list[str]:
    """Select a deterministic, group-balanced anchor basket from references."""
    ranked = sorted(
        (
            item
            for symbol, item in leaders.items()
            if symbol in REFERENCE_UNIVERSE
            and item.industry != "unknown"
            and item.confidence >= cfg.leader_min_confidence
            and item.components.get("secular_score", 0.0) >= cfg.risk_anchor_min_secular_score
        ),
        key=lambda item: (
            -item.components.get("secular_score", 0.0),
            -item.score,
            item.symbol,
        ),
    )
    selected: list[str] = []
    groups: set[str] = set()
    for item in ranked:
        if item.industry not in groups:
            selected.append(item.symbol)
            groups.add(item.industry)
        if len(selected) >= cfg.risk_anchor_count:
            break
    for item in ranked:
        if item.symbol not in selected:
            selected.append(item.symbol)
        if len(selected) >= cfg.risk_anchor_count:
            break
    return selected


def _update_dynamic_anchors(
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    cfg: SystemConfig,
    allow_reanchor: bool,
) -> tuple[str, ...]:
    """Apply confirmation hysteresis and reset break state on a true re-anchor."""
    if not cfg.dynamic_risk_anchors_enabled:
        return ()
    candidate = _dynamic_anchor_candidate(leaders, cfg)
    candidate_groups = {
        leaders[symbol].industry
        for symbol in candidate
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    signature = ",".join(candidate)
    current_signature = account.risk_anchor_signature
    if len(candidate) != cfg.risk_anchor_count or len(candidate_groups) < cfg.risk_anchor_min_groups:
        # Confirmation must be consecutive.  Missing coverage/evidence cannot
        # bridge two otherwise unrelated candidate periods.  More importantly,
        # a partial candidate must never replace a previously complete basket
        # and silently disarm its structural-break evidence.
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
        return tuple(account.risk_anchor_symbols)
    if not allow_reanchor:
        # Do not replace damaged sentinels with the day's survivors while a
        # transition is under way; that would erase the very break they are
        # meant to observe.
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
        return tuple(account.risk_anchor_symbols)
    if signature and signature != current_signature:
        if signature == account.risk_anchor_candidate_signature:
            account.risk_anchor_candidate_streak += 1
        else:
            account.risk_anchor_candidate_signature = signature
            account.risk_anchor_candidate_streak = 1
        if account.risk_anchor_candidate_streak >= cfg.risk_anchor_confirm_days:
            account.risk_anchor_symbols = candidate
            account.risk_anchor_signature = signature
            account.risk_anchor_candidate_signature = ""
            account.risk_anchor_candidate_streak = 0
            account.risk_streaks["reference_anchor_armed"] = 0
            account.risk_streaks["reference_anchor_break"] = 0
    elif signature == current_signature:
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
    return tuple(account.risk_anchor_symbols)


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


def _update_capital_budget_ladder(
    account: AccountState,
    *,
    observed_level: int,
    repair_confirmed: bool,
    repair_days: int,
) -> None:
    """Escalate immediately and repair at most one capital tier per window."""
    current = account.capital_budget_level
    if observed_level > current:
        account.capital_budget_level = observed_level
        account.capital_budget_repair_streak = 0
        return
    if observed_level < current and repair_confirmed:
        account.capital_budget_repair_streak += 1
        if account.capital_budget_repair_streak >= repair_days:
            account.capital_budget_level = max(observed_level, current - 1)
            account.capital_budget_repair_streak = 0
        return
    account.capital_budget_repair_streak = 0


def _capital_budget_repair_drawdown_confirmed(
    *,
    level: int,
    capital_drawdown: float,
    operating_drawdown: float,
    cfg: SystemConfig,
) -> bool:
    """Require drawdown repair before releasing a persistent capital tier."""

    threshold = (
        cfg.capital_dd_crisis
        if level >= 4
        else cfg.capital_budget_level3_dd
        if level >= 3
        else cfg.capital_budget_level2_dd
        if level >= 2
        else cfg.operating_dd_caution
    )
    return max(capital_drawdown, operating_drawdown) < threshold


def _assess_base_risk(
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
    reference_context: ReferenceContext | None = None,
    configured_universe_size: int | None = None,
) -> RiskAssessment:
    """Assess market, breadth, correlation, holding, and drawdown risk.

    This function is the sole authority for gross-exposure caps. It updates the
    account's persistent shock/recovery state and returns the evidence used by
    the portfolio allocator and daily report.
    """
    if date not in broad.index or date not in tech.index:
        raise RuntimeError("risk indices missing at decision date")
    del configured_universe_size
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
    expected_industries = {INDUSTRY.get(symbol, "unknown") for symbol in expected} - {"unknown"}
    minimum_symbols = max(3, math.ceil(0.80 * len(expected)))
    minimum_industries = min(5, len(expected_industries))
    if len(present) < minimum_symbols or len(industries - {"unknown"}) < minimum_industries:
        raise RuntimeError("independent risk basket coverage is insufficient")
    market_context = {
        "broad_ret5": scalar(broad.loc[date], "ret5", 0.0),
        "tech_ret5": scalar(tech.loc[date], "ret5", 0.0),
        "broad_ret20": scalar(broad.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "tech_ret20": scalar(tech.loc[date], f"ret{cfg.trend_fast}", 0.0),
        "broad_ret60": scalar(broad.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "tech_ret60": scalar(tech.loc[date], f"ret{cfg.trend_medium}", 0.0),
        "broad_ret120": scalar(broad.loc[date], f"ret{cfg.trend_slow}", 0.0),
        "tech_ret120": scalar(tech.loc[date], f"ret{cfg.trend_slow}", 0.0),
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
                "evidence_families": EVIDENCE_FAMILY_MEMBERS,
                "family_votes": {family: False for family in EVIDENCE_FAMILY_MEMBERS},
                "family_vote_count": 0,
                **market_context,
            },
            reasons=("risk overlay disabled for causal counterfactual",),
            shock_state="NONE",
        )
    fast_returns: list[float] = []
    below_ma20: list[bool] = []
    above_ma60: list[bool] = []
    leader_failures: list[bool] = []
    sector_returns: dict[str, list[float]] = {}
    sector_below20: dict[str, list[bool]] = {}
    sector_above60: dict[str, list[bool]] = {}
    for symbol in present:
        row = reference_panel[symbol].loc[date]
        ret5 = scalar(row, "ret5")
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        industry = leaders[symbol].industry if symbol in leaders else INDUSTRY.get(symbol, "unknown")
        if math.isfinite(ret5):
            fast_returns.append(ret5)
            sector_returns.setdefault(industry, []).append(ret5)
        if math.isfinite(close) and math.isfinite(ma20):
            below_ma20.append(close < ma20)
            sector_below20.setdefault(industry, []).append(close < ma20)
        if math.isfinite(close) and math.isfinite(ma60):
            above_ma60.append(close > ma60)
            sector_above60.setdefault(industry, []).append(close > ma60)
        if (
            symbol in leaders
            and leaders[symbol].mature
            and (
                not cfg.same_day_leader_pipeline_enabled
                or account.leader_tenure.get(symbol, 0) >= cfg.leader_tenure_days
            )
        ):
            leader_failures.append(ret5 < -0.06 or (math.isfinite(close) and close < ma20))
    declining_name = float(np.mean(np.array(fast_returns) < 0)) if fast_returns else 0.0
    below_name = float(np.mean(below_ma20)) if below_ma20 else 0.0
    declining_group = (
        float(np.mean([float(np.mean(values)) < 0.0 for values in sector_returns.values()]))
        if sector_returns
        else declining_name
    )
    below_group = (
        float(np.mean([float(np.mean(values)) for values in sector_below20.values()]))
        if sector_below20
        else below_name
    )
    name_weight = cfg.risk_breadth_name_weight
    declining = name_weight * declining_name + (1.0 - name_weight) * declining_group
    below = name_weight * below_name + (1.0 - name_weight) * below_group
    breadth60_name = float(np.mean(above_ma60)) if above_ma60 else 0.0
    breadth60_group = (
        float(np.mean([float(np.mean(values)) for values in sector_above60.values()]))
        if sector_above60
        else breadth60_name
    )
    breadth60 = name_weight * breadth60_name + (1.0 - name_weight) * breadth60_group
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
    if reference_context is not None:
        declining_name = reference_context.name_declining
        declining_group = reference_context.group_declining
        declining = reference_context.declining
        below_name = 1.0 - reference_context.name_breadth20
        below_group = 1.0 - reference_context.group_breadth20
        below = 1.0 - reference_context.breadth20
        breadth60_name = reference_context.name_breadth60
        breadth60_group = reference_context.group_breadth60
        breadth60 = reference_context.breadth60
        sector_stress = reference_context.sector_stress
        correlation = reference_context.median_correlation
    recent_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(10).std(ddof=0))
    normal_vol = float(tech.loc[:date, "close"].pct_change(fill_method=None).tail(60).std(ddof=0))
    vol_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
    leader_failure = float(np.mean(leader_failures)) if leader_failures else 0.0
    operating_dd, capital_dd = _portfolio_drawdowns(account, equity)
    tech_speed = min(scalar(tech.loc[date], "ret5", 0.0), scalar(tech.loc[date], "ret10", 0.0))
    broad_speed = min(scalar(broad.loc[date], "ret5", 0.0), scalar(broad.loc[date], "ret10", 0.0))

    breadth20 = 1.0 - below
    previous_breadth20 = account.risk_signal_state.get("breadth20", breadth20)
    previous_breadth60 = account.risk_signal_state.get("breadth60", breadth60)
    breadth_drop = min(
        1.0,
        max(0.0, previous_breadth20 - breadth20) / 0.15
        + 0.5 * max(0.0, previous_breadth60 - breadth60) / 0.15,
    )
    correlation_damage = (
        min(1.0, max(0.0, (correlation - 0.45) / 0.35)) if math.isfinite(correlation) else 0.0
    )
    volatility_damage = min(1.0, max(0.0, (vol_ratio - 1.0) / 1.25))
    transition_damage = min(
        1.0,
        0.22 * (1.0 - breadth20)
        + 0.18 * (1.0 - breadth60)
        + 0.16 * breadth_drop
        + 0.14 * leader_failure
        + 0.10 * correlation_damage
        + 0.10 * volatility_damage
        + 0.10 * sector_stress,
    )
    trend_health = min(
        1.0,
        max(
            0.0,
            0.32 * breadth20
            + 0.28 * breadth60
            + 0.16 * (1.0 - leader_failure)
            + 0.12 * (1.0 - correlation_damage)
            + 0.12 * (1.0 - volatility_damage),
        ),
    )
    account.risk_signal_state.update(
        {
            "breadth20": breadth20,
            "breadth60": breadth60,
            "leader_failure": leader_failure,
            "correlation": correlation if math.isfinite(correlation) else 0.0,
            "volatility_ratio": vol_ratio,
            "transition_damage": transition_damage,
            "trend_health": trend_health,
        }
    )
    choppy_context = account.opportunity in {
        "CHOPPY",
        "WEAK",
    }
    chronic_observed = bool(
        cfg.chronic_overlay_enabled and choppy_context and transition_damage >= cfg.transition_damage_freeze
    )
    account.chronic_streak = account.chronic_streak + 1 if chronic_observed else 0
    if account.chronic_streak >= cfg.chronic_confirm_days:
        account.chronic_level = (
            3
            if transition_damage >= 0.80
            else 2
            if transition_damage >= 0.68
            else max(1, account.chronic_level)
        )
        account.chronic_repair_streak = 0
    elif transition_damage <= cfg.transition_damage_repair:
        account.chronic_repair_streak += 1
        if account.chronic_repair_streak >= cfg.chronic_repair_days:
            account.chronic_level = 0
            account.chronic_repair_streak = 0
    else:
        account.chronic_repair_streak = 0

    reasons: list[str] = []
    indicator_state = {
        "sector_breadth_shock": average_fast <= cfg.risk_fast_return and declining >= cfg.risk_breadth,
        "below_ma20_structure": below >= cfg.risk_below_ma20,
        "multi_industry_sync": sector_stress >= 0.50,
        "correlation_shock": math.isfinite(correlation) and correlation >= cfg.risk_correlation,
        "volatility_shock": vol_ratio >= cfg.risk_volatility_ratio,
        "leader_failure": leader_failure >= 0.50,
        "index_velocity": tech_speed <= -0.055 or broad_speed <= -0.045,
    }
    reason_by_indicator = {
        "sector_breadth_shock": "sector breadth shock",
        "below_ma20_structure": "MA20 structural damage",
        "multi_industry_sync": "multi-industry synchronization",
        "correlation_shock": "correlation shock",
        "volatility_shock": "volatility shock",
        "leader_failure": "leader failure",
        "index_velocity": "index velocity shock",
        "live_book_damage": "live book structural damage",
        "capital_damage": "portfolio capital damage",
    }
    for indicator, active in indicator_state.items():
        if active:
            reasons.append(reason_by_indicator[indicator])
    family_votes = _evidence_family_votes(indicator_state)
    votes = (
        sum(family_votes.values())
        if cfg.evidence_family_voting_enabled
        else sum(indicator_state.values())
    )

    sector_guard = update_sector_guard(
        date=date,
        calendar=pd.DatetimeIndex(tech.index),
        panel=user_panel,
        account=account,
        leadership_divergence=(market_context["tech_ret120"] - market_context["broad_ret120"]),
        cfg=cfg,
    )
    if sector_guard.triggered:
        _reset_recovery_owner_rearm(account)
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_on",
                "shock_count": sector_guard.shock_count,
                "leadership_divergence": (market_context["tech_ret120"] - market_context["broad_ret120"]),
                "equal_weight_return": (
                    sector_guard.observation.equal_return if sector_guard.observation is not None else None
                ),
                "exposure_weighted_return": (
                    sector_guard.observation.weighted_return if sector_guard.observation is not None else None
                ),
            }
        )
    if sector_guard.recovered:
        account.risk_events.append(
            {
                "date": str(date.date()),
                "event": "sector_guard_off",
                "active_sessions": sector_guard.active_sessions,
            }
        )
    held_damage: list[bool] = []
    held_repair: list[bool] = []
    held_loss: list[bool] = []
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
            math.isfinite(close)
            and math.isfinite(ma20)
            and close < ma20
            and ret5 <= -0.05
        )
        held_loss.append(math.isfinite(close) and close < position.avg_cost)
        held_repair.append(math.isfinite(ret1) and ret1 > 0)
    held_damage_ratio = float(np.mean(held_damage)) if held_damage else 0.0
    held_loss_ratio = float(np.mean(held_loss)) if held_loss else 0.0
    held_repair_ratio = float(np.mean(held_repair)) if held_repair else 0.0
    indicator_state.update(
        live_book_damage=(
            sector_guard.active
            or held_damage_ratio >= cfg.concentrated_break_ratio
        ),
        capital_damage=(
            capital_dd >= cfg.capital_budget_level2_dd
            or (operating_dd >= cfg.operating_dd_caution and held_damage_ratio > 0.0)
        ),
    )
    reasons.extend(
        reason_by_indicator[indicator]
        for indicator in ("live_book_damage", "capital_damage")
        if indicator_state[indicator]
    )
    family_votes = _evidence_family_votes(indicator_state)
    votes = (
        sum(family_votes.values())
        if cfg.evidence_family_voting_enabled
        else sum(
            indicator_state[name]
            for name in (
                "sector_breadth_shock",
                "below_ma20_structure",
                "multi_industry_sync",
                "correlation_shock",
                "volatility_shock",
                "leader_failure",
                "index_velocity",
            )
        )
    )
    anchor_symbols = _update_dynamic_anchors(
        leaders=leaders,
        account=account,
        cfg=cfg,
        allow_reanchor=(
            account.risk == Risk.NORMAL.value
            and transition_damage <= cfg.transition_damage_repair
            and votes <= 1
        ),
    )
    anchor_damage: list[bool] = []
    anchor_ret5: list[float] = []
    for symbol in anchor_symbols:
        frame = reference_panel.get(symbol)
        if frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        anchor_ret5.append(ret5)
        anchor_damage.append(math.isfinite(close) and math.isfinite(ma20) and close < ma20 and ret5 <= -0.06)
    anchor_groups = {leaders[symbol].industry for symbol in anchor_symbols if symbol in leaders}
    complete_anchor_basket = (
        len(anchor_damage) == cfg.risk_anchor_count and len(anchor_groups) >= cfg.risk_anchor_min_groups
    )
    reference_anchor_healthy = complete_anchor_basket and all(
        scalar(reference_panel[symbol].loc[date], "close")
        > scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_medium}")
        and scalar(
            reference_panel[symbol].loc[date],
            f"ret{cfg.trend_medium}",
            -1.0,
        )
        > 0
        for symbol in anchor_symbols
    )
    if reference_anchor_healthy:
        account.risk_streaks["reference_anchor_armed"] = 1
    reference_anchor_armed = account.risk_streaks.get("reference_anchor_armed", 0) == 1
    reference_anchor_break = complete_anchor_basket and all(anchor_damage)
    anchor_break_key = "reference_anchor_break"
    account.risk_streaks[anchor_break_key] = (
        account.risk_streaks.get(anchor_break_key, 0) + 1 if reference_anchor_break else 0
    )
    immediate_reference_break = bool(
        reference_anchor_armed
        and complete_anchor_basket
        and all(
            scalar(reference_panel[symbol].loc[date], "close")
            < scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_fast}")
            for symbol in anchor_symbols
        )
        and float(np.mean(anchor_ret5)) <= cfg.severe_shock_ret5
    )
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
        and (
            votes >= 3
            or (bool(held_ret5) and float(np.mean(held_ret5)) <= -0.08)
        )
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
        account.risk_streaks.get(market_backed_break_key, 0) + 1
        if market_backed_partial_cohort_damage
        else 0
    )
    market_backed_partial_cohort_break = bool(
        account.risk_streaks[market_backed_break_key]
        >= cfg.concentrated_break_confirm_days
    )
    held_cohort_break_confirmed = bool(
        synchronized_held_cohort_break or market_backed_partial_cohort_break
    )
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
        or len(set(account.anchor_weights) | live_recovery_members)
        >= min(3, cfg.max_positions)
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
                and account.candidate_tenure.get("post_shock_restore_complete", 0)
                == 0
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
    capital_drawdown_relapse = bool(
        capital_impaired_restoration_relapse
        or market_backed_restoration_relapse
    )
    concentrated_confirmed = (
        account_break_confirmed
        or reference_anchor_confirmed
        or held_cohort_break_confirmed
        or incomplete_universe_tail_break
        or (shock_rearmed and strategic_tail_break and reference_anchor_confirmed)
        or capital_drawdown_relapse
    )

    independent_damage = bool(
        sector_guard.active
        or (
            held_damage_ratio >= cfg.concentrated_break_ratio
            and transition_damage >= cfg.transition_damage_freeze
            and votes >= 2
        )
        or (
            reference_anchor_break
            and held_damage_ratio >= cfg.concentrated_break_ratio
            and transition_damage >= cfg.transition_damage_freeze
            and votes >= 4
        )
    )
    worsening_damage = bool(
        independent_damage
        and (
            votes >= 3
            or transition_damage >= 0.68
            or held_damage_ratio >= cfg.concentrated_break_ratio
        )
    )
    observed_budget_level = 0
    if cfg.capital_budget_ladder_enabled:
        if (
            capital_dd >= cfg.capital_dd_crisis
            and worsening_damage
            and votes >= 4
            and sector_stress >= 0.50
            and transition_damage >= 0.68
        ):
            observed_budget_level = 4
        elif (
            capital_dd >= cfg.capital_budget_level3_dd
            and worsening_damage
            and votes >= 4
            and transition_damage >= cfg.transition_damage_freeze
        ):
            observed_budget_level = 3
        elif capital_dd >= cfg.capital_budget_level2_dd and independent_damage:
            observed_budget_level = 2
        elif max(capital_dd, operating_dd) >= cfg.operating_dd_caution and (
            votes >= 2 or (votes >= 1 and held_damage_ratio > 0)
        ):
            observed_budget_level = 1
        cohort_grace_days = (
            cfg.capital_budget_emerging_cohort_grace_days
            if account.strategic_candidate_signature.startswith(
                "strategic_qualification:EMERGING_SECULAR:"
            )
            else cfg.capital_budget_new_cohort_grace_days
        )
        young_strategic_cohort = bool(
            strategic_active
            and _strategic_grace_supported(
                account=account,
            )
            and account.strategic_candidate_signature.startswith(
                (
                    "strategic_qualification:SECULAR:",
                    "strategic_qualification:EMERGING_SECULAR:",
                )
            )
            and account.candidate_tenure.get("strategic_cohort_days", 0)
            < cohort_grace_days
        )
        young_cohort_systemic_break = bool(
            votes >= 5
            and sector_stress >= 0.50
            and transition_damage >= 0.80
        )
        if (
            young_strategic_cohort
            and not young_cohort_systemic_break
        ):
            # Early cohort volatility is already owned by the strategic damage
            # guard and independent market/live-book families. Do not let the
            # same immature high-water mark manufacture a second cap authority,
            # regardless of unrelated universe size.
            observed_budget_level = 0
    strategic_damage_guard_triggered = _strategic_damage_guard_required(
        account=account,
        operating_drawdown=operating_dd,
        transition_damage=transition_damage,
        votes=votes,
        cfg=cfg,
    )
    persistent_strategic_damage_guard = bool(
        strategic_damage_guard_triggered
        and _strategic_damage_guard_persists(account, cfg)
    )
    if strategic_damage_guard_triggered and account.strategic_epoch > 0:
        if persistent_strategic_damage_guard:
            account.candidate_tenure[
                "strategic_damage_guard_active_epoch"
            ] = account.strategic_epoch
        else:
            # A diversified cohort needs one sparse de-risking observation,
            # not a persistent aggregate cap that repeatedly forces healthy
            # members out.  Record the one-shot owner for this epoch while a
            # concentrated handoff keeps the durable guard lifecycle above.
            account.candidate_tenure[
                "strategic_damage_trim_epoch"
            ] = account.strategic_epoch
    strategic_damage_guard = bool(
        strategic_damage_guard_triggered
        or _strategic_damage_guard_active(account)
    )
    _update_capital_budget_ladder(
        account,
        observed_level=observed_budget_level,
        repair_confirmed=(
            transition_damage <= cfg.transition_damage_repair
            and votes <= 1
            and held_damage_ratio < 0.50
            and _capital_budget_repair_drawdown_confirmed(
                level=account.capital_budget_level,
                capital_drawdown=capital_dd,
                operating_drawdown=operating_dd,
                cfg=cfg,
            )
        ),
        repair_days=cfg.capital_budget_repair_days,
    )
    strategic_guard_level2_overlay = _strategic_guard_level2_overlay_required(
        account
    )
    if strategic_guard_level2_overlay:
        account.candidate_tenure[
            "strategic_guard_level2_epoch"
        ] = account.strategic_epoch
    freeze_new_risk = bool(
        strategic_damage_guard
        or account.capital_budget_level >= 1
        or account.chronic_level >= 1
    )
    overlay_cap = cfg.max_gross
    if account.capital_budget_level >= 4:
        overlay_cap = min(overlay_cap, cfg.market_crisis_gross)
    elif account.capital_budget_level >= 3:
        overlay_cap = min(overlay_cap, cfg.capital_budget_level3_cap)
    elif account.capital_budget_level >= 2:
        overlay_cap = min(overlay_cap, cfg.capital_budget_level2_cap)
        if strategic_guard_level2_overlay:
            overlay_cap = min(overlay_cap, cfg.strategic_guard_level2_cap)
    if strategic_damage_guard:
        overlay_cap = min(overlay_cap, cfg.strategic_damage_guard_gross)
    if account.chronic_level >= 3:
        overlay_cap = min(overlay_cap, cfg.chronic_severe_cap)
    elif account.chronic_level >= 2:
        overlay_cap = min(overlay_cap, cfg.chronic_moderate_cap)
    overlay_reduction_level = (
        3
        if account.capital_budget_level >= 4
        else 2
        if overlay_cap < cfg.max_gross - 1e-12
        else 1
        if freeze_new_risk
        else 0
    )
    continuous_evidence = {
        "breadth20": breadth20,
        "breadth60": breadth60,
        "name_weighted_declining_ratio": declining_name,
        "group_balanced_declining_ratio": declining_group,
        "name_weighted_below_ma20_ratio": below_name,
        "group_balanced_below_ma20_ratio": below_group,
        "transition_damage": transition_damage,
        "trend_health": trend_health,
        "freeze_new_risk": freeze_new_risk,
        "chronic_level": account.chronic_level,
        "capital_budget_level": account.capital_budget_level,
        "independent_damage": independent_damage,
        "strategic_damage_guard": strategic_damage_guard,
        "strategic_guard_level2_overlay": strategic_guard_level2_overlay,
        "risk_anchor_symbols": list(anchor_symbols),
        "risk_anchor_signature": account.risk_anchor_signature,
        "risk_anchor_group_count": len(anchor_groups),
        "evidence_families": EVIDENCE_FAMILY_MEMBERS,
        "family_votes": family_votes,
        "family_vote_count": votes,
        **(reference_context.evidence() if reference_context is not None else {}),
    }

    previous = Risk(account.risk)
    live_symbols = {
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0
    }
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
            and scalar(user_panel[symbol].loc[date], "ret120", -1.0)
            >= market_context["tech_ret120"]
            for symbol in live_symbols
        )
    )
    acute_sector_evacuation = bool(
        _acute_sector_evacuation_required(
            sector_guard,
            cfg,
            leadership_divergence=(
                market_context["tech_ret120"] - market_context["broad_ret120"]
            ),
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
                symbol
                for symbol, position in account.positions.items()
                if position.shares > 0
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
        account.candidate_tenure["acute_sector_evacuation"] = 1
        evacuation_state = (
            Risk.CRISIS
            if previous is Risk.CRISIS or concentrated_confirmed
            else Risk.RISK_OFF
        )
        if evacuation_state is Risk.CRISIS and previous is not Risk.CRISIS:
            # Acute evacuation is a hard cap overlay, not a new owner of an
            # already-established crisis route. Preserve calibrated states
            # such as unbacked incomplete-universe and cohort-break cooldowns.
            severe_held_move = bool(held_ret5) and (
                float(np.mean(held_ret5)) <= cfg.severe_shock_ret5
            )
            account.shock_severity = (
                "SEVERE"
                if severe_held_move and votes >= 4
                else "CONCENTRATED"
            )
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
                "sector_guard_equal_return": (
                    observation.equal_return if observation is not None else None
                ),
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
                desired - protected_current.get(symbol, 0.0) + 1e-12
                < protected_trade_threshold[symbol]
                or (
                    protected_current.get(symbol, 0.0) >= 0.95 * desired
                    and desired - protected_current.get(symbol, 0.0)
                    < protected_completion_tolerance
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
                    reserve_backed=bool(
                        credible_reserve
                        and account.anchor_weights
                        and not strategic_active
                    ),
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
        if (
            capital_impaired_restoration_relapse
            or terminal_market_backed_restoration_relapse
        ):
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
                reserve_backed=bool(
                    credible_reserve
                    and account.anchor_weights
                    and not strategic_active
                ),
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
                "route": "sector_guard" if sector_guard_forced else "risk_state",
            }
        )
    account.risk = state.value
    account.shock_state = shock
    crisis_cap = _persistent_crisis_cap(
        account.shock_severity,
        cfg,
        reserve_backed=bool(
            credible_reserve
            and account.anchor_weights
            and not strategic_active
        ),
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
    return RiskAssessment(
        state=state,
        target_gross_cap=cap,
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
            "sector_guard_active": sector_guard.active,
            "acute_sector_evacuation": acute_sector_evacuation,
            "sector_guard_shock_count": sector_guard.shock_count,
            "sector_guard_active_sessions": sector_guard.active_sessions,
            "sector_guard_equal_return": (observation.equal_return if observation is not None else None),
            "sector_guard_weighted_return": (
                observation.weighted_return if observation is not None else None
            ),
            "sector_guard_negative_exposure": (
                observation.negative_exposure if observation is not None else None
            ),
            "sector_guard_positive_breadth": (
                observation.positive_breadth if observation is not None else None
            ),
        },
        reasons=tuple(reasons),
        shock_state=shock,
        freeze_new_risk=freeze_new_risk or state is not Risk.NORMAL,
        reduction_level=max(
            overlay_reduction_level,
            3 if state is Risk.CRISIS else 2 if state is Risk.RISK_OFF else 1 if state is Risk.CAUTION else 0,
        ),
        severity=account.shock_severity,
    )


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
    reference_context: ReferenceContext | None = None,
    configured_universe_size: int | None = None,
    sentinel_assessment: SentinelAssessment | None = None,
    sentinel_history: tuple[SentinelAssessment, ...] = (),
    sentinel_opportunity: Opportunity | str | None = None,
) -> RiskAssessment:
    """Return formal uquant risk with the optional freeze-only Sentinel overlay.

    The base assessor remains the sole owner of state, severity and reductions.
    This public boundary alone may take the minimum of its cap and a Sentinel
    candidate, so Sentinel cannot mutate durable state or create a parallel
    risk transition.
    """

    base = _assess_base_risk(
        date=date,
        broad=broad,
        tech=tech,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        reference_context=reference_context,
        configured_universe_size=configured_universe_size,
    )
    hysteresis = (
        apply_causal_hysteresis(
            sentinel_history,
            as_of=str(date.date()),
            confirm_days=cfg.risk_sentinel_confirm_days,
            repair_days=cfg.risk_sentinel_repair_days,
            severe_direct=cfg.risk_sentinel_severe_direct_enabled,
            min_confidence=cfg.risk_sentinel_min_confidence,
        )
        if cfg.risk_sentinel_mode == "LIMITED_GROSS_CAP" and sentinel_history
        else None
    )
    integrated = integrate_freeze_only(
        base=base,
        sentinel=sentinel_assessment,
        cfg=cfg,
        opportunity=sentinel_opportunity,
        hysteresis=hysteresis,
    )
    effective_level = (
        hysteresis.effective_level
        if hysteresis is not None
        else SentinelLevel.NORMAL
    )
    sentinel_cap = (
        sentinel_cap_for_level(effective_level, cfg)
        if cfg.risk_sentinel_mode == "LIMITED_GROSS_CAP"
        else None
    )
    final_cap = min(
        base.target_gross_cap,
        sentinel_cap if sentinel_cap is not None else 1.0,
    )
    cap_binding = bool(
        sentinel_cap is not None
        and final_cap < base.target_gross_cap - 1e-12
    )
    return replace(
        integrated,
        evidence={
            **integrated.evidence,
            "base_target_gross_cap": base.target_gross_cap,
            "sentinel_cap": sentinel_cap,
            "sentinel_cap_binding": cap_binding,
        },
        target_gross_cap=final_cap,
        freeze_new_risk=(
            integrated.freeze_new_risk or sentinel_cap is not None
        ),
    )
