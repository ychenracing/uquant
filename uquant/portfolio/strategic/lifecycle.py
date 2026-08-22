"""Mechanical Task 8 strategic owner extracted from the immutable policy."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...portfolio_core import strategic_dominant_symbol
from ...types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    RiskAssessment,
    Target,
)

if TYPE_CHECKING:
    from .discovery import StrategicPortfolioPolicy

from .targets import _strategic_active_targets, _strategic_completed_exit_targets


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
        and (
            transition_damage <= self.cfg.transition_damage_repair
            or live_book_recovered
        )
    )
    return reason_clean_level2 or repaired_caution


def _retire_strategic_member(account: AccountState, symbol: str) -> None:
    """Remove every live intent owned by one completed cohort member."""
    account.strategic_cohort_targets.pop(symbol, None)
    account.strategic_exit_bands.pop(symbol, None)
    account.strategic_active_bands.pop(symbol, None)
    account.strategic_restore_weights.pop(symbol, None)
    account.protected_weights.pop(symbol, None)


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
) -> tuple[Target, ...] | None:
    """Run the active dynamic cohort through its current strategic epoch.

    Five neighboring ATR exit bands share one position and one final target.
    The bands smooth discrete signal dates without creating sleeves or orders;
    the execution planner still receives only one target weight per symbol. A
    completed epoch may re-arm only after the configured cooldown and a
    materially changed causal cohort signature.
    """
    self._initialize_strategic_cohort(
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        risk=risk,
        admission_open=admission_open,
    )
    if account.candidate_tenure.get("strategic_cohort_active", 0) != 1:
        return None

    active_symbols = set(account.strategic_cohort_targets)
    held_cohort = {
        symbol
        for symbol in account.strategic_cohort_symbols
        if (position := account.positions.get(symbol)) is not None and position.shares > 0
    }
    if not active_symbols:
        if held_cohort:
            return _strategic_completed_exit_targets(
                self=self,
                leaders=leaders,
                account=account,
            )
        # A portfolio-risk event may have copied the active cohort into
        # protected_weights before the strategy's own exit bands finished.
        # Once every member has economically exited and the epoch is being
        # completed, those weights no longer own a restoration right.
        # Keeping them would restore this completed cohort on the next
        # recovery observation and manufacture a sell/rebuy round trip.
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
        account.strategic_rearm_date = str(
            (date + pd.offsets.BDay(self.cfg.strategic_epoch_cooldown_sessions)).date()
        )
        account.strategic_previous_symbols = list(account.strategic_cohort_symbols)
        return None

    account.candidate_tenure["strategic_cohort_days"] = (
        account.candidate_tenure.get("strategic_cohort_days", 0) + 1
    )
    # SECULAR and EMERGING_SECULAR share one lifecycle. Lower-latency
    # transition evidence has narrower restore rights and exits atomically
    # when every neighboring ATR band breaks. Stored signatures remain
    # readable because they are part of the durable account contract.
    transition_impulse_epoch = bool(
        account.strategic_candidate_signature.startswith(
            "strategic_qualification:transition_impulse:"
        )
        or ":evidence=transition_impulse" in account.strategic_candidate_signature
    )
    strategic_damage_guard_active = bool(
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
    strategic_damage_trim_active = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get(
            "strategic_damage_trim_epoch", -1
        )
        == account.strategic_epoch
        and account.candidate_tenure.get(
            "strategic_damage_guard_complete_epoch", -1
        )
        != account.strategic_epoch
        and bool(account.strategic_restore_weights)
    )
    strategic_damage_guard_owns_transition = bool(
        strategic_damage_guard_active
        or strategic_damage_trim_active
        or risk.evidence.get("strategic_damage_guard", False)
    )
    dominant_symbol = strategic_dominant_symbol(account)
    dominant_profit_locked = bool(
        dominant_symbol is not None
        and account.candidate_tenure.get(
            "strategic_dominant_profit_lock_epoch", -1
        )
        == account.strategic_epoch
    )
    dominant_profit_lock_armed_now = False
    # A partially held cohort is not started: the missing members still
    # need targets.  Treating "any member held" as complete previously
    # stranded broad-universe runs in a one-name pseudo-cohort forever.
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
    for symbol in sorted(active_symbols):
        position = account.positions.get(symbol)
        if position is None or position.shares <= 0:
            if (
                transition_impulse_epoch
                and account.candidate_tenure.get("strategic_cohort_started", 0) == 1
                and (
                    symbol in account.strategic_restore_weights
                    or symbol in account.protected_weights
                )
            ):
                # A low-latency impulse owns less durable evidence than an
                # established cohort. Once a hard portfolio event has
                # economically liquidated a member, its old pre-shock
                # restore right cannot reopen that position; it must earn
                # a fresh epoch from current evidence.
                self._retire_strategic_member(account, symbol)
            elif symbol in account.strategic_exit_bands:
                # Exit bands are a sell-only decomposition of shares that
                # still exist; they are never a future buy target.  A
                # broker-authoritative zero position therefore settles the
                # member even when a portfolio-risk liquidation completed
                # faster than the staged strategy trail.  Keeping those
                # weights would strand the epoch forever and could later
                # turn an old structural exit into an unintended re-entry.
                self._retire_strategic_member(account, symbol)
            elif (
                account.candidate_tenure.get("strategic_cohort_started", 0) == 1
                and symbol not in account.strategic_restore_weights
                and symbol not in account.protected_weights
                and not any(
                    order.symbol == symbol and order.side == "BUY" for order in account.pending_orders
                )
            ):
                # Once every cohort member has originally filled, an
                # unexplained broker-authoritative zero is an exit, not an
                # implicit permission to buy from a stale target.  Only an
                # explicit restoration target or durable BUY may keep a
                # missing member alive.
                self._retire_strategic_member(account, symbol)
            continue
        frame = user_panel.get(symbol)
        if frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        core_costs = [
            tranche.avg_cost
            for tranche in position.tranches
            if tranche.shares > 0 and tranche.lifecycle == Lifecycle.CORE.value
        ]
        strategic_cost = min(core_costs) if core_costs else position.avg_cost
        pnl = close / max(strategic_cost, 1e-12) - 1.0
        if pnl <= self.cfg.strategic_cohort_disaster_stop:
            self._retire_strategic_member(account, symbol)
            continue
        atr = scalar(row, "atr", math.inf)
        structural_damage = (
            close < scalar(row, f"ma{self.cfg.trend_fast}")
            and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
        )
        peak_mfe = position.highest_close / max(strategic_cost, 1e-12) - 1.0
        if (
            symbol == dominant_symbol
            and not dominant_profit_locked
            and peak_mfe >= self.cfg.strategic_dominant_profit_lock_mfe
        ):
            account.candidate_tenure[
                "strategic_dominant_profit_lock_epoch"
            ] = account.strategic_epoch
            account.strategic_cohort_targets[symbol] = min(
                account.strategic_cohort_targets[symbol],
                self.cfg.strategic_dominant_retained_gross,
            )
            account.strategic_restore_weights.pop(symbol, None)
            account.protected_weights.pop(symbol, None)
            dominant_profit_locked = True
            dominant_profit_lock_armed_now = True
        triggered = [
            peak_mfe >= self.cfg.strategic_cohort_profit_arm
            and structural_damage
            and math.isfinite(atr)
            and close <= position.highest_close - threshold * atr
            for threshold in thresholds
        ]
        if (
            any(triggered)
            and symbol not in account.strategic_exit_bands
            and not strategic_damage_guard_owns_transition
            and not (symbol == dominant_symbol and dominant_profit_locked)
        ):
            current = weights_now.get(symbol, 0.0)
            account.strategic_exit_bands[symbol] = [current / band_count] * band_count
            account.strategic_active_bands[symbol] = [False] * band_count
            # Once a structural strategy exit starts, an older portfolio
            # cap must never buy this symbol back.  Risk restoration may
            # still preserve untouched cohort members.
            account.strategic_restore_weights.pop(symbol, None)
            account.protected_weights.pop(symbol, None)
            account.candidate_tenure["strategic_profit_armed"] = 1
        if symbol not in account.strategic_exit_bands:
            continue
        # A structural exit owns this member until zero.  Reconciliation
        # or a later crisis capture may have reintroduced a stale restore
        # right after the band first opened, so clear both maps
        # idempotently on every evaluation rather than only on day one.
        account.strategic_restore_weights.pop(symbol, None)
        account.protected_weights.pop(symbol, None)
        bands = account.strategic_exit_bands[symbol]
        armed = account.strategic_active_bands[symbol]
        if transition_impulse_epoch and all(triggered):
            armed[:] = [True] * band_count
            bands[:] = [0.0] * band_count
        else:
            for index, signal in enumerate(triggered):
                if signal:
                    armed[index] = True
                    transition_accelerated_step = self.cfg.strategic_cohort_exit_step
                    gradual_structural_damage = bool(
                        scalar(row, "ret5", -math.inf)
                        > self.cfg.tactical_rebound_oversold_max_ret5
                        and scalar(row, "ret20", math.inf)
                        <= self.cfg.tactical_rebound_breadth_max_ret20
                        and scalar(row, "ret60", math.inf) <= 0.0
                        and scalar(row, "ret120", -math.inf)
                        >= self.cfg.strategic_secular_min_score
                    )
                    repaired_guard_step = (
                        (
                            self.cfg.strategic_gradual_post_guard_exit_step
                            if gradual_structural_damage
                            else self.cfg.strategic_post_guard_exit_step
                        )
                        if account.strategic_epoch > 0
                        and account.candidate_tenure.get(
                            "strategic_damage_guard_complete_epoch", -1
                        )
                        == account.strategic_epoch
                        and account.candidate_tenure.get(
                            "strategic_guard_level2_epoch", -1
                        )
                        != account.strategic_epoch
                        else self.cfg.strategic_cohort_exit_step
                    )
                    exit_step = max(
                        transition_accelerated_step,
                        repaired_guard_step,
                    )
                    bands[index] = max(
                        0.0,
                        bands[index] - exit_step / band_count,
                    )
        if sum(bands) <= 1e-12:
            account.strategic_cohort_targets.pop(symbol, None)

    active_symbols = set(account.strategic_cohort_targets)
    current_selected = {
        symbol: weights_now.get(symbol, 0.0)
        for symbol in active_symbols
        if weights_now.get(symbol, 0.0) > 0
    }
    current_gross = sum(current_selected.values())
    if risk.target_gross_cap + 0.02 < current_gross:
        # Capture each still-live member independently. A later sparse risk
        # cut may remove one member entirely; replacing the whole map from
        # the surviving aggregate would destroy that member's only durable
        # restoration intent.  Existing missing-member rights are monotone;
        # only the remaining gross headroom may absorb newly observed drift,
        # so the persisted weight-map invariant can never exceed max_gross.
        saved = {
            symbol: min(self.cfg.max_symbol_weight, max(0.0, weight))
            for symbol, weight in account.strategic_restore_weights.items()
            if symbol in active_symbols and symbol not in account.strategic_exit_bands
        }
        saved_total = sum(saved.values())
        if saved_total > self.cfg.max_gross + 1e-12:
            saved = {
                symbol: weight * self.cfg.max_gross / saved_total for symbol, weight in saved.items()
            }
            saved_total = self.cfg.max_gross
        increments = {
            symbol: max(0.0, weight - saved.get(symbol, 0.0))
            for symbol, weight in current_selected.items()
            if symbol not in account.strategic_exit_bands
        }
        increment_total = sum(increments.values())
        headroom = max(0.0, self.cfg.max_gross - saved_total)
        increment_scale = min(1.0, headroom / increment_total) if increment_total > 0 else 0.0
        for symbol, increment in increments.items():
            saved[symbol] = saved.get(symbol, 0.0) + increment * increment_scale
        account.strategic_restore_weights = {
            symbol: weight for symbol, weight in saved.items() if weight > 1e-12
        }
    proposed = dict(current_selected)
    buy_risk_open = bool(not risk.freeze_new_risk and not risk.evidence.get("freeze_new_risk", False))
    bounded_restore_risk_open = self._bounded_strategic_restore_risk_open(
        risk=risk,
        account=account,
    )
    strategic_guard_repaired = bool(
        not (
            strategic_damage_guard_active
            or strategic_damage_trim_active
        )
        or bounded_restore_risk_open
        or (
            risk.votes <= 1
            and float(risk.evidence.get("transition_damage", 0.0))
            <= self.cfg.transition_damage_repair
        )
    )
    restore_confirmed = bool(
        (buy_risk_open or bounded_restore_risk_open)
        and strategic_guard_repaired
        and (
            risk.state.value == "NORMAL"
            or (
                risk.state.value == "CAUTION"
                and risk.votes <= 2
                and (
                    bounded_restore_risk_open
                    or float(risk.evidence.get("transition_damage", 1.0))
                    <= self.cfg.transition_damage_repair
                )
            )
        )
    )
    if account.strategic_restore_weights and risk.target_gross_cap > 1e-12 and restore_confirmed:
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
        # If live exposure already exceeds the current risk cap, the outer
        # allocator owns the required sell plan.  The strategy still must
        # hand it an admissible pre-risk vector: per-member winner drift
        # plus saved loser restoration can otherwise exceed max_gross
        # before the risk reducer gets a chance to run.
        restore_gross_cap = (
            min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
            if current_strategy_gross <= risk.target_gross_cap + 1e-12
            else self.cfg.max_gross
        )
        scale = (
            min(1.0, restore_gross_cap / requested)
            if requested > 0
            else 0.0
        )
        proposed = {symbol: weight * scale for symbol, weight in restore.items()}
        equity = account.cash + sum(
            position.shares * prices.get(symbol, 0.0)
            for symbol, position in account.positions.items()
        )
        restore_trade_threshold = max(
            self.cfg.restoration_min_trade_weight,
            self.cfg.min_trade_value / equity if equity > 1e-12 else math.inf,
        )
        restore_completion_tolerance = max(
            self.cfg.min_trade_weight,
            self.cfg.min_trade_value / equity if equity > 1e-12 else math.inf,
        )
        material_pending_restore_buys = {
            order.symbol
            for order in account.pending_orders
            if order.side == "BUY"
            and order.symbol in proposed
            and weights_now.get(order.symbol, 0.0)
            < 0.95 * proposed[order.symbol]
            and proposed[order.symbol] - weights_now.get(order.symbol, 0.0)
            >= restore_trade_threshold
        }
        # Completion is a per-member invariant.  Aggregate gross can reach
        # 95% while a capacity-constrained member is still entirely
        # missing; clearing the map in that state loses its only durable
        # restoration intent and strands the epoch.  Conversely, compare
        # against the scaled, cap-attainable target: winner drift can make
        # the unscaled snapshot impossible without selling healthy lots,
        # and an economically satisfied stale BUY must not keep the guard
        # active forever.
        restore_complete = bool(
            risk.target_gross_cap >= sum(saved_restore.values()) - 1e-12
            and not material_pending_restore_buys
            and all(
                desired - weights_now.get(symbol, 0.0) + 1e-12
                < restore_trade_threshold
                or (
                    weights_now.get(symbol, 0.0) >= 0.95 * desired
                    and desired - weights_now.get(symbol, 0.0)
                    < restore_completion_tolerance
                )
                for symbol, desired in proposed.items()
                if desired > 1e-12
            )
        )
        if restore_complete:
            account.strategic_restore_weights.clear()
            if strategic_damage_guard_active or strategic_damage_trim_active:
                account.candidate_tenure[
                    "strategic_damage_guard_active_epoch"
                ] = 0
                account.candidate_tenure["strategic_damage_trim_epoch"] = 0
                account.candidate_tenure[
                    "strategic_damage_guard_complete_epoch"
                ] = account.strategic_epoch
    elif (
        (strategic_damage_guard_active or strategic_damage_trim_active)
        and risk.state.value == "NORMAL"
        and not risk.reasons
        and not account.strategic_restore_weights
    ):
        # A guard can be armed while the broker book is already below its
        # cap, leaving no economic gap to restore.  Settle that one-shot
        # lifecycle only after clean risk evidence returns.
        account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
        account.candidate_tenure["strategic_damage_trim_epoch"] = 0
        account.candidate_tenure[
            "strategic_damage_guard_complete_epoch"
        ] = account.strategic_epoch
    if account.candidate_tenure.get("strategic_cohort_started", 0) == 0 and buy_risk_open:
        proposed = dict(account.strategic_cohort_targets)
    if dominant_profit_lock_armed_now and dominant_symbol is not None:
        proposed[dominant_symbol] = min(
            proposed.get(dominant_symbol, 0.0),
            self.cfg.strategic_dominant_retained_gross,
        )
    for symbol in active_symbols & set(account.strategic_exit_bands):
        proposed[symbol] = min(
            proposed.get(symbol, 0.0),
            sum(account.strategic_exit_bands[symbol]),
        )
    return _strategic_active_targets(
        self=self,
        proposed=proposed,
        leaders=leaders,
        account=account,
        dominant_profit_lock_armed_now=dominant_profit_lock_armed_now,
        dominant_symbol=dominant_symbol,
        current_selected=current_selected,
    )
