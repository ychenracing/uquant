"""Protected-book repair and crisis recovery stages."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..holding_history import protected_weights_for_current_episode
from ..risk_sector import SectorGuardTransition
from ..types import AccountState, LeaderScore, Risk, RiskAssessment


def capture_protected_holdings(
    *, account: AccountState, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
    equity: float, use_anchors: bool = True,
) -> None:
    """Retain valid old rights and snapshot every current holding before a new cut."""
    retained = (
        {} if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1
        else protected_weights_for_current_episode(account)
    )
    if use_anchors and not retained:
        retained = dict(account.anchor_weights)
    for symbol, position in account.positions.items():
        if symbol in user_panel and date in user_panel[symbol].index and position.shares > 0:
            retained.setdefault(symbol, position.shares * scalar(user_panel[symbol].loc[date], "close") / equity)
    account.protected_weights = retained
    account.candidate_tenure["post_shock_restore_complete"] = 0


def persistent_crisis_cap(
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
        return cfg.risk_off_gross if reserve_backed else cfg.concentrated_crisis_gross
    if severity in {"SEVERE", "ANCHOR_BREAK"}:
        return cfg.severe_crisis_gross
    if severity == "CONCENTRATED":
        return cfg.concentrated_crisis_gross
    return cfg.market_crisis_gross


@dataclass(slots=True)
class _ProtectedRecoveryContext:
    date: pd.Timestamp
    broad: pd.DataFrame
    tech: pd.DataFrame
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    equity: float
    cfg: SystemConfig
    previous: Risk
    votes: int
    continuous_evidence: dict[str, object]
    market_context: dict[str, float]
    average_fast: float
    declining: float
    below: float
    sector_stress: float
    correlation: float
    vol_ratio: float
    leader_failure: float
    held_damage_ratio: float
    held_repair_ratio: float
    tech_speed: float
    broad_speed: float
    operating_dd: float
    capital_dd: float
    credible_reserve: bool
    freeze_new_risk: bool
    overlay_cap: float
    overlay_reduction_level: int
    sector_guard: SectorGuardTransition
    shock_rearmed: bool
    strategic_active: bool


def _recovery_evidence(
    ctx: _ProtectedRecoveryContext,
    *,
    held_repair_ratio: float,
    repair_details: dict[str, float | int] | None = None,
) -> dict[str, object]:
    return {
        **ctx.continuous_evidence,
        **ctx.market_context,
        "ai_fast_return": ctx.average_fast,
        "declining_ratio": ctx.declining,
        "below_ma20_ratio": ctx.below,
        "sector_stress_ratio": ctx.sector_stress,
        "median_correlation": ctx.correlation,
        "volatility_ratio": ctx.vol_ratio,
        "leader_failure_ratio": ctx.leader_failure,
        "held_damage_ratio": ctx.held_damage_ratio,
        "held_repair_ratio": held_repair_ratio,
        **({} if repair_details is None else repair_details),
        "tech_speed": ctx.tech_speed,
        "broad_speed": ctx.broad_speed,
        "operating_drawdown": ctx.operating_dd,
        "capital_drawdown": ctx.capital_dd,
    }


def _protected_structure_ratio(ctx: _ProtectedRecoveryContext) -> float:
    structures: list[bool] = []
    for symbol in protected_weights_for_current_episode(ctx.account):
        frame = ctx.user_panel.get(symbol)
        if frame is None or ctx.date not in frame.index:
            structures.append(False)
            continue
        row = frame.loc[ctx.date]
        structures.append(
            scalar(row, "close") > scalar(row, f"ma{ctx.cfg.trend_fast}")
            and scalar(row, f"ret{ctx.cfg.trend_fast}", 0.0) > 0
        )
    return float(np.mean(structures)) if structures else 0.0


def _protected_book_restored(
    ctx: _ProtectedRecoveryContext,
    *,
    target_gross: float,
    desired: dict[str, float],
    current: dict[str, float],
    pending_buys: set[str],
    threshold: dict[str, float],
) -> bool:
    return bool(
        ctx.account.candidate_tenure.get("post_shock_restore_complete", 0) == 1
        or target_gross <= 1e-12
        or (
            not pending_buys
            and all(
                wanted - current.get(symbol, 0.0) + 1e-12 < threshold[symbol]
                or (
                    current.get(symbol, 0.0) >= 0.95 * wanted
                    and wanted - current.get(symbol, 0.0) < ctx.cfg.min_trade_weight
                )
                for symbol, wanted in desired.items()
                if wanted > 1e-12
            )
        )
    )


def _protected_restoration_state(ctx: _ProtectedRecoveryContext) -> tuple[float, bool]:
    account = ctx.account
    cfg = ctx.cfg
    normalize_key = "protected_structure_normalization"
    account.risk_streaks[normalize_key] = (
        account.risk_streaks.get(normalize_key, 0) + 1 if _protected_structure_ratio(ctx) >= 0.67 else 0
    )
    protected_targets = {
        symbol: min(cfg.max_symbol_weight, max(0.0, weight))
        for symbol, weight in protected_weights_for_current_episode(account).items()
        if symbol in ctx.user_panel
    }
    target_gross = sum(protected_targets.values())
    full_cap = min(target_gross, cfg.max_gross)
    scale = min(1.0, full_cap / target_gross) if target_gross > 1e-12 else 0.0
    desired = {symbol: weight * scale for symbol, weight in protected_targets.items()}
    current = {
        symbol: position.shares * scalar(ctx.user_panel[symbol].loc[ctx.date], "close") / ctx.equity
        for symbol, position in account.positions.items()
        if symbol in desired and ctx.date in ctx.user_panel[symbol].index and position.shares > 0
    }
    pending_buys = {
        order.symbol for order in account.pending_orders if order.side == "BUY" and order.symbol in desired
    }
    threshold = {
        symbol: (
            cfg.protected_restore_min_trade_weight
            if wanted >= cfg.core_admission_weight
            else cfg.restoration_min_trade_weight
        )
        for symbol, wanted in desired.items()
    }
    restored = _protected_book_restored(
        ctx,
        target_gross=target_gross,
        desired=desired,
        current=current,
        pending_buys=pending_buys,
        threshold=threshold,
    )
    return full_cap, restored


def _normalize_protected_book(ctx: _ProtectedRecoveryContext) -> None:
    account = ctx.account
    cfg = ctx.cfg
    normalize_key = "protected_structure_normalization"
    full_cap, restored = _protected_restoration_state(ctx)
    if (
        protected_weights_for_current_episode(account)
        and ctx.previous is not Risk.CRISIS
        and account.positions
        and account.capital_budget_level == 0
        and account.chronic_level == 0
        and ctx.overlay_cap >= full_cap - 1e-12
        and restored
        and (ctx.capital_dd <= 1e-12 or account.risk_streaks[normalize_key] >= cfg.recovery_risk_confirm_days)
    ):
        account.protected_weights.clear()
        account.candidate_tenure["post_shock_restore_complete"] = 0
        account.shock_start_date = ""
        account.shock_severity = "NORMAL"
        account.shock_state = "NONE"


def _unbacked_cooldown_assessment(ctx: _ProtectedRecoveryContext) -> RiskAssessment:
    ctx.account.risk = Risk.CRISIS.value
    ctx.account.shock_state = "UNBACKED_COOLDOWN"
    return RiskAssessment(
        state=Risk.CRISIS,
        target_gross_cap=0.0,
        votes=ctx.votes,
        evidence=_recovery_evidence(ctx, held_repair_ratio=ctx.held_repair_ratio),
        reasons=("unbacked universe remains in capital cooldown",),
        shock_state="UNBACKED_COOLDOWN",
        freeze_new_risk=True,
        reduction_level=3,
        severity=ctx.account.shock_severity,
    )


@dataclass(frozen=True, slots=True)
class _MarketRepairState:
    repair_leaders: int
    protected_fast_ratio: float
    protected_swing_ratio: float
    shock_wait_days: int
    fast_v_repair: bool
    standard_repair_ready: bool
    persistent_repair_ready: bool


def _repair_leader_count(ctx: _ProtectedRecoveryContext) -> int:
    count = 0
    for symbol, frame in ctx.user_panel.items():
        leader = ctx.leaders.get(symbol)
        if leader is None or not leader.mature or ctx.date not in frame.index:
            continue
        row = frame.loc[ctx.date]
        if (
            scalar(row, "close") > scalar(row, f"ma{ctx.cfg.trend_fast}")
            and scalar(row, f"ret{ctx.cfg.trend_fast}", 0.0) > 0
        ):
            count += 1
    return count


def _protected_repair_ratios(ctx: _ProtectedRecoveryContext) -> tuple[float, float]:
    fast_repairs: list[bool] = []
    swing_repairs: list[bool] = []
    for symbol in protected_weights_for_current_episode(ctx.account):
        frame = ctx.user_panel.get(symbol)
        if frame is None or ctx.date not in frame.index:
            fast_repairs.append(False)
            swing_repairs.append(False)
            continue
        row = frame.loc[ctx.date]
        returns1 = frame.loc[: ctx.date, "close"].pct_change(fill_method=None)
        fast_repairs.append(
            bool(len(returns1)) and math.isfinite(float(returns1.iloc[-1])) and float(returns1.iloc[-1]) > 0
        )
        swing_repairs.append(
            scalar(row, "ret5", -1.0) > 0 and scalar(row, "close") > scalar(row, f"ma{ctx.cfg.trend_fast}")
        )
    fast_ratio = float(np.mean(fast_repairs)) if fast_repairs else 0.0
    swing_ratio = float(np.mean(swing_repairs)) if swing_repairs else 0.0
    return fast_ratio, swing_ratio


def _v_market_repair(ctx: _ProtectedRecoveryContext) -> bool:
    return bool(
        ctx.average_fast >= ctx.cfg.fast_v_recovery_return
        and ctx.declining <= ctx.cfg.fast_v_recovery_breadth
        and ctx.below <= ctx.cfg.fast_v_recovery_below_ma20
        and (
            scalar(ctx.tech.loc[ctx.date], "ret5", 0.0) >= ctx.cfg.fast_v_recovery_index_return
            or scalar(ctx.broad.loc[ctx.date], "ret5", 0.0) >= ctx.cfg.fast_v_recovery_index_return
        )
    )


def _structural_independent_repair(ctx: _ProtectedRecoveryContext, *, repair_leaders: int) -> bool:
    return bool(
        not ctx.account.anchor_weights
        and (
            scalar(ctx.broad.loc[ctx.date], "close")
            > scalar(ctx.broad.loc[ctx.date], f"ma{ctx.cfg.trend_fast}")
            or scalar(ctx.tech.loc[ctx.date], "close")
            > scalar(ctx.tech.loc[ctx.date], f"ma{ctx.cfg.trend_fast}")
        )
        and ctx.declining <= 0.55
        and ctx.below <= 0.60
        and repair_leaders >= 2
    )


def _market_repair_state(ctx: _ProtectedRecoveryContext) -> _MarketRepairState:
    repair_leaders = _repair_leader_count(ctx)
    fast_ratio, swing_ratio = _protected_repair_ratios(ctx)
    shock_elapsed = 0
    if ctx.account.shock_start_date:
        shock_elapsed = len(ctx.tech.loc[pd.Timestamp(ctx.account.shock_start_date) : ctx.date]) - 1
    shock_wait_days = ctx.cfg.severe_shock_wait_days
    v_market_repair = _v_market_repair(ctx)
    fast_v_repair = shock_elapsed >= shock_wait_days and v_market_repair and fast_ratio >= 0.50
    persistent_v_repair = (
        shock_elapsed >= ctx.cfg.persistent_v_recovery_wait_days
        and len(protected_weights_for_current_episode(ctx.account)) == 1
        and v_market_repair
        and swing_ratio >= 1.0
        and not ctx.sector_guard.active
    )
    structural_repair = _structural_independent_repair(ctx, repair_leaders=repair_leaders)
    independent_repair = not ctx.sector_guard.active and (structural_repair or fast_v_repair)
    market_key = "independent_market_repair"
    ctx.account.risk_streaks[market_key] = (
        ctx.account.risk_streaks.get(market_key, 0) + 1 if independent_repair else 0
    )
    confirm_days = (
        ctx.cfg.fast_v_recovery_confirm_days if fast_v_repair else ctx.cfg.recovery_risk_confirm_days
    )
    standard_ready = ctx.account.risk_streaks[market_key] >= confirm_days
    persistent_key = "persistent_v_market_repair"
    ctx.account.risk_streaks[persistent_key] = (
        ctx.account.risk_streaks.get(persistent_key, 0) + 1 if persistent_v_repair else 0
    )
    persistent_ready = (
        ctx.account.risk_streaks[persistent_key] >= ctx.cfg.fast_v_recovery_confirm_days and not fast_v_repair
    )
    return _MarketRepairState(
        repair_leaders=repair_leaders,
        protected_fast_ratio=fast_ratio,
        protected_swing_ratio=swing_ratio,
        shock_wait_days=shock_wait_days,
        fast_v_repair=fast_v_repair,
        standard_repair_ready=standard_ready,
        persistent_repair_ready=persistent_ready,
    )


def _confirmed_market_repair(ctx: _ProtectedRecoveryContext, state: _MarketRepairState) -> RiskAssessment:
    persistent_confirmed = state.persistent_repair_ready and not state.standard_repair_ready
    expedited = state.fast_v_repair or persistent_confirmed
    ctx.account.risk = Risk.CAUTION.value
    ctx.account.operating_peak = ctx.equity
    ctx.account.candidate_tenure["fast_v_recovery"] = int(expedited)
    ctx.account.shock_state = "FAST_V_RECOVERY" if expedited else "ROTATION_RECOVERY"
    reason = (
        "confirmed persistent V-recovery after extended single-name protection"
        if persistent_confirmed
        else "confirmed fast V-recovery breadth and index impulse"
        if state.fast_v_repair
        else "independent market and replacement-leader repair"
    )
    ctx.account.risk_events.append(
        {
            "date": str(ctx.date.date()),
            "from": ctx.previous.value,
            "to": Risk.CAUTION.value,
            "votes": ctx.votes,
            "reasons": [reason],
        }
    )
    return RiskAssessment(
        state=Risk.CAUTION,
        target_gross_cap=min(
            ctx.cfg.max_gross,
            ctx.cfg.fast_v_recovery_gross if expedited else ctx.cfg.recovery_target_gross,
            ctx.overlay_cap,
        ),
        votes=ctx.votes,
        evidence=_recovery_evidence(
            ctx,
            held_repair_ratio=ctx.held_repair_ratio,
            repair_details={
                "protected_fast_repair_ratio": state.protected_fast_ratio,
                "protected_swing_repair_ratio": state.protected_swing_ratio,
                "replacement_leaders": state.repair_leaders,
            },
        ),
        reasons=(reason,),
        shock_state=("FAST_V_RECOVERY" if expedited else "ROTATION_RECOVERY"),
        freeze_new_risk=ctx.freeze_new_risk,
        reduction_level=max(1, ctx.overlay_reduction_level),
        severity=ctx.account.shock_severity,
    )


def _protected_daily_repair_ratio(ctx: _ProtectedRecoveryContext) -> float:
    repairs: list[bool] = []
    for symbol in protected_weights_for_current_episode(ctx.account):
        frame = ctx.user_panel.get(symbol)
        if frame is None or ctx.date not in frame.index:
            repairs.append(False)
            continue
        ret1 = float(frame.loc[: ctx.date, "close"].pct_change(fill_method=None).iloc[-1])
        repairs.append(math.isfinite(ret1) and ret1 > 0)
    return float(np.mean(repairs)) if repairs else 0.0


def _severe_wait_complete(ctx: _ProtectedRecoveryContext, *, shock_wait_days: int) -> bool:
    if ctx.account.shock_severity not in {"SEVERE", "CONCENTRATED"} or not ctx.account.shock_start_date:
        return True
    wait_complete = bool(
        len(ctx.tech.loc[pd.Timestamp(ctx.account.shock_start_date) : ctx.date]) - 1 >= shock_wait_days
    )
    structures: list[bool] = []
    for symbol in protected_weights_for_current_episode(ctx.account):
        frame = ctx.user_panel.get(symbol)
        if frame is None or ctx.date not in frame.index:
            structures.append(False)
            continue
        row = frame.loc[ctx.date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{ctx.cfg.trend_fast}")
        ret5 = scalar(row, "ret5", -1.0)
        structures.append(math.isfinite(close) and math.isfinite(ma20) and close > ma20 and ret5 > 0)
    return wait_complete and bool(structures) and float(np.mean(structures)) >= 0.67


def _concentrated_repair_ready(
    ctx: _ProtectedRecoveryContext, *, repair_ratio: float, shock_wait_days: int
) -> bool:
    wait_complete = _severe_wait_complete(ctx, shock_wait_days=shock_wait_days)
    repair_key = "concentrated_repair"
    ctx.account.risk_streaks[repair_key] = (
        ctx.account.risk_streaks.get(repair_key, 0) + 1
        if repair_ratio >= 0.67 and wait_complete and not ctx.sector_guard.active
        else 0
    )
    return ctx.account.risk_streaks[repair_key] >= ctx.cfg.concentrated_repair_days


def _confirmed_concentrated_repair(ctx: _ProtectedRecoveryContext, *, repair_ratio: float) -> RiskAssessment:
    ctx.account.operating_peak = ctx.equity
    recovery_gross = {
        "SEVERE": ctx.cfg.severe_recovery_gross,
        "CONCENTRATED": ctx.cfg.concentrated_recovery_gross,
    }.get(ctx.account.shock_severity, ctx.cfg.recovery_target_gross)
    ctx.account.risk = Risk.CAUTION.value
    ctx.account.shock_state = "RECOVERY"
    ctx.account.risk_events.append(
        {
            "date": str(ctx.date.date()),
            "from": ctx.previous.value,
            "to": Risk.CAUTION.value,
            "votes": ctx.votes,
            "reasons": ["two-day synchronized leader repair"],
        }
    )
    return RiskAssessment(
        state=Risk.CAUTION,
        target_gross_cap=min(min(ctx.cfg.max_gross, recovery_gross), ctx.overlay_cap),
        votes=ctx.votes,
        evidence=_recovery_evidence(ctx, held_repair_ratio=repair_ratio),
        reasons=("two-day synchronized leader repair",),
        shock_state="RECOVERY",
        freeze_new_risk=ctx.freeze_new_risk,
        reduction_level=max(1, ctx.overlay_reduction_level),
        severity=ctx.account.shock_severity,
    )


def _persistent_stress(ctx: _ProtectedRecoveryContext, *, repair_ratio: float) -> RiskAssessment:
    ctx.account.risk = Risk.CRISIS.value
    ctx.account.shock_state = "PERSISTENT_STRESS"
    return RiskAssessment(
        state=Risk.CRISIS,
        target_gross_cap=min(
            persistent_crisis_cap(
                ctx.account.shock_severity,
                ctx.cfg,
                reserve_backed=bool(
                    ctx.credible_reserve and ctx.account.anchor_weights and not ctx.strategic_active
                ),
            ),
            ctx.overlay_cap,
        ),
        votes=ctx.votes,
        evidence=_recovery_evidence(ctx, held_repair_ratio=repair_ratio),
        reasons=("awaiting synchronized repair confirmation",),
        shock_state="PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=3,
        severity=ctx.account.shock_severity,
    )


def assess_protected_recovery(
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

    ctx = _ProtectedRecoveryContext(
        date=date,
        broad=broad,
        tech=tech,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        previous=previous,
        votes=votes,
        continuous_evidence=continuous_evidence,
        market_context=market_context,
        average_fast=average_fast,
        declining=declining,
        below=below,
        sector_stress=sector_stress,
        correlation=correlation,
        vol_ratio=vol_ratio,
        leader_failure=leader_failure,
        held_damage_ratio=held_damage_ratio,
        held_repair_ratio=held_repair_ratio,
        tech_speed=tech_speed,
        broad_speed=broad_speed,
        operating_dd=operating_dd,
        capital_dd=capital_dd,
        credible_reserve=credible_reserve,
        freeze_new_risk=freeze_new_risk,
        overlay_cap=overlay_cap,
        overlay_reduction_level=overlay_reduction_level,
        sector_guard=sector_guard,
        shock_rearmed=shock_rearmed,
        strategic_active=strategic_active,
    )
    _normalize_protected_book(ctx)
    if previous is not Risk.CRISIS or not protected_weights_for_current_episode(account):
        return None
    if account.shock_severity == "INCOMPLETE_UNIVERSE_UNBACKED" and not shock_rearmed:
        return _unbacked_cooldown_assessment(ctx)
    market_repair = _market_repair_state(ctx)
    if market_repair.standard_repair_ready or market_repair.persistent_repair_ready:
        return _confirmed_market_repair(ctx, market_repair)
    repair_ratio = _protected_daily_repair_ratio(ctx)
    if _concentrated_repair_ready(
        ctx,
        repair_ratio=repair_ratio,
        shock_wait_days=market_repair.shock_wait_days,
    ):
        return _confirmed_concentrated_repair(ctx, repair_ratio=repair_ratio)
    return _persistent_stress(ctx, repair_ratio=repair_ratio)
