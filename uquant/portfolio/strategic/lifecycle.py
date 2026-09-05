"""Strategic-cohort lifecycle, restoration, and exit policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pandas as pd

from ...features import scalar
from ...models.strategic_epoch import (
    bind_account_strategic_ownership,
    settle_account_strategic_epoch,
)
from ...models.strategic_grant import StrategicQualificationObservation
from ...models.strategic_universe import StrategicUniverseRoles
from ...portfolio_core import strategic_dominant_symbol
from ...types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    RiskAssessment,
    Target,
)
from .discovery import (
    observe_strategic_candidates,
)
from .grant_lifecycle import revalidate_strategic_grant
from .qualification_candidates import reset_strategic_candidate_eligibility

if TYPE_CHECKING:
    from .discovery import StrategicPortfolioPolicy

from .rearm import strategic_cash_rearm_grant_open
from .targets import strategic_active_targets as _strategic_active_targets
from .targets import strategic_completed_exit_targets as _strategic_completed_exit_targets


@dataclass(slots=True)
class _StrategicLifecycleContext:
    policy: StrategicPortfolioPolicy
    date: pd.Timestamp
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    prices: dict[str, float]
    weights_now: dict[str, float]
    active_symbols: set[str]
    transition_impulse_epoch: bool
    damage_guard_active: bool
    damage_trim_active: bool
    damage_guard_owns_transition: bool
    dominant_symbol: str | None
    dominant_profit_locked: bool
    thresholds: tuple[float, ...]
    dominant_profit_lock_armed_now: bool = False


def _bounded_strategic_restore_risk_open(
    self: StrategicPortfolioPolicy,
    *,
    risk: RiskAssessment,
    account: AccountState,
) -> bool:
    """Permit only a saved cohort repair inside the explicit risk cap."""

    restoration_owned = bool(
        account.candidate_tenure.get("strategic_cohort_started", 0) == 1
        and bool(account.strategic_restore_weights)
    )
    if not restoration_owned:
        return False
    reason_clean_level2 = bool(
        risk.state.value == "NORMAL"
        and not risk.reasons
        and account.capital_budget_level == 2
        and account.chronic_level == 0
    )
    transition_damage = float(risk.evidence.get("transition_damage", 1.0))
    live_book_recovered = bool(
        max(
            float(risk.evidence.get("operating_drawdown", 1.0)),
            float(risk.evidence.get("capital_drawdown", 1.0)),
        )
        < self.cfg.strategic_damage_guard_dd
        and transition_damage < self.cfg.strategic_damage_guard_transition
    )
    repaired_caution = bool(
        risk.state.value == "CAUTION"
        and not bool(risk.evidence.get("freeze_new_risk", False))
        and risk.votes <= 1
        and (transition_damage <= self.cfg.transition_damage_repair or live_book_recovered)
    )
    return reason_clean_level2 or repaired_caution


def _retire_strategic_member(account: AccountState, symbol: str) -> None:
    """Remove every live intent owned by one completed cohort member."""
    reset_strategic_candidate_eligibility(account=account, symbol=symbol)
    account.strategic_cohort_targets.pop(symbol, None)
    account.strategic_exit_bands.pop(symbol, None)
    account.strategic_active_bands.pop(symbol, None)
    account.strategic_restore_weights.pop(symbol, None)
    account.protected_weights.pop(symbol, None)


def _complete_empty_strategic_cohort(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    held_cohort: set[str],
) -> tuple[Target, ...] | None:
    if held_cohort:
        return _strategic_completed_exit_targets(self=self, leaders=leaders, account=account)
    grant = account.strategic_grant
    epoch_id = (
        account.active_strategic_epoch_id
        or (grant.epoch_id if grant is not None else "")
    )
    if epoch_id and not settle_account_strategic_epoch(
        account,
        epoch_id=epoch_id,
        closed_session=str(date.date()),
        close_reason="owner_exit",
    ):
        return ()
    for symbol in account.strategic_cohort_symbols:
        account.protected_weights.pop(symbol, None)
    account.candidate_tenure["strategic_cohort_active"] = 0
    account.candidate_tenure["strategic_cohort_completed"] = 1
    account.candidate_tenure["strategic_cohort_started"] = 0
    account.candidate_tenure["strategic_profit_armed"] = 0
    account.candidate_tenure["strategic_tail_armed"] = 0
    account.candidate_tenure["strategic_dominant_epoch"] = 0
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = 0
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.strategic_epochs_completed += 1
    account.strategic_last_exit_date = str(date.date())
    account.strategic_rearm_date = ""
    account.strategic_previous_symbols = list(account.strategic_cohort_symbols)
    account.strategic_cohort_symbols.clear()
    account.strategic_candidate_signature = ""
    account.strategic_qualification = StrategicQualificationObservation()
    account.strategic_successor_qualification = StrategicQualificationObservation()
    for key in tuple(account.replacement_tenure):
        if key.startswith(("strategic_qualification:", "strategic_successor:")):
            account.replacement_tenure[key] = 0
    return None


def _strategic_lifecycle_context(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
) -> _StrategicLifecycleContext:
    account.candidate_tenure["strategic_cohort_days"] = (
        account.candidate_tenure.get("strategic_cohort_days", 0) + 1
    )
    transition_impulse = bool(
        account.strategic_candidate_signature.startswith("strategic_qualification:transition_impulse:")
        or ":evidence=transition_impulse" in account.strategic_candidate_signature
    )
    damage_guard = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_damage_guard_active_epoch", -1) == account.strategic_epoch
        and account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1)
        != account.strategic_epoch
    )
    damage_trim = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_damage_trim_epoch", -1) == account.strategic_epoch
        and account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1)
        != account.strategic_epoch
        and bool(account.strategic_restore_weights)
    )
    dominant = strategic_dominant_symbol(account)
    dominant_locked = bool(
        dominant is not None
        and account.candidate_tenure.get("strategic_dominant_profit_lock_epoch", -1)
        == account.strategic_epoch
    )
    active_symbols = set(account.strategic_cohort_targets)
    if active_symbols and all(
        weights_now.get(symbol, 0.0) >= 0.95 * account.strategic_cohort_targets.get(symbol, 0.0)
        for symbol in active_symbols
    ):
        account.candidate_tenure["strategic_cohort_started"] = 1
    band_count = self.cfg.strategic_cohort_trail_bands
    thresholds = tuple(
        self.cfg.strategic_cohort_trail_atr
        + (index - (band_count - 1) / 2.0) * self.cfg.strategic_cohort_trail_spacing
        for index in range(band_count)
    )
    return _StrategicLifecycleContext(
        self,
        date,
        risk,
        user_panel,
        leaders,
        account,
        prices,
        weights_now,
        active_symbols,
        transition_impulse,
        damage_guard,
        damage_trim,
        bool(damage_guard or damage_trim or risk.evidence.get("strategic_damage_guard", False)),
        dominant,
        dominant_locked,
        thresholds,
    )


def _missing_strategic_member(ctx: _StrategicLifecycleContext, symbol: str) -> bool:
    account = ctx.account
    position = account.positions.get(symbol)
    if position is not None and position.shares > 0:
        return False
    if (
        (
            ctx.transition_impulse_epoch
            and account.candidate_tenure.get("strategic_cohort_started", 0) == 1
            and (symbol in account.strategic_restore_weights or symbol in account.protected_weights)
        )
        or symbol in account.strategic_exit_bands
        or (
            account.candidate_tenure.get("strategic_cohort_started", 0) == 1
            and symbol not in account.strategic_restore_weights
            and symbol not in account.protected_weights
            and not any(order.symbol == symbol and order.side == "BUY" for order in account.pending_orders)
        )
    ):
        ctx.policy._retire_strategic_member(account, symbol)
    return True


def _arm_dominant_profit_lock(
    ctx: _StrategicLifecycleContext,
    *,
    symbol: str,
    peak_mfe: float,
) -> None:
    if not (
        symbol == ctx.dominant_symbol
        and not ctx.dominant_profit_locked
        and peak_mfe >= ctx.policy.cfg.strategic_dominant_profit_lock_mfe
    ):
        return
    account = ctx.account
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = account.strategic_epoch
    account.strategic_cohort_targets[symbol] = min(
        account.strategic_cohort_targets[symbol],
        ctx.policy.cfg.strategic_dominant_retained_gross,
    )
    account.strategic_restore_weights.pop(symbol, None)
    account.protected_weights.pop(symbol, None)
    ctx.dominant_profit_locked = True
    ctx.dominant_profit_lock_armed_now = True


def _bounded_strategic_exit_scale(
    ctx: _StrategicLifecycleContext,
    *,
    symbol: str,
) -> float:
    """Keep a staged owner's exit duration invariant to its bounded weight."""

    grant = ctx.account.strategic_grant
    if grant is None:
        return 1.0
    epoch = next(
        (
            item
            for item in ctx.account.strategic_epochs
            if item.epoch_id == grant.epoch_id
            and item.grant_id == grant.grant_id
            and item.owner_symbol == symbol
            and not item.terminal
        ),
        None,
    )
    if epoch is None or not (
        grant.authorization_id
        or epoch.qualification_quorum in {"STRONG_PAIR", "ABSOLUTE_SINGLE"}
    ):
        return 1.0
    if epoch.target_weight <= 0.0 or epoch.full_weight <= 0.0:
        return 1.0
    return min(1.0, epoch.target_weight / epoch.full_weight)


def _strategic_exit_step(
    ctx: _StrategicLifecycleContext,
    *,
    symbol: str,
    row: pd.Series,
) -> float:
    cfg = ctx.policy.cfg
    gradual = bool(
        scalar(row, "ret5", -math.inf) > cfg.tactical_rebound_oversold_max_ret5
        and scalar(row, "ret20", math.inf) <= cfg.tactical_rebound_breadth_max_ret20
        and scalar(row, "ret60", math.inf) <= 0.0
        and scalar(row, "ret120", -math.inf) >= cfg.strategic_secular_min_score
    )
    repaired_step = (
        cfg.strategic_gradual_post_guard_exit_step if gradual else cfg.strategic_post_guard_exit_step
    )
    post_guard_repair = bool(
        ctx.account.strategic_epoch > 0
        and ctx.account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1)
        == ctx.account.strategic_epoch
        and ctx.account.candidate_tenure.get("strategic_guard_level2_epoch", -1)
        != ctx.account.strategic_epoch
    )
    if not post_guard_repair:
        return (
            cfg.strategic_cohort_exit_step
            * _bounded_strategic_exit_scale(ctx, symbol=symbol)
        )
    return max(cfg.strategic_cohort_exit_step, repaired_step)


def _advance_strategic_exit(
    ctx: _StrategicLifecycleContext,
    *,
    symbol: str,
    row: pd.Series,
    triggered: list[bool],
) -> None:
    account = ctx.account
    account.strategic_restore_weights.pop(symbol, None)
    account.protected_weights.pop(symbol, None)
    bands = account.strategic_exit_bands[symbol]
    armed = account.strategic_active_bands[symbol]
    band_count = ctx.policy.cfg.strategic_cohort_trail_bands
    if ctx.transition_impulse_epoch and all(triggered):
        armed[:] = [True] * band_count
        bands[:] = [0.0] * band_count
    else:
        for index, signal in enumerate(triggered):
            if signal:
                armed[index] = True
                bands[index] = max(
                    0.0,
                    bands[index]
                    - _strategic_exit_step(ctx, symbol=symbol, row=row) / band_count,
                )
    if sum(bands) <= 1e-12:
        reset_strategic_candidate_eligibility(account=account, symbol=symbol)
        account.strategic_cohort_targets.pop(symbol, None)


def _evaluate_strategic_member(ctx: _StrategicLifecycleContext, symbol: str) -> None:
    if _missing_strategic_member(ctx, symbol):
        return
    self = ctx.policy
    position = ctx.account.positions[symbol]
    frame = ctx.user_panel.get(symbol)
    if frame is None or ctx.date not in frame.index:
        return
    row = cast(pd.Series, frame.loc[ctx.date])
    close = scalar(row, "close")
    core_costs = [
        tranche.avg_cost
        for tranche in position.tranches
        if tranche.shares > 0 and tranche.lifecycle == Lifecycle.CORE.value
    ]
    strategic_cost = min(core_costs) if core_costs else position.avg_cost
    pnl = close / max(strategic_cost, 1e-12) - 1.0
    if pnl <= self.cfg.strategic_cohort_disaster_stop:
        self._retire_strategic_member(ctx.account, symbol)
        return
    atr = scalar(row, "atr", math.inf)
    structural_damage = (
        close < scalar(row, f"ma{self.cfg.trend_fast}") and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
    )
    peak_mfe = position.highest_close / max(strategic_cost, 1e-12) - 1.0
    _arm_dominant_profit_lock(ctx, symbol=symbol, peak_mfe=peak_mfe)
    triggered = [
        peak_mfe >= self.cfg.strategic_cohort_profit_arm
        and structural_damage
        and math.isfinite(atr)
        and close <= position.highest_close - threshold * atr
        for threshold in ctx.thresholds
    ]
    if (
        any(triggered)
        and symbol not in ctx.account.strategic_exit_bands
        and not ctx.damage_guard_owns_transition
        and not (symbol == ctx.dominant_symbol and ctx.dominant_profit_locked)
    ):
        current = ctx.weights_now.get(symbol, 0.0)
        band_count = self.cfg.strategic_cohort_trail_bands
        ctx.account.strategic_exit_bands[symbol] = [current / band_count] * band_count
        ctx.account.strategic_active_bands[symbol] = [False] * band_count
        ctx.account.strategic_restore_weights.pop(symbol, None)
        ctx.account.protected_weights.pop(symbol, None)
        ctx.account.candidate_tenure["strategic_profit_armed"] = 1
    if symbol in ctx.account.strategic_exit_bands:
        _advance_strategic_exit(ctx, symbol=symbol, row=row, triggered=triggered)


def _capture_strategic_restore(
    ctx: _StrategicLifecycleContext,
    *,
    active_symbols: set[str],
    current_selected: dict[str, float],
) -> None:
    self = ctx.policy
    current_gross = sum(current_selected.values())
    if ctx.risk.target_gross_cap + 0.02 >= current_gross:
        return
    saved = {
        symbol: min(self.cfg.max_symbol_weight, max(0.0, weight))
        for symbol, weight in ctx.account.strategic_restore_weights.items()
        if symbol in active_symbols and symbol not in ctx.account.strategic_exit_bands
    }
    saved_total = sum(saved.values())
    if saved_total > self.cfg.max_gross + 1e-12:
        saved = {symbol: weight * self.cfg.max_gross / saved_total for symbol, weight in saved.items()}
        saved_total = self.cfg.max_gross
    increments = {
        symbol: max(0.0, weight - saved.get(symbol, 0.0))
        for symbol, weight in current_selected.items()
        if symbol not in ctx.account.strategic_exit_bands
    }
    increment_total = sum(increments.values())
    headroom = max(0.0, self.cfg.max_gross - saved_total)
    increment_scale = min(1.0, headroom / increment_total) if increment_total > 0 else 0.0
    for symbol, increment in increments.items():
        saved[symbol] = saved.get(symbol, 0.0) + increment * increment_scale
    ctx.account.strategic_restore_weights = {
        symbol: weight for symbol, weight in saved.items() if weight > 1e-12
    }


def _strategic_restore_confirmed(ctx: _StrategicLifecycleContext) -> tuple[bool, bool]:
    self = ctx.policy
    risk = ctx.risk
    cash_rearm_open = strategic_cash_rearm_grant_open(
        account=ctx.account,
        risk=risk,
        cfg=self.cfg,
    )
    buy_risk_open = bool(
        cash_rearm_open
        or (
            not risk.freeze_new_risk
            and not risk.evidence.get("freeze_new_risk", False)
        )
    )
    bounded = self._bounded_strategic_restore_risk_open(risk=risk, account=ctx.account)
    guard_repaired = bool(
        not (ctx.damage_guard_active or ctx.damage_trim_active)
        or bounded
        or (
            risk.votes <= 1
            and float(risk.evidence.get("transition_damage", 0.0)) <= self.cfg.transition_damage_repair
        )
    )
    confirmed = bool(
        (buy_risk_open or bounded)
        and guard_repaired
        and (
            risk.state.value == "NORMAL"
            or (
                risk.state.value == "CAUTION"
                and risk.votes <= 2
                and (
                    bounded
                    or float(risk.evidence.get("transition_damage", 1.0)) <= self.cfg.transition_damage_repair
                )
            )
        )
    )
    return buy_risk_open, confirmed


def _settle_strategic_guard(ctx: _StrategicLifecycleContext) -> None:
    ctx.account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    ctx.account.candidate_tenure["strategic_damage_trim_epoch"] = 0
    ctx.account.candidate_tenure["strategic_damage_guard_complete_epoch"] = ctx.account.strategic_epoch


def _strategic_restore_complete(
    ctx: _StrategicLifecycleContext,
    *,
    saved_restore: dict[str, float],
    proposed: dict[str, float],
) -> bool:
    self = ctx.policy
    account = ctx.account
    equity = account.cash + sum(
        position.shares * ctx.prices.get(symbol, 0.0) for symbol, position in account.positions.items()
    )
    trade_threshold = max(
        self.cfg.restoration_min_trade_weight,
        self.cfg.min_trade_value / equity if equity > 1e-12 else math.inf,
    )
    completion_tolerance = max(
        self.cfg.min_trade_weight,
        self.cfg.min_trade_value / equity if equity > 1e-12 else math.inf,
    )
    material_pending = {
        order.symbol
        for order in account.pending_orders
        if order.side == "BUY"
        and order.symbol in proposed
        and ctx.weights_now.get(order.symbol, 0.0) < 0.95 * proposed[order.symbol]
        and proposed[order.symbol] - ctx.weights_now.get(order.symbol, 0.0) >= trade_threshold
    }
    return bool(
        ctx.risk.target_gross_cap >= sum(saved_restore.values()) - 1e-12
        and not material_pending
        and all(
            desired - ctx.weights_now.get(symbol, 0.0) + 1e-12 < trade_threshold
            or (
                ctx.weights_now.get(symbol, 0.0) >= 0.95 * desired
                and desired - ctx.weights_now.get(symbol, 0.0) < completion_tolerance
            )
            for symbol, desired in proposed.items()
            if desired > 1e-12
        )
    )


def _apply_strategic_restore(
    ctx: _StrategicLifecycleContext,
    *,
    active_symbols: set[str],
    current_selected: dict[str, float],
    restore_confirmed: bool,
) -> dict[str, float]:
    self = ctx.policy
    account = ctx.account
    proposed = dict(current_selected)
    if not (account.strategic_restore_weights and ctx.risk.target_gross_cap > 1e-12 and restore_confirmed):
        return proposed
    saved_restore = {
        symbol: weight
        for symbol, weight in account.strategic_restore_weights.items()
        if symbol in active_symbols and symbol not in account.strategic_exit_bands
    }
    restore = {
        symbol: max(
            current_selected.get(symbol, 0.0),
            saved_restore.get(symbol, current_selected.get(symbol, 0.0)),
        )
        for symbol in active_symbols
    }
    requested = sum(restore.values())
    current_strategy_gross = sum(current_selected.values())
    restore_gross_cap = (
        min(self.cfg.max_gross, max(0.0, ctx.risk.target_gross_cap))
        if current_strategy_gross <= ctx.risk.target_gross_cap + 1e-12
        else self.cfg.max_gross
    )
    scale = min(1.0, restore_gross_cap / requested) if requested > 0 else 0.0
    proposed = {symbol: weight * scale for symbol, weight in restore.items()}
    restore_complete = _strategic_restore_complete(
        ctx,
        saved_restore=saved_restore,
        proposed=proposed,
    )
    if restore_complete:
        account.strategic_restore_weights.clear()
        if ctx.damage_guard_active or ctx.damage_trim_active:
            _settle_strategic_guard(ctx)
    return proposed


def _final_strategic_proposal(
    ctx: _StrategicLifecycleContext,
    *,
    active_symbols: set[str],
    current_selected: dict[str, float],
) -> dict[str, float]:
    buy_risk_open, restore_confirmed = _strategic_restore_confirmed(ctx)
    proposed = _apply_strategic_restore(
        ctx,
        active_symbols=active_symbols,
        current_selected=current_selected,
        restore_confirmed=restore_confirmed,
    )
    account = ctx.account
    if (
        not account.strategic_restore_weights
        and (ctx.damage_guard_active or ctx.damage_trim_active)
        and ctx.risk.state.value == "NORMAL"
        and not ctx.risk.reasons
    ):
        _settle_strategic_guard(ctx)
    if account.candidate_tenure.get("strategic_cohort_started", 0) == 0 and buy_risk_open:
        proposed = dict(account.strategic_cohort_targets)
    if buy_risk_open:
        # Near-target holding completion does not cancel a registered buy's
        # unfilled quantity. Actual reductions and the shared budget still apply.
        for order in account.pending_orders:
            if order.side == "BUY" and order.symbol in active_symbols:
                proposed[order.symbol] = max(
                    proposed.get(order.symbol, 0.0),
                    min(order.target_weight, account.strategic_cohort_targets[order.symbol]),
                )
    if ctx.dominant_profit_lock_armed_now and ctx.dominant_symbol is not None:
        proposed[ctx.dominant_symbol] = min(
            proposed.get(ctx.dominant_symbol, 0.0),
            ctx.policy.cfg.strategic_dominant_retained_gross,
        )
    for symbol in active_symbols & set(account.strategic_exit_bands):
        proposed[symbol] = min(
            proposed.get(symbol, 0.0),
            sum(account.strategic_exit_bands[symbol]),
        )
    return proposed


def _strategic_cohort_targets(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    admission_open: bool = True,
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> tuple[Target, ...] | None:
    """Run the active dynamic cohort through its current strategic epoch.

    Five neighboring ATR exit bands share one position and one final target.
    The bands smooth discrete signal dates without creating sleeves or orders;
    the execution planner still receives only one target weight per symbol. A
    exited candidate must rebuild its own causal qualification. Other
    candidates continue confirmation against the same account-level risk.
    """

    observe_strategic_candidates(
        self, date=date, user_panel=user_panel, leaders=leaders, account=account, risk=risk,
        qualification_panel=qualification_panel, qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    grant_revalidated, invalidated_targets = _revalidated_strategic_targets(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
        weights_now=weights_now,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    if not grant_revalidated:
        return invalidated_targets
    self._initialize_strategic_cohort(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    _promote_filled_strategic_epoch(self, date=date, account=account, risk=risk)
    if account.candidate_tenure.get("strategic_cohort_active", 0) != 1:
        return None
    active_symbols = set(account.strategic_cohort_targets)
    held_cohort = {
        symbol
        for symbol in account.strategic_cohort_symbols
        if (position := account.positions.get(symbol)) is not None and position.shares > 0
    }
    if not active_symbols:
        return _complete_empty_strategic_cohort(
            self,
            date=date,
            leaders=leaders,
            account=account,
            held_cohort=held_cohort,
        )
    ctx = _strategic_lifecycle_context(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        weights_now=weights_now,
    )
    for symbol in sorted(ctx.active_symbols):
        _evaluate_strategic_member(ctx, symbol)
    active_symbols = set(account.strategic_cohort_targets)
    current_selected = {
        symbol: weights_now.get(symbol, 0.0) for symbol in active_symbols if weights_now.get(symbol, 0.0) > 0
    }
    _capture_strategic_restore(
        ctx,
        active_symbols=active_symbols,
        current_selected=current_selected,
    )
    bind_account_strategic_ownership(account)
    proposed = _final_strategic_proposal(
        ctx,
        active_symbols=active_symbols,
        current_selected=current_selected,
    )
    _mark_strategic_grant_pending_execution(
        account=account,
        proposed=proposed,
        weights_now=weights_now,
    )
    return _strategic_active_targets(
        self=self,
        proposed=proposed,
        leaders=leaders,
        account=account,
        dominant_profit_lock_armed_now=ctx.dominant_profit_lock_armed_now,
        dominant_symbol=ctx.dominant_symbol,
        current_selected=current_selected,
    )


def _revalidated_strategic_targets(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    weights_now: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None,
    qualification_leaders: dict[str, LeaderScore] | None,
    strategic_universe: StrategicUniverseRoles | None,
) -> tuple[bool, tuple[Target, ...] | None]:
    if revalidate_strategic_grant(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
        weights_now=weights_now,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    ):
        return True, None
    if not account.strategic_cohort_targets:
        # An invalidated grant owns this session's no-deployment decision;
        # do not fall through and promote a runner through another policy.
        return False, ()
    targets = _strategic_active_targets(
        self=self,
        proposed=dict(account.strategic_cohort_targets),
        leaders=leaders,
        account=account,
        dominant_profit_lock_armed_now=False,
        dominant_symbol=None,
        current_selected=dict(account.strategic_cohort_targets),
    )
    return False, targets


def _promote_filled_strategic_epoch(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    account: AccountState,
    risk: RiskAssessment,
) -> None:
    grant = account.strategic_grant
    epoch = next(
        (
            item
            for item in account.strategic_epochs
            if grant is not None and item.epoch_id == grant.epoch_id
        ),
        None,
    )
    if not (
        epoch is not None
        and epoch.realized_status == "CORE"
        and epoch.first_fill_session
        and str(date.date()) > epoch.first_fill_session
        and account.strategic_qualification.qualification_ready
        and not account.strategic_qualification.deployment_blocked
        and risk.state.value == "NORMAL"
        and not risk.freeze_new_risk
        and not bool(risk.evidence.get("freeze_new_risk", False))
        and account.capital_budget_level == 0
        and account.chronic_level == 0
    ):
        return
    promoted_weight = min(
        epoch.full_weight,
        self.cfg.max_symbol_weight,
        risk.target_gross_cap,
    )
    if promoted_weight <= account.strategic_cohort_targets.get(epoch.owner_symbol, 0.0):
        return
    account.strategic_cohort_targets = {epoch.owner_symbol: promoted_weight}
    epoch.target_weight = promoted_weight
    if grant is not None:
        grant.target_weight = promoted_weight


def _mark_strategic_grant_pending_execution(
    *,
    account: AccountState,
    proposed: dict[str, float],
    weights_now: dict[str, float],
) -> None:
    grant = account.strategic_grant
    if (
        grant is not None
        and grant.status == "QUALIFIED"
        and any(
            proposed.get(symbol, 0.0) > weights_now.get(symbol, 0.0) + 1e-12
            for symbol in proposed
        )
    ):
        grant.status = "PENDING_EXECUTION"


bounded_strategic_restore_risk_open = _bounded_strategic_restore_risk_open
retire_strategic_member = _retire_strategic_member
strategic_cohort_targets = _strategic_cohort_targets
