"""The only portfolio allocator: alpha and risk never submit orders directly."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .types import AccountState, LeaderScore, Lifecycle, Opportunity, RiskAssessment, Target


def current_weights(account: AccountState, prices: dict[str, float]) -> tuple[dict[str, float], float]:
    market_value = sum(
        position.shares * prices.get(symbol, 0.0) for symbol, position in account.positions.items()
    )
    equity = account.cash + market_value
    if equity <= 0:
        raise RuntimeError("account equity must be positive")
    return (
        {
            symbol: position.shares * prices.get(symbol, 0.0) / equity
            for symbol, position in account.positions.items()
        },
        equity,
    )


def effective_n(weights: dict[str, float], correlations: pd.DataFrame | None = None) -> float:
    values = np.array([value for value in weights.values() if value > 0], dtype=float)
    if not len(values):
        return 0.0
    normalized = values / values.sum()
    naive = float(1.0 / np.square(normalized).sum())
    if correlations is None or correlations.empty:
        return naive
    off_diagonal = correlations.where(~np.eye(len(correlations), dtype=bool)).stack()
    median = float(off_diagonal.median()) if not off_diagonal.empty else 0.0
    return naive / (1.0 + max(0.0, median) * (naive - 1.0))


class PortfolioAllocator:
    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    def _targets(
        self,
        *,
        proposed: dict[str, float],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        lifecycle: Lifecycle,
        reason: str,
    ) -> tuple[Target, ...]:
        targets: list[Target] = []
        for symbol in sorted(set(account.positions) | set(proposed)):
            score = leaders.get(symbol)
            weight = min(self.cfg.max_symbol_weight, max(0.0, proposed.get(symbol, 0.0)))
            targets.append(
                Target(
                    symbol=symbol,
                    weight=weight,
                    lifecycle=lifecycle.value,
                    alpha_score=score.score if score else 0.0,
                    confidence=score.confidence if score else 0.0,
                    reason=reason if weight > 0 else "exit: confirmed account risk",
                )
            )
        positive = [item for item in targets if item.weight > 1e-12]
        if len(positive) > self.cfg.max_positions or sum(item.weight for item in positive) > 1.0 + 1e-8:
            raise RuntimeError("allocator violated portfolio hard constraints")
        return tuple(targets)

    def _cap_underdiversified(
        self, proposed: dict[str, float], account: AccountState
    ) -> tuple[dict[str, float], bool]:
        if account.candidate_tenure.get("diversification_capped", 0):
            return proposed, False
        count = len(account.anchor_weights)
        cap = self.cfg.one_anchor_gross_cap if count == 1 else self.cfg.two_anchor_gross_cap
        total = sum(proposed.values())
        if count not in {1, 2} or total <= cap or total <= 0:
            return proposed, False
        scaled = {symbol: weight * cap / total for symbol, weight in proposed.items()}
        account.anchor_weights = {
            symbol: scaled.get(symbol, weight) for symbol, weight in account.anchor_weights.items()
        }
        account.candidate_tenure["diversification_capped"] = 1
        return scaled, True

    def _liquidity_confirmed(self, frame: pd.DataFrame, date: pd.Timestamp) -> bool:
        amounts = frame.loc[:date, "amount"].tail(20)
        positive = amounts[amounts > 0]
        return len(positive) >= 10 and float(positive.median()) >= self.cfg.minimum_median_amount

    def allocate(
        self,
        *,
        date: pd.Timestamp,
        opportunity: Opportunity,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        prices: dict[str, float],
    ) -> tuple[Target, ...]:
        weights_now, _ = current_weights(account, prices)
        cooldown = account.candidate_tenure.get("tactical_cooldown", 0)
        if cooldown > 0:
            account.candidate_tenure["tactical_cooldown"] = cooldown - 1

        tactical = [
            position
            for position in account.positions.values()
            if position.shares > 0 and position.lifecycle == Lifecycle.RECOVERY.value
        ]
        if (
            tactical
            and not account.anchor_weights
            and risk.state.value != "CRISIS"
            and not (account.protected_weights and risk.shock_state == "RECOVERY")
        ):
            position = tactical[0]
            pnl = prices.get(position.symbol, 0.0) / max(position.avg_cost, 1e-12) - 1.0
            held_sessions = len(user_panel[position.symbol].loc[pd.Timestamp(position.entry_date) : date])
            if pnl >= self.cfg.tactical_rebound_take_profit or held_sessions >= 12:
                account.candidate_tenure["tactical_active"] = 0
                account.candidate_tenure["tactical_cooldown"] = self.cfg.tactical_rebound_cooldown_days
                return self._targets(
                    proposed={},
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="controlled rebound exit",
                )
            return self._targets(
                proposed={position.symbol: weights_now.get(position.symbol, 0.0)},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="controlled rebound probe",
            )

        if risk.state.value == "CRISIS":
            return self._targets(
                proposed={},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason="confirmed concentrated leader break",
            )

        if account.protected_weights and risk.shock_state == "RECOVERY":
            proposed = {
                symbol: min(self.cfg.max_symbol_weight, weight)
                for symbol, weight in account.protected_weights.items()
                if symbol in user_panel
            }
            total = sum(proposed.values())
            cap = min(self.cfg.recovery_target_gross, risk.target_gross_cap)
            if account.shock_severity == "SEVERE":
                cap = min(cap, self.cfg.severe_recovery_gross)
            if total > cap and total > 0:
                proposed = {symbol: weight * cap / total for symbol, weight in proposed.items()}
            return self._targets(
                proposed=proposed,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="confirmed post-shock restoration",
            )

        # A strategic anchor is deliberately sticky: price drift is allowed to
        # concentrate winners, while account risk remains the sole cut authority.
        anchored_held = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in account.anchor_weights
            if weights_now.get(symbol, 0.0) > 0
        }
        anchor_elapsed = 0
        if account.recovery_anchor_date and user_panel:
            clock_symbol = next(iter(user_panel))
            anchor_elapsed = len(
                user_panel[clock_symbol].loc[pd.Timestamp(account.recovery_anchor_date) : date]
            ) - 1
        if (
            anchored_held
            and len(anchored_held) == len(account.anchor_weights)
            and len(account.anchor_weights) >= min(3, self.cfg.max_positions)
            and (
                account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
                or anchor_elapsed > self.cfg.recovery_add_window_days
            )
        ):
            return self._targets(
                proposed=anchored_held,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason="mature anchored leader",
            )

        if (
            not account.positions
            and not account.anchor_weights
            and account.candidate_tenure.get("tactical_active", 0) == 0
            and account.candidate_tenure.get("tactical_cooldown", 0) == 0
            and opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}
            and risk.state.value in {"NORMAL", "CAUTION"}
        ):
            rebound: list[LeaderScore] = []
            for symbol, score in leaders.items():
                if symbol not in user_panel or date not in user_panel[symbol].index:
                    continue
                row = user_panel[symbol].loc[date]
                close = scalar(row, "close")
                ma120 = scalar(row, f"ma{self.cfg.trend_slow}")
                if (
                    scalar(row, f"ret{self.cfg.trend_fast}", 0.0) <= -0.15
                    and math.isfinite(close)
                    and math.isfinite(ma120)
                    and close >= ma120
                    and self._liquidity_confirmed(user_panel[symbol], date)
                ):
                    rebound.append(score)
            if rebound:
                pick = max(rebound, key=lambda item: (item.score, item.symbol))
                account.candidate_tenure["tactical_active"] = 1
                return self._targets(
                    proposed={pick.symbol: self.cfg.tactical_rebound_weight},
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="controlled oversold rebound probe",
                )

        if opportunity is Opportunity.RECOVERY:
            proposed = dict(anchored_held)
            if account.candidate_tenure.get("recovery_cohort_locked", 0) == 1:
                for symbol, target_weight in account.anchor_weights.items():
                    proposed.setdefault(symbol, target_weight)
                return self._targets(
                    proposed=proposed,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="causal crash-recovery leader",
                )
            candidates: list[LeaderScore] = []
            crash_depth: dict[str, float] = {}
            recovery_elapsed = 0
            for symbol, score in leaders.items():
                if symbol not in user_panel or date not in user_panel[symbol].index:
                    continue
                frame = user_panel[symbol].loc[:date]
                row = frame.loc[date]
                close = scalar(row, "close")
                ma20 = scalar(row, f"ma{self.cfg.trend_fast}")
                ret120 = scalar(row, f"ret{self.cfg.trend_slow}", 0.0)
                previous_high = float(frame["close"].iloc[-11:-1].max()) if len(frame) >= 11 else float("nan")
                if (
                    math.isfinite(close)
                    and math.isfinite(ma20)
                    and math.isfinite(previous_high)
                    and close >= ma20
                    and close >= previous_high
                    and ret120 < 0
                    and self._liquidity_confirmed(user_panel[symbol], date)
                ):
                    candidates.append(score)
                    crash_depth[symbol] = ret120
                elif symbol in account.anchor_weights and math.isfinite(ret120):
                    crash_depth[symbol] = ret120
            deep_count = sum(value <= -0.30 for value in crash_depth.values())
            admission_depth = -0.15 if deep_count >= 2 else -0.30
            candidates = [
                item
                for item in candidates
                if crash_depth.get(item.symbol, 0.0) <= admission_depth
            ]
            candidates.sort(
                key=lambda item: (crash_depth.get(item.symbol, 0.0), -item.score, item.symbol)
            )
            if account.anchor_weights and account.recovery_anchor_date:
                clock_symbol = next(iter(user_panel))
                recovery_elapsed = len(
                    user_panel[clock_symbol].loc[pd.Timestamp(account.recovery_anchor_date) : date]
                ) - 1
                if recovery_elapsed > self.cfg.recovery_add_window_days:
                    candidates = []
            if candidates:
                previous_members = set(account.anchor_weights)
                cohort = set(account.anchor_weights) | {item.symbol for item in candidates}
                selected = sorted(
                    cohort,
                    key=lambda symbol: (
                        crash_depth.get(symbol, 0.0),
                        -leaders[symbol].score,
                        symbol,
                    ),
                )[: min(3, self.cfg.max_positions)]
                incumbent_order = [symbol for symbol in account.anchor_weights if symbol in selected]
                lead = incumbent_order[0] if incumbent_order else selected[0]
                secondaries = [symbol for symbol in selected if symbol != lead]
                proposed = {lead: min(self.cfg.max_symbol_weight, self.cfg.recovery_target_gross)}
                remaining = max(0.0, self.cfg.recovery_target_gross - proposed[lead])
                if secondaries and remaining > 0:
                    raw = np.array(
                        [max(0.01, leaders[symbol].score) for symbol in secondaries], dtype=float
                    )
                    raw /= raw.sum()
                    for symbol, share in zip(secondaries, raw, strict=True):
                        proposed[symbol] = remaining * float(share)
                account.anchor_weights = dict(proposed)
                if len(selected) == min(3, self.cfg.max_positions) and all(
                    crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected
                ):
                    account.candidate_tenure["recovery_cohort_locked"] = 1
                if not account.recovery_anchor_date:
                    account.recovery_anchor_date = str(date.date())
                cohort_changed = set(selected) != previous_members
            else:
                cohort_changed = False
            if proposed:
                if not cohort_changed:
                    for symbol in account.anchor_weights:
                        if weights_now.get(symbol, 0.0) > 0:
                            proposed[symbol] = weights_now[symbol]
                capped = False
                if recovery_elapsed > self.cfg.recovery_add_window_days:
                    proposed, capped = self._cap_underdiversified(proposed, account)
                return self._targets(
                    proposed=proposed,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason=(
                        "under-diversified recovery cap"
                        if capped
                        else "recovery cohort construction"
                        if cohort_changed
                        else "causal crash-recovery leader"
                    ),
                )
        anchored_held = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in account.anchor_weights
            if weights_now.get(symbol, 0.0) > 0
        }
        if anchored_held:
            anchored_held, capped = self._cap_underdiversified(anchored_held, account)
            return self._targets(
                proposed=anchored_held,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason="under-diversified recovery cap" if capped else "mature anchored leader",
            )

        # With no independently confirmed recovery leader the robust action is
        # cash. This prevents a broad input pool from turning into a generic,
        # high-churn momentum strategy merely because more symbols were supplied.
        return self._targets(
            proposed={},
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="no independently confirmed leader",
        )
