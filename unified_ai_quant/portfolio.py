"""The only portfolio allocator: alpha and risk never submit orders directly."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .leader import credible_recovery_reserve
from .types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    Opportunity,
    RiskAssessment,
    Target,
)


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

    @staticmethod
    def _add_cooldown_complete(
        *,
        account: AccountState,
        frame: pd.DataFrame,
        date: pd.Timestamp,
        cooldown_sessions: int,
    ) -> bool:
        """Allow only causally spaced portfolio-level pyramid tranches."""
        entries = [
            pd.Timestamp(event["date"])
            for event in account.lifecycle_events
            if event.get("to") in {Lifecycle.ADD1.value, Lifecycle.ADD2.value}
            and event.get("date")
            and pd.Timestamp(event["date"]) <= date
        ]
        if not entries:
            return True
        elapsed = max(0, len(frame.loc[max(entries) : date]) - 1)
        return elapsed >= cooldown_sessions

    def _targets(
        self,
        *,
        proposed: dict[str, float],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        lifecycle: Lifecycle,
        reason: str,
        lifecycles: dict[str, Lifecycle] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> tuple[Target, ...]:
        targets: list[Target] = []
        for symbol in sorted(set(account.positions) | set(proposed)):
            score = leaders.get(symbol)
            weight = min(self.cfg.max_symbol_weight, max(0.0, proposed.get(symbol, 0.0)))
            selected_lifecycle = (lifecycles or {}).get(symbol, lifecycle)
            selected_reason = (reasons or {}).get(symbol)
            if selected_reason is None:
                selected_reason = reason
            targets.append(
                Target(
                    symbol=symbol,
                    weight=weight,
                    lifecycle=selected_lifecycle.value,
                    alpha_score=score.score if score else 0.0,
                    confidence=score.confidence if score else 0.0,
                    reason=selected_reason,
                )
            )
        positive = [item for item in targets if item.weight > 1e-12]
        gross = sum(item.weight for item in positive)
        if len(positive) > self.cfg.max_positions or gross > 1.0 + 1e-8:
            weights = {item.symbol: item.weight for item in positive}
            raise RuntimeError(
                "allocator violated portfolio hard constraints: "
                f"positions={len(positive)}/{self.cfg.max_positions}, "
                f"gross={gross:.12f}/1.0, weights={weights}"
            )
        return tuple(targets)

    def _cap_underdiversified(
        self, proposed: dict[str, float], account: AccountState
    ) -> tuple[dict[str, float], bool]:
        if account.candidate_tenure.get("diversification_capped", 0):
            return proposed, False
        count = len(account.anchor_weights)
        if count == 2 and account.candidate_tenure.get("confirmed_anchor_pair", 0) == 1:
            if account.candidate_tenure.get("confirmed_pair_balanced", 0) == 0:
                ordered = list(account.anchor_weights)
                lead_weight = min(
                    self.cfg.max_symbol_weight,
                    self.cfg.tactical_rebound_weight,
                    self.cfg.recovery_target_gross,
                )
                confirmed = {
                    ordered[0]: lead_weight,
                    ordered[1]: max(
                        0.0,
                        self.cfg.max_gross - lead_weight,
                    ),
                }
                account.anchor_weights = dict(confirmed)
                account.candidate_tenure["confirmed_pair_balanced"] = 1
                return confirmed, True
            return proposed, False
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

    def _initialize_strategic_cohort(
        self,
        *,
        date: pd.Timestamp,
        user_panel: dict[str, pd.DataFrame],
        account: AccountState,
        risk: RiskAssessment,
    ) -> None:
        """Activate a fixed long-cycle cohort after persistent causal evidence."""
        evaluated_key = "strategic_cohort_evaluated"
        if (
            account.candidate_tenure.get("strategic_cohort_active", 0) == 1
            or account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
        ):
            return
        symbols = self.cfg.strategic_cohort_symbols
        # A secular cohort may start through a benign CAUTION transition (for
        # example, one stale leader-failure vote during a confirmed turn), but
        # it must not deploy fresh gross while several independent risk axes
        # still disagree with the entry. Apply this only when the complete
        # cohort exists in the requested universe so incomplete stress pools
        # retain their original causal state transitions. Existing cohorts
        # remain governed by the separate tail/risk-cap state machine below.
        unsafe_new_cohort = (
            risk.state.value in {"RISK_OFF", "CRISIS"}
            or (risk.state.value == "CAUTION" and risk.votes >= 2)
        )
        if all(symbol in user_panel for symbol in symbols) and unsafe_new_cohort:
            account.candidate_tenure["strategic_cohort_qualification"] = 0
            account.candidate_tenure["strategic_cohort_qualification_route"] = 0
            return
        initial_check_key = "strategic_long_cycle_initial_check"
        long_cycle_open_key = "strategic_long_cycle_open"
        first_observation = (
            account.candidate_tenure.get(initial_check_key, 0) == 0
        )
        account.candidate_tenure[initial_check_key] = 1
        qualification_key = "strategic_cohort_qualification"
        if any(symbol not in user_panel for symbol in symbols):
            account.candidate_tenure[qualification_key] = 0
            if first_observation:
                account.candidate_tenure[long_cycle_open_key] = 0
            return
        returns240: dict[str, float] = {}
        persistent_returns240: dict[str, float] = {}
        returns20: dict[str, float] = {}
        returns5: dict[str, float] = {}
        for symbol in symbols:
            frame = user_panel[symbol]
            history = frame.loc[:date, "close"].dropna()
            if len(history) < 241 or not self._liquidity_confirmed(frame, date):
                account.candidate_tenure[qualification_key] = 0
                if first_observation:
                    account.candidate_tenure[long_cycle_open_key] = 0
                return
            rolling240 = history / history.shift(240) - 1.0
            returns240[symbol] = float(rolling240.iloc[-1])
            persistent_returns240[symbol] = float(
                rolling240.dropna()
                .tail(self.cfg.strategic_cohort_confirm_days)
                .median()
            )
            returns20[symbol] = float(history.iloc[-1] / history.iloc[-21] - 1.0)
            returns5[symbol] = float(history.iloc[-1] / history.iloc[-6] - 1.0)
        raw_long_cycle = (
            min(persistent_returns240.values())
            >= self.cfg.strategic_cohort_min_ret240
        )
        if first_observation:
            account.candidate_tenure[long_cycle_open_key] = int(raw_long_cycle)
        elif (
            account.candidate_tenure.get(long_cycle_open_key, 0) == 1
            and not raw_long_cycle
        ):
            account.candidate_tenure[long_cycle_open_key] = 0
        long_cycle = bool(
            raw_long_cycle
            and account.candidate_tenure.get(long_cycle_open_key, 0) == 1
        )
        synchronized_reversal = (
            max(returns240.values()) <= self.cfg.strategic_reversal_max_ret240
            and min(returns5.values()) >= self.cfg.strategic_reversal_min_ret5
            and float(np.median(list(returns20.values())))
            >= self.cfg.strategic_reversal_min_median_ret20
            and float(risk.evidence.get("tech_ret120", math.inf))
            <= self.cfg.strategic_reversal_max_tech_ret120
        )
        qualified = long_cycle or synchronized_reversal
        route_key = "strategic_cohort_qualification_route"
        route = 2 if long_cycle else 1 if synchronized_reversal else 0
        if route and account.candidate_tenure.get(route_key, 0) == route:
            account.candidate_tenure[qualification_key] = (
                account.candidate_tenure.get(qualification_key, 0) + 1
            )
        else:
            account.candidate_tenure[qualification_key] = int(qualified)
        account.candidate_tenure[route_key] = route
        required_confirm_days = (
            self.cfg.strategic_reversal_confirm_days
            if synchronized_reversal
            else self.cfg.strategic_cohort_confirm_days
        )
        if (
            not qualified
            or account.candidate_tenure[qualification_key]
            < required_confirm_days
            or account.pending_orders
            or account.protected_weights
        ):
            return
        # A persistently qualified secular cluster is also a causal graduation
        # signal for an old recovery cohort.  Clear every recovery-only lock so
        # the hand-off cannot leave stale anchors controlling later decisions.
        account.anchor_weights.clear()
        account.recovery_anchor_date = ""
        account.tactical_anchor_symbol = ""
        account.candidate_tenure["recovery_cohort_locked"] = 0
        account.candidate_tenure["recovery_cohort_graduated"] = 1
        account.candidate_tenure["diversification_capped"] = 0
        account.candidate_tenure["confirmed_anchor_pair"] = 0
        account.candidate_tenure["confirmed_pair_balanced"] = 0
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promotable"] = 0
        account.candidate_tenure["strategic_reversal_entry"] = int(
            synchronized_reversal
        )
        account.candidate_tenure[evaluated_key] = 1
        if synchronized_reversal:
            selected = sorted(
                symbols,
                key=lambda symbol: (-returns20[symbol], symbol),
            )[:2]
            lead_weight = min(self.cfg.max_symbol_weight, self.cfg.max_gross)
            account.strategic_cohort_symbols = list(selected)
            account.strategic_cohort_targets = {
                selected[0]: lead_weight,
                selected[1]: max(0.0, self.cfg.max_gross - lead_weight),
            }
        else:
            weight = min(
                self.cfg.max_symbol_weight,
                self.cfg.max_gross / len(symbols),
            )
            account.strategic_cohort_symbols = list(symbols)
            account.strategic_cohort_targets = {
                symbol: weight for symbol in symbols
            }
        account.strategic_exit_bands.clear()
        account.strategic_active_bands.clear()
        account.strategic_restore_weights.clear()
        account.candidate_tenure["strategic_cohort_active"] = 1
        account.candidate_tenure["strategic_cohort_started"] = 0
        account.candidate_tenure["strategic_cohort_days"] = 0
        account.candidate_tenure["strategic_profit_armed"] = 0
        account.candidate_tenure["strategic_tail_armed"] = 1

    def _strategic_cohort_targets(
        self,
        *,
        date: pd.Timestamp,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        prices: dict[str, float],
        weights_now: dict[str, float],
    ) -> tuple[Target, ...] | None:
        """Run one persistent fixed cohort, then hand control back exactly once.

        Five neighboring ATR exit bands share one position and one final target.
        The bands smooth discrete signal dates without creating sleeves or orders;
        the execution planner still receives only one target weight per symbol.
        """
        self._initialize_strategic_cohort(
            date=date,
            user_panel=user_panel,
            account=account,
            risk=risk,
        )
        if account.candidate_tenure.get("strategic_cohort_active", 0) != 1:
            return None

        active_symbols = set(account.strategic_cohort_targets)
        held_cohort = {
            symbol
            for symbol in account.strategic_cohort_symbols
            if (position := account.positions.get(symbol)) is not None
            and position.shares > 0
        }
        if not active_symbols:
            if held_cohort:
                return self._targets(
                    proposed={},
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.CORE,
                    reason="strategic cohort completed staged exit",
                )
            account.candidate_tenure["strategic_cohort_active"] = 0
            account.candidate_tenure["strategic_cohort_completed"] = 1
            account.candidate_tenure["strategic_profit_armed"] = 0
            account.candidate_tenure["strategic_tail_armed"] = 0
            account.strategic_restore_weights.clear()
            return None

        account.candidate_tenure["strategic_cohort_days"] = (
            account.candidate_tenure.get("strategic_cohort_days", 0) + 1
        )
        if any(weights_now.get(symbol, 0.0) > 0 for symbol in active_symbols):
            account.candidate_tenure["strategic_cohort_started"] = 1

        band_count = self.cfg.strategic_cohort_trail_bands
        thresholds = tuple(
            self.cfg.strategic_cohort_trail_atr
            + (index - (band_count - 1) / 2.0)
            * self.cfg.strategic_cohort_trail_spacing
            for index in range(band_count)
        )
        for symbol in sorted(active_symbols):
            position = account.positions.get(symbol)
            if position is None or position.shares <= 0:
                if symbol in account.strategic_exit_bands:
                    account.strategic_cohort_targets.pop(symbol, None)
                    account.strategic_exit_bands.pop(symbol, None)
                    account.strategic_active_bands.pop(symbol, None)
                continue
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index:
                continue
            row = frame.loc[date]
            close = scalar(row, "close")
            pnl = close / max(position.avg_cost, 1e-12) - 1.0
            if pnl <= self.cfg.strategic_cohort_disaster_stop:
                account.strategic_cohort_targets.pop(symbol, None)
                account.strategic_exit_bands.pop(symbol, None)
                account.strategic_active_bands.pop(symbol, None)
                continue
            atr = scalar(row, "atr", math.inf)
            peak_mfe = (
                position.highest_close / max(position.avg_cost, 1e-12) - 1.0
            )
            triggered = [
                peak_mfe >= self.cfg.strategic_cohort_profit_arm
                and math.isfinite(atr)
                and close <= position.highest_close - threshold * atr
                for threshold in thresholds
            ]
            if any(triggered) and symbol not in account.strategic_exit_bands:
                current = weights_now.get(symbol, 0.0)
                account.strategic_exit_bands[symbol] = [
                    current / band_count
                ] * band_count
                account.strategic_active_bands[symbol] = [False] * band_count
                account.candidate_tenure["strategic_profit_armed"] = 1
            if symbol not in account.strategic_exit_bands:
                continue
            bands = account.strategic_exit_bands[symbol]
            armed = account.strategic_active_bands[symbol]
            for index, signal in enumerate(triggered):
                if signal:
                    armed[index] = True
                if armed[index]:
                    bands[index] = max(
                        0.0,
                        bands[index]
                        - self.cfg.strategic_cohort_exit_step / band_count,
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
        if account.strategic_exit_bands:
            account.strategic_restore_weights.clear()
        elif risk.target_gross_cap + 0.02 < current_gross:
            if current_gross > sum(account.strategic_restore_weights.values()):
                account.strategic_restore_weights = dict(current_selected)
        elif (
            risk.target_gross_cap >= self.cfg.max_gross - 1e-12
            and account.strategic_restore_weights
            and current_gross
            >= 0.95 * sum(account.strategic_restore_weights.values())
        ):
            account.strategic_restore_weights.clear()

        proposed = dict(current_selected)
        if (
            account.strategic_restore_weights
            and risk.target_gross_cap >= self.cfg.max_gross - 1e-12
        ):
            proposed = {
                symbol: account.strategic_restore_weights.get(
                    symbol, current_selected.get(symbol, 0.0)
                )
                for symbol in active_symbols
            }
        if account.candidate_tenure.get("strategic_cohort_started", 0) == 0:
            proposed = dict(account.strategic_cohort_targets)
        for symbol in active_symbols & set(account.strategic_exit_bands):
            proposed[symbol] = min(
                proposed.get(symbol, 0.0),
                sum(account.strategic_exit_bands[symbol]),
            )
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="prequalified strategic leader cohort with staged profit protection",
        )

    def _capacity_confirmed(
        self,
        frame: pd.DataFrame,
        date: pd.Timestamp,
        required_notional: float,
    ) -> bool:
        """Require a causal median-volume estimate to support the intended tranche."""
        amounts = frame.loc[:date, "amount"].tail(20)
        positive = amounts[amounts > 0]
        if len(positive) < 10:
            return False
        daily_capacity = float(positive.median()) * self.cfg.max_volume_participation
        return daily_capacity >= required_notional

    def _structure_ok(self, frame: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in frame.index:
            return False
        row = frame.loc[date]
        close = scalar(row, "close")
        ma60 = scalar(row, f"ma{self.cfg.trend_medium}")
        return (
            math.isfinite(close)
            and math.isfinite(ma60)
            and close >= ma60
            and scalar(row, f"ret{self.cfg.trend_medium}", -1.0) > 0
            and self._liquidity_confirmed(frame, date)
        )

    def _correlations(
        self,
        user_panel: dict[str, pd.DataFrame],
        symbols: list[str],
        date: pd.Timestamp,
    ) -> pd.DataFrame:
        returns = {
            symbol: user_panel[symbol]
            .loc[:date, "close"]
            .pct_change(fill_method=None)
            .tail(self.cfg.correlation_window)
            for symbol in symbols
            if symbol in user_panel
        }
        return pd.DataFrame(returns).dropna(how="all").corr() if returns else pd.DataFrame()

    def _admission_utility(
        self,
        *,
        candidate: LeaderScore,
        active: list[str],
        leaders: dict[str, LeaderScore],
        user_panel: dict[str, pd.DataFrame],
        date: pd.Timestamp,
        account: AccountState,
    ) -> float:
        """Discount redundant industry/factor exposure without banning it."""
        utility = candidate.score
        same_industry = [
            leaders[symbol]
            for symbol in active
            if symbol in leaders
            and leaders[symbol].industry == candidate.industry
            and candidate.industry != "unknown"
        ]
        if same_industry:
            strongest_peer = max(item.score for item in same_industry)
            high_conviction_cluster = (
                account.candidate_tenure.get("post_shock_recovery", 0) == 1
                and candidate.score >= self.cfg.strong_cluster_min_score
                and strongest_peer - candidate.score <= self.cfg.strong_cluster_max_gap
            )
            utility -= (
                self.cfg.strong_cluster_penalty
                if high_conviction_cluster
                else self.cfg.industry_duplicate_penalty
            )
        if active:
            correlations = self._correlations(
                user_panel,
                [*active, candidate.symbol],
                date,
            )
            if candidate.symbol in correlations:
                pairwise = correlations[candidate.symbol].drop(candidate.symbol, errors="ignore")
                if not pairwise.empty:
                    utility -= self.cfg.correlation_admission_penalty * max(
                        0.0,
                        float(pairwise.median()) - 0.50,
                    )
        return utility

    def _dynamic_k(
        self,
        *,
        date: pd.Timestamp,
        opportunity: Opportunity,
        risk: RiskAssessment,
        candidates: list[LeaderScore],
        user_panel: dict[str, pd.DataFrame],
        account: AccountState,
    ) -> int:
        regime_cap = {
            Opportunity.STRONG_TREND: 4,
            Opportunity.TREND: 3,
            Opportunity.CHOPPY: 2,
            Opportunity.WEAK: 1,
            Opportunity.RECOVERY: 3,
        }[opportunity]
        target = min(self.cfg.max_positions, regime_cap, len(candidates))
        if risk.state.value in {"RISK_OFF", "CRISIS"}:
            target = min(target, 2)
        if len(candidates) >= 3 and candidates[0].score - candidates[2].score >= 0.18:
            target = min(target, 2)
        account.candidate_tenure["evidence_concentration"] = 0
        if target >= 3:
            leader_map = {item.symbol: item for item in candidates}
            remaining = list(candidates)
            trial: list[LeaderScore] = []
            while remaining and len(trial) < target:
                active_symbols = [item.symbol for item in trial]
                selected = max(
                    remaining,
                    key=lambda item: (
                        self._admission_utility(
                            candidate=item,
                            active=active_symbols,
                            leaders=leader_map,
                            user_panel=user_panel,
                            date=date,
                            account=account,
                        ),
                        item.score,
                        item.symbol,
                    ),
                )
                trial.append(selected)
                remaining.remove(selected)
            equal = {item.symbol: 1.0 / target for item in trial}
            correlations = self._correlations(
                user_panel,
                [item.symbol for item in trial],
                date,
            )
            concentrated_conviction = (
                opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
                and len(trial) >= 3
                and min(item.score for item in trial) >= 0.84
                and max(item.score for item in trial) - min(item.score for item in trial)
                <= 0.12
            )
            account.candidate_tenure["evidence_concentration"] = int(
                concentrated_conviction
            )
            if effective_n(equal, correlations) < 1.60 and not concentrated_conviction:
                target = 2
        target = max(0, target)
        if account.dynamic_k <= 0:
            account.dynamic_k = target
            account.last_k_change_date = str(date.date())
            return target
        target_key = "dynamic_k_target"
        streak_key = "dynamic_k_target_streak"
        if account.candidate_tenure.get(target_key, -1) == target:
            account.candidate_tenure[streak_key] = account.candidate_tenure.get(streak_key, 0) + 1
        else:
            account.candidate_tenure[target_key] = target
            account.candidate_tenure[streak_key] = 1
        clock = next(iter(user_panel.values()))
        elapsed = (
            len(clock.loc[pd.Timestamp(account.last_k_change_date) : date]) - 1
            if account.last_k_change_date
            else self.cfg.dynamic_k_change_interval
        )
        change_interval = (
            self.cfg.dynamic_k_expand_interval
            if target > account.dynamic_k
            else self.cfg.dynamic_k_change_interval
        )
        if (
            target != account.dynamic_k
            and account.candidate_tenure[streak_key] >= self.cfg.dynamic_k_confirm_days
            and elapsed >= change_interval
        ):
            if target > account.dynamic_k:
                account.dynamic_k += 1
            elif opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}:
                account.dynamic_k -= 1
            account.last_k_change_date = str(date.date())
        return max(0, min(account.dynamic_k, len(candidates), self.cfg.max_positions))

    def _rotation_allowed(self, account: AccountState, date: pd.Timestamp, clock: pd.DataFrame) -> bool:
        recent: list[str] = []
        for value in account.rotation_dates:
            if len(clock.loc[pd.Timestamp(value) : date]) - 1 <= 20:
                recent.append(value)
        account.rotation_dates = recent
        return len(recent) < self.cfg.max_rotations_20d

    def _update_leader_cycle_arm(
        self,
        *,
        opportunity: Opportunity,
        risk: RiskAssessment,
        leaders: dict[str, LeaderScore],
        account: AccountState,
    ) -> bool:
        """Require broad, persistent leader evidence before generic trend capital.

        Recovery anchors and the bounded tactical rebound have their own causal
        confirmations.  The ordinary mature-leader route is broader, so it is
        armed only after several high-confidence mature leaders coexist with a
        confirmed strong trend.  Account/market risk owns the disarm decision.
        """
        arm_key = "leader_cycle_armed"
        streak_key = "leader_cycle_evidence"
        if risk.state.value in {"RISK_OFF", "CRISIS"}:
            account.candidate_tenure[arm_key] = 0
            account.candidate_tenure[streak_key] = 0
            return False
        credible = sum(
            item.mature
            and item.confidence >= self.cfg.leader_min_confidence
            and item.score >= self.cfg.leader_cycle_min_score
            for item in leaders.values()
        )
        impulse_leader = any(
            item.mature
            and item.confidence >= self.cfg.leader_min_confidence
            and item.score >= self.cfg.leader_mature_score
            for item in leaders.values()
        )
        evidence_map = risk.evidence
        market_aligned = bool(
            account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
            or min(
                float(evidence_map.get("broad_ret120", -math.inf)),
                float(evidence_map.get("tech_ret120", -math.inf)),
            )
            >= self.cfg.leader_cycle_min_market_ret120
        )
        impulse = (
            market_aligned
            and
            opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
            and impulse_leader
            and risk.votes <= 1
            and float(evidence_map.get("ai_fast_return", -math.inf))
            >= self.cfg.leader_cycle_impulse_return
            and float(evidence_map.get("declining_ratio", 1.0))
            <= self.cfg.leader_cycle_impulse_breadth
            and float(evidence_map.get("below_ma20_ratio", 1.0))
            <= self.cfg.leader_cycle_impulse_breadth
            and max(
                float(evidence_map.get("tech_speed", -math.inf)),
                float(evidence_map.get("broad_speed", -math.inf)),
            )
            >= self.cfg.leader_cycle_impulse_index_return
        )
        evidence = (
            market_aligned
            and
            opportunity is Opportunity.STRONG_TREND
            and risk.votes <= 1
            and credible >= self.cfg.leader_cycle_min_mature
        )
        account.candidate_tenure[streak_key] = (
            account.candidate_tenure.get(streak_key, 0) + 1 if evidence else 0
        )
        if (
            account.candidate_tenure[streak_key]
            >= self.cfg.leader_cycle_confirm_days
            or impulse
        ):
            account.candidate_tenure[arm_key] = 1
        return account.candidate_tenure.get(arm_key, 0) == 1

    @staticmethod
    def _retention_score(
        symbol: str,
        leaders: dict[str, LeaderScore],
        account: AccountState,
    ) -> float:
        """Protect proven winners when K contracts or a challenger appears."""
        position = account.positions.get(symbol)
        if position is None:
            return leaders[symbol].score
        peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
        winner_bonus = min(0.20, 0.50 * max(0.0, peak_mfe))
        return leaders[symbol].score + winner_bonus

    def _leader_targets(
        self,
        *,
        date: pd.Timestamp,
        opportunity: Opportunity,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        weights_now: dict[str, float],
        prices: dict[str, float],
    ) -> tuple[Target, ...] | None:
        if risk.state.value == "CRISIS":
            return None
        ranked = sorted(
            (
                item
                for item in leaders.values()
                if item.mature
                and item.confidence >= self.cfg.leader_min_confidence
                and self._structure_ok(user_panel[item.symbol], date)
            ),
            key=lambda item: (-item.score, item.symbol),
        )
        emerging = sorted(
            (
                item
                for item in leaders.values()
                if item.emerging
                and self._structure_ok(user_panel[item.symbol], date)
            ),
            key=lambda item: (-item.score, item.symbol),
        )
        held_symbols = {symbol for symbol, position in account.positions.items() if position.shares > 0}
        target_k = self._dynamic_k(
            date=date,
            opportunity=opportunity,
            risk=risk,
            candidates=ranked,
            user_panel=user_panel,
            account=account,
        )
        reasons: dict[str, str] = {}
        lifecycles: dict[str, Lifecycle] = {}
        active = [
            symbol
            for symbol in account.active_leaders
            if symbol in held_symbols and symbol in leaders
        ]
        for symbol in sorted(held_symbols - set(active)):
            position = account.positions[symbol]
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index or symbol not in leaders:
                continue
            row = frame.loc[date]
            proven_winner = (
                position.highest_close / max(position.avg_cost, 1e-12) - 1.0
                >= 0.10
                and prices[symbol] >= position.avg_cost
                and scalar(row, "close")
                >= scalar(row, f"ma{self.cfg.trend_medium}")
            )
            if proven_winner:
                active.append(symbol)
                reasons[symbol] = "proven mature winner retained across rank drift"
        for symbol in sorted(held_symbols):
            position = account.positions[symbol]
            leader = leaders.get(symbol)
            if (
                position.lifecycle == Lifecycle.RECOVERY.value
                and leader is not None
                and leader.score >= self.cfg.leader_mature_score
                and leader.confidence >= self.cfg.leader_min_confidence
                and self._structure_ok(user_panel[symbol], date)
            ):
                if symbol not in active:
                    active.append(symbol)
                position.lifecycle = Lifecycle.CORE.value
                for tranche in position.tranches:
                    tranche.lifecycle = Lifecycle.CORE.value
                reasons[symbol] = "repaired recovery position graduated to core"
        stable_k = max(0, min(account.dynamic_k, self.cfg.max_positions))
        if stable_k and len(active) > stable_k:
            proven: set[str] = set()
            for symbol in active:
                position = account.positions.get(symbol)
                if position is None:
                    continue
                proven_winner = (
                    position.highest_close / max(position.avg_cost, 1e-12) - 1.0
                    >= 0.10
                    and prices[symbol] / max(position.avg_cost, 1e-12) - 1.0 >= 0
                    and scalar(user_panel[symbol].loc[date], "close")
                    >= scalar(
                        user_panel[symbol].loc[date],
                        f"ma{self.cfg.trend_medium}",
                    )
                )
                if proven_winner:
                    proven.add(symbol)
            keep_count = max(stable_k, len(proven))
            ranked_retention = sorted(
                active,
                key=lambda symbol: (
                    -self._retention_score(symbol, leaders, account),
                    symbol,
                ),
            )
            retained = sorted(
                proven,
                key=lambda symbol: (
                    -self._retention_score(symbol, leaders, account),
                    symbol,
                ),
            )
            retained.extend(
                symbol
                for symbol in ranked_retention
                if symbol not in proven
            )
            retained = retained[:keep_count]
            for symbol in set(active) - set(retained):
                reasons[symbol] = "dynamic K contraction after hysteresis"
            active = retained
        available_ranked = [item for item in ranked if item.symbol not in active]
        while (
            len(active) < target_k
            and available_ranked
            and risk.state.value != "RISK_OFF"
            and opportunity is not Opportunity.RECOVERY
        ):
            item = max(
                available_ranked,
                key=lambda candidate: (
                    self._admission_utility(
                        candidate=candidate,
                        active=active,
                        leaders=leaders,
                        user_panel=user_panel,
                        date=date,
                        account=account,
                    ),
                    candidate.score,
                    candidate.symbol,
                ),
            )
            active.append(item.symbol)
            available_ranked.remove(item)

        clock = next(iter(user_panel.values()))
        rotation_transfers: dict[str, float] = {}
        if active and len(active) >= target_k and ranked and self._rotation_allowed(account, date, clock):
            challenger = next((item for item in ranked if item.symbol not in active), None)
            weakest = min(
                active,
                key=lambda symbol: (
                    self._retention_score(symbol, leaders, account),
                    symbol,
                ),
            )
            weakest_frame = user_panel[weakest]
            weakest_row = weakest_frame.loc[date]
            old_structure_broken = (
                scalar(weakest_row, "close") < scalar(weakest_row, f"ma{self.cfg.trend_fast}")
                and scalar(weakest_row, f"ret{self.cfg.trend_fast}", 0.0) < 0
            )
            old_position = account.positions.get(weakest)
            held_sessions = (
                len(weakest_frame.loc[pd.Timestamp(old_position.entry_date) : date])
                if old_position is not None and old_position.entry_date
                else 0
            )
            if challenger is not None and old_position is not None:
                peak_mfe = (
                    old_position.highest_close / max(old_position.avg_cost, 1e-12)
                    - 1.0
                )
                winner_penalty = min(0.20, 0.50 * max(0.0, peak_mfe))
                same_cluster_penalty = (
                    0.15 if challenger.industry == leaders[weakest].industry else 0.0
                )
                uncertainty_penalty = 0.05 * max(0.0, 1.0 - challenger.confidence)
                edge = (
                    challenger.score
                    - leaders[weakest].score
                    - 0.01
                    - winner_penalty
                    - same_cluster_penalty
                    - uncertainty_penalty
                    + (0.08 if old_structure_broken else 0.0)
                )
                key = f"{weakest}->{challenger.symbol}"
                account.replacement_tenure[key] = (
                    account.replacement_tenure.get(key, 0) + 1
                    if edge >= self.cfg.replacement_edge and old_structure_broken
                    else 0
                )
                if (
                    account.replacement_tenure[key] >= self.cfg.replacement_confirm_days
                    and held_sessions >= self.cfg.min_hold_days
                ):
                    active.remove(weakest)
                    active.append(challenger.symbol)
                    rotation_transfers[challenger.symbol] = min(
                        self.cfg.max_symbol_weight,
                        self.cfg.replacement_transfer_cap,
                        weights_now.get(weakest, 0.0),
                    )
                    account.rotation_dates.append(str(date.date()))
                    account.replacement_events.append(
                        {
                            "signal_date": str(date.date()),
                            "old_symbol": weakest,
                            "new_symbol": challenger.symbol,
                            "old_close": prices[weakest],
                            "new_close": prices[challenger.symbol],
                            "edge": edge,
                        }
                    )
                    reasons[weakest] = f"rotation exit: {challenger.symbol} confirmed edge"
                    reasons[challenger.symbol] = f"rotation entry: replaces {weakest}"
                    lifecycles[challenger.symbol] = Lifecycle.CORE
                    account.replacement_tenure[key] = 0

        # A leader can graduate to cash when no credible replacement exists.
        # This is a lifecycle exit, not a second risk controller: it requires
        # persistent loss of both maturity and price structure.
        for symbol in list(active):
            frame = user_panel[symbol]
            row = frame.loc[date]
            position = account.positions.get(symbol)
            peak_mfe = (
                position.highest_close / max(position.avg_cost, 1e-12) - 1.0
                if position is not None
                else 0.0
            )
            protected_winner = peak_mfe >= 0.20
            broken = (
                not leaders[symbol].mature
                and scalar(row, "close")
                < scalar(
                    row,
                    f"ma{self.cfg.trend_medium if protected_winner else self.cfg.trend_fast}",
                )
                and scalar(row, f"ret{self.cfg.trend_fast}", 0.0)
                <= (-0.15 if protected_winner else -0.08)
            )
            key = f"lifecycle_exit:{symbol}"
            account.replacement_tenure[key] = (
                account.replacement_tenure.get(key, 0) + 1 if broken else 0
            )
            held_sessions = (
                len(frame.loc[pd.Timestamp(position.entry_date) : date])
                if position is not None and position.entry_date
                else 0
            )
            if (
                account.replacement_tenure[key] >= self.cfg.replacement_confirm_days
                and held_sessions >= self.cfg.min_hold_days
            ):
                active.remove(symbol)
                reasons[symbol] = "leader lifecycle exit: confirmed structural deterioration"

        account.active_leaders = sorted(set(active), key=lambda symbol: (-leaders[symbol].score, symbol))
        proposed = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in account.active_leaders
            if weights_now.get(symbol, 0.0) > 0
        }
        new_core = [symbol for symbol in account.active_leaders if symbol not in proposed]
        gross_cap = min(
            risk.target_gross_cap,
            self.cfg.strong_trend_gross
            if opportunity is Opportunity.STRONG_TREND
            else self.cfg.trend_target_gross
            if opportunity is Opportunity.TREND
            else self.cfg.choppy_target_gross,
        )
        projected_industry_cap = (
            gross_cap
            if account.candidate_tenure.get("evidence_concentration", 0) == 1
            else self.cfg.industry_weight_cap
        )
        satellite_reserve = sum(
            weights_now.get(symbol, 0.0)
            for symbol, position in account.positions.items()
            if position.shares > 0
            and position.lifecycle == Lifecycle.SATELLITE.value
            and symbol not in proposed
        )
        if not proposed and new_core:
            entry_gross = min(
                max(0.0, gross_cap - satellite_reserve),
                self.cfg.trend_entry_gross,
            )
            raw = np.array([max(0.01, leaders[symbol].score) for symbol in new_core], dtype=float)
            raw /= raw.sum()
            for symbol, share in zip(new_core, raw, strict=True):
                entry_cap = (
                    self.cfg.single_core_entry_cap
                    if len(new_core) == 1
                    else self.cfg.max_symbol_weight
                )
                proposed[symbol] = min(entry_cap, entry_gross * float(share))
                lifecycles[symbol] = Lifecycle.CORE
                reasons.setdefault(symbol, "confirmed mature leader core")
            for industry in {leaders[symbol].industry for symbol in proposed}:
                members = [
                    symbol
                    for symbol in proposed
                    if leaders[symbol].industry == industry
                ]
                industry_weight = sum(proposed[symbol] for symbol in members)
                if industry != "unknown" and industry_weight > projected_industry_cap:
                    scale = projected_industry_cap / industry_weight
                    for symbol in members:
                        proposed[symbol] *= scale
        elif new_core:
            available = max(
                0.0,
                gross_cap - satellite_reserve - sum(proposed.values()),
            )
            allocation = min(
                self.cfg.core_admission_weight,
                available / len(new_core) if new_core else 0.0,
            )
            for symbol in new_core:
                industry = leaders[symbol].industry
                industry_weight = sum(
                    weight
                    for held, weight in proposed.items()
                    if leaders[held].industry == industry
                )
                admitted = min(
                    rotation_transfers.get(symbol, allocation),
                    available,
                    max(0.0, projected_industry_cap - industry_weight),
                )
                if admitted > 0:
                    proposed[symbol] = admitted
                    available = max(0.0, available - admitted)
                    lifecycles[symbol] = Lifecycle.CORE
                    reasons.setdefault(symbol, "confirmed mature leader admission")
            for symbol in new_core:
                if symbol in proposed:
                    continue
                industry = leaders[symbol].industry
                members = [
                    item
                    for item in account.active_leaders
                    if leaders[item].industry == industry
                ]
                incumbents = [item for item in members if item in proposed]
                industry_weight = sum(proposed[item] for item in incumbents)
                if industry == "unknown" or not incumbents or industry_weight <= 0:
                    continue
                scores = np.array(
                    [max(0.01, leaders[item].score) for item in members],
                    dtype=float,
                )
                scores /= scores.sum()
                redistributed = min(industry_weight, projected_industry_cap)
                for item, share in zip(members, scores, strict=True):
                    proposed[item] = min(
                        self.cfg.max_symbol_weight,
                        redistributed * float(share),
                    )
                lifecycles[symbol] = Lifecycle.CORE
                reasons[symbol] = "dynamic K expansion within industry cap"

        available = max(
            0.0,
            gross_cap - satellite_reserve - sum(proposed.values()),
        )
        index_chase = (
            max(
                float(risk.evidence.get("broad_ret5", 0.0)),
                float(risk.evidence.get("tech_ret5", 0.0)),
            )
            >= self.cfg.add_index_chase_ret5
        )
        for symbol in list(account.active_leaders):
            position = account.positions.get(symbol)
            if position is None or available < self.cfg.min_trade_weight:
                continue
            add_cooldown_complete = self._add_cooldown_complete(
                account=account,
                frame=user_panel[symbol],
                date=date,
                cooldown_sessions=self.cfg.add_tranche_cooldown_sessions,
            )
            mfe = prices[symbol] / max(position.avg_cost, 1e-12) - 1.0
            industry = leaders[symbol].industry
            industry_weight = sum(
                weight
                for held, weight in proposed.items()
                if leaders[held].industry == industry
            )
            industry_room = max(0.0, projected_industry_cap - industry_weight)
            if (
                position.lifecycle == Lifecycle.CORE.value
                and add_cooldown_complete
                and not index_chase
                and mfe >= self.cfg.add1_min_mfe
                and risk.state.value in {"NORMAL", "CAUTION"}
                and opportunity is not Opportunity.RECOVERY
                and proposed[symbol] < self.cfg.max_symbol_weight
            ):
                increment = min(
                    self.cfg.add1_weight,
                    available,
                    industry_room,
                    self.cfg.max_symbol_weight - proposed[symbol],
                )
                if increment <= 1e-12:
                    continue
                proposed[symbol] = min(self.cfg.max_symbol_weight, proposed[symbol] + increment)
                available = max(
                    0.0,
                    gross_cap - satellite_reserve - sum(proposed.values()),
                )
                lifecycles[symbol] = Lifecycle.ADD1
                reasons[symbol] = "ADD1: positive MFE with normal risk"
            elif (
                position.lifecycle == Lifecycle.ADD1.value
                and add_cooldown_complete
                and not index_chase
                and mfe >= self.cfg.add2_min_mfe
                and opportunity is Opportunity.STRONG_TREND
                and risk.state.value == "NORMAL"
                and proposed[symbol] < self.cfg.max_symbol_weight
            ):
                increment = min(
                    self.cfg.add2_weight,
                    available,
                    industry_room,
                    self.cfg.max_symbol_weight - proposed[symbol],
                )
                if increment <= 1e-12:
                    continue
                proposed[symbol] = min(self.cfg.max_symbol_weight, proposed[symbol] + increment)
                available = max(
                    0.0,
                    gross_cap - satellite_reserve - sum(proposed.values()),
                )
                lifecycles[symbol] = Lifecycle.ADD2
                reasons[symbol] = "ADD2: high-confidence trend continuation"

        satellites_now = [
            symbol
            for symbol, position in account.positions.items()
            if position.lifecycle == Lifecycle.SATELLITE.value and position.shares > 0
        ]
        for symbol in satellites_now:
            position = account.positions[symbol]
            held = len(user_panel[symbol].loc[pd.Timestamp(position.entry_date) : date])
            if leaders.get(symbol) and leaders[symbol].mature:
                proposed[symbol] = weights_now.get(symbol, 0.0)
                lifecycles[symbol] = Lifecycle.CORE
                reasons[symbol] = "satellite promoted to mature core"
                if symbol not in account.active_leaders:
                    account.active_leaders.append(symbol)
            elif held <= self.cfg.emerging_expiry_days and self._structure_ok(user_panel[symbol], date):
                proposed[symbol] = weights_now.get(symbol, 0.0)
                lifecycles[symbol] = Lifecycle.SATELLITE
                reasons[symbol] = "emerging leader satellite observation"
            else:
                reasons[symbol] = "satellite expiry or failed confirmation"
                account.satellite_entry_dates.pop(symbol, None)
        if (
            risk.state.value == "NORMAL"
            and opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
            and len(proposed) < self.cfg.max_positions
        ):
            slots = min(
                self.cfg.max_satellites - len(satellites_now),
                self.cfg.max_positions - len(proposed),
            )
            for item in emerging:
                if slots <= 0 or item.symbol in proposed:
                    break
                if sum(proposed.values()) + self.cfg.satellite_weight > gross_cap:
                    break
                industry_weight = sum(
                    weight
                    for held, weight in proposed.items()
                    if leaders[held].industry == item.industry
                )
                if industry_weight + self.cfg.satellite_weight > projected_industry_cap:
                    continue
                proposed[item.symbol] = self.cfg.satellite_weight
                lifecycles[item.symbol] = Lifecycle.SATELLITE
                reasons[item.symbol] = "emerging leader satellite probe"
                account.satellite_entry_dates[item.symbol] = str(date.date())
                slots -= 1

        if not proposed and not held_symbols:
            return None
        for symbol in held_symbols - set(proposed):
            reasons.setdefault(symbol, "confirmed leader deterioration")
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="mature leader lifecycle",
            lifecycles=lifecycles,
            reasons=reasons,
        )

    def _recovery_anchor_substitution(
        self,
        *,
        date: pd.Timestamp,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        weights_now: dict[str, float],
        anchor_elapsed: int,
    ) -> tuple[Target, ...] | None:
        """Replace a broken secondary in an incomplete recovery cohort.

        The lead anchor remains sticky.  A secondary can rotate only after its
        own price structure is broken and a liquid, mature challenger has held
        a material score edge for the normal replacement confirmation period.
        """
        if account.candidate_tenure.get("recovery_substitution_pending", 0) == 1:
            missing = {
                symbol: weight
                for symbol, weight in account.anchor_weights.items()
                if weights_now.get(symbol, 0.0) <= 0
            }
            if missing:
                proposed = {
                    symbol: weights_now.get(symbol, 0.0)
                    for symbol in account.anchor_weights
                    if weights_now.get(symbol, 0.0) > 0
                }
                proposed.update(missing)
                return self._targets(
                    proposed=proposed,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.CORE,
                    reason="confirmed recovery anchor substitution",
                )
            account.candidate_tenure["recovery_substitution_pending"] = 0

        if (
            len(account.anchor_weights) != 2
            or len(
                set(account.anchor_weights)
                & set(self.cfg.strategic_cohort_symbols)
            )
            != 2
            or not set(self.cfg.strategic_reserve_symbols).issubset(user_panel)
            or account.candidate_tenure.get("recovery_substitution_completed", 0)
            == 1
            or anchor_elapsed <= self.cfg.recovery_add_window_days
            or account.protected_weights
            or risk.state.value not in {"NORMAL", "CAUTION"}
        ):
            return None
        lead, incumbent = tuple(account.anchor_weights)
        if weights_now.get(lead, 0.0) <= 0 or weights_now.get(incumbent, 0.0) <= 0:
            return None
        incumbent_score = leaders.get(incumbent)
        incumbent_frame = user_panel.get(incumbent)
        if (
            incumbent_score is None
            or incumbent_frame is None
            or date not in incumbent_frame.index
        ):
            return None
        incumbent_row = incumbent_frame.loc[date]
        structure_broken = (
            scalar(incumbent_row, "close")
            < scalar(incumbent_row, f"ma{self.cfg.trend_fast}")
            and scalar(incumbent_row, f"ret{self.cfg.trend_fast}", 0.0) < 0
        )
        sessions_since_shock = math.inf
        if account.last_shock_date:
            sessions_since_shock = (
                len(incumbent_frame.loc[pd.Timestamp(account.last_shock_date) : date])
                - 1
            )
        broken = (
            not incumbent_score.mature
            and (
                structure_broken
                or sessions_since_shock
                <= self.cfg.recovery_substitution_shock_window
            )
        )
        challengers = [
            item
            for item in leaders.values()
            if item.symbol not in account.anchor_weights
            and item.symbol in self.cfg.strategic_reserve_symbols
            and credible_recovery_reserve(
                score=item,
                frame=user_panel[item.symbol],
                date=date,
                occupied_industries={
                    leaders[symbol].industry
                    for symbol in account.anchor_weights
                    if symbol in leaders
                },
                cfg=self.cfg,
            )
        ]
        challenger = max(
            challengers,
            key=lambda item: (item.score, item.symbol),
            default=None,
        )
        edge = (
            challenger.score - incumbent_score.score
            if challenger is not None
            else -math.inf
        )
        key = (
            f"recovery_substitution:{incumbent}->{challenger.symbol}"
            if challenger is not None
            else f"recovery_substitution:{incumbent}->none"
        )
        confirmed = broken and edge >= self.cfg.recovery_substitution_edge
        account.replacement_tenure[key] = (
            account.replacement_tenure.get(key, 0) + 1 if confirmed else 0
        )
        if (
            challenger is None
            or account.replacement_tenure[key] < self.cfg.replacement_confirm_days
        ):
            return None
        transfer = min(
            self.cfg.max_symbol_weight,
            max(
                account.anchor_weights.get(incumbent, 0.0),
                weights_now.get(incumbent, 0.0),
            ),
        )
        account.anchor_weights = {
            lead: account.anchor_weights[lead],
            challenger.symbol: transfer,
        }
        account.candidate_tenure["recovery_substitution_pending"] = 1
        account.candidate_tenure["recovery_substitution_completed"] = 1
        account.rotation_dates.append(str(date.date()))
        account.replacement_events.append(
            {
                "signal_date": str(date.date()),
                "old_symbol": incumbent,
                "new_symbol": challenger.symbol,
                "old_close": scalar(incumbent_row, "close"),
                "new_close": scalar(user_panel[challenger.symbol].loc[date], "close"),
                "edge": edge,
                "route": "recovery_anchor_substitution",
            }
        )
        return self._targets(
            proposed={
                lead: weights_now[lead],
                challenger.symbol: transfer,
            },
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="confirmed recovery anchor substitution",
            reasons={
                incumbent: f"recovery anchor exit: {challenger.symbol} confirmed edge",
                challenger.symbol: f"recovery anchor entry: replaces {incumbent}",
            },
        )

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
        """Apply the risk engine's gross cap to every strategy return path."""
        try:
            targets = self._allocate_strategy(
                date=date,
                opportunity=opportunity,
                risk=risk,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
                prices=prices,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"portfolio allocation failed on {date.date()} "
                f"for opportunity={opportunity.value}, risk={risk.state.value}: {exc}"
            ) from exc
        gross_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
        target_gross = sum(item.weight for item in targets if item.weight > 0)
        weights_now, _ = current_weights(account, prices)
        current_gross = sum(weight for weight in weights_now.values() if weight > 0)
        if target_gross <= gross_cap + 1e-12:
            if current_gross <= gross_cap + 1e-12:
                return targets
            return tuple(
                replace(
                    target,
                    reason=(
                        f"portfolio risk gross cap; {target.reason}"
                        if target.weight + 1e-12
                        < weights_now.get(target.symbol, 0.0)
                        and target.reason
                        in {
                            "mature anchored leader",
                            "causal crash-recovery leader",
                        }
                        and "portfolio risk gross cap" not in target.reason
                        else target.reason
                    ),
                )
                for target in targets
            )
        scale = gross_cap / target_gross if target_gross > 0 else 0.0
        capped: list[Target] = []
        for target in targets:
            weight = target.weight * scale
            reason = target.reason
            if weight + 1e-12 < weights_now.get(target.symbol, 0.0):
                reason = f"portfolio risk gross cap; {reason}"
            capped.append(replace(target, weight=weight, reason=reason))
        if sum(item.weight for item in capped if item.weight > 0) > gross_cap + 1e-8:
            raise RuntimeError("allocator failed to enforce risk gross cap")
        return tuple(capped)

    def _allocate_strategy(
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
        strategic = self._strategic_cohort_targets(
            date=date,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            prices=prices,
            weights_now=weights_now,
        )
        if strategic is not None:
            return strategic
        leader_cycle_armed = self._update_leader_cycle_arm(
            opportunity=opportunity,
            risk=risk,
            leaders=leaders,
            account=account,
        )
        cooldown = account.candidate_tenure.get("tactical_cooldown", 0)
        if cooldown > 0:
            account.candidate_tenure["tactical_cooldown"] = cooldown - 1

        tactical = (
            [
                position
                for position in account.positions.values()
                if position.shares > 0
                and position.lifecycle == Lifecycle.RECOVERY.value
                and (
                    not account.tactical_anchor_symbol
                    or position.symbol == account.tactical_anchor_symbol
                )
            ]
            if account.candidate_tenure.get("tactical_active", 0) == 1
            else []
        )
        promotable_tactical = bool(
            tactical
            and not account.anchor_weights
            and account.candidate_tenure.get("tactical_promotable", 0) == 1
            and account.tactical_anchor_symbol == tactical[0].symbol
        )
        if promotable_tactical and opportunity is Opportunity.RECOVERY:
            position = tactical[0]
            # A deep-crisis probe that survives until causal recovery confirmation
            # becomes the first core tranche.  Keeping the same shares avoids the
            # economically pointless sell/rebuy pair that previously inflated
            # account orders and discarded the best entry price.
            account.anchor_weights = {
                position.symbol: weights_now.get(position.symbol, 0.0)
            }
            account.recovery_anchor_date = str(date.date())
            account.candidate_tenure["recovery_reserve_qualified"] = 0
            account.candidate_tenure["recovery_substitution_pending"] = 0
            account.candidate_tenure["recovery_substitution_completed"] = 0
            account.candidate_tenure["tactical_active"] = 0
            account.candidate_tenure["tactical_promoted"] = 1
            position.lifecycle = Lifecycle.CORE.value
            for tranche in position.tranches:
                tranche.lifecycle = Lifecycle.CORE.value
        tactical_leader_graduation = bool(
            tactical
            and not account.anchor_weights
            and risk.state.value in {"NORMAL", "CAUTION"}
            and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
            and tactical[0].symbol in leaders
            and leaders[tactical[0].symbol].mature
            and leaders[tactical[0].symbol].confidence
            >= self.cfg.leader_min_confidence
            and self._structure_ok(user_panel[tactical[0].symbol], date)
        )
        if tactical_leader_graduation:
            position = tactical[0]
            account.candidate_tenure["tactical_active"] = 0
            account.candidate_tenure["tactical_promoted"] = 1
            account.tactical_anchor_symbol = ""
            if position.symbol not in account.active_leaders:
                account.active_leaders.append(position.symbol)
            position.lifecycle = Lifecycle.CORE.value
            for tranche in position.tranches:
                tranche.lifecycle = Lifecycle.CORE.value
            tactical = []
        if (
            tactical
            and not account.anchor_weights
            and risk.state.value != "CRISIS"
            and not (account.protected_weights and risk.shock_state == "RECOVERY")
        ):
            position = tactical[0]
            pnl = prices.get(position.symbol, 0.0) / max(position.avg_cost, 1e-12) - 1.0
            held_sessions = len(user_panel[position.symbol].loc[pd.Timestamp(position.entry_date) : date])
            exit_due = (
                held_sessions >= 30
                if promotable_tactical
                else pnl >= self.cfg.tactical_rebound_take_profit or held_sessions >= 12
            )
            if exit_due:
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
            if account.anchor_weights:
                account.candidate_tenure["risk_trimmed"] = 1
            gross_now = sum(weights_now.values())
            proposed = (
                {
                    symbol: weight * risk.target_gross_cap / gross_now
                    for symbol, weight in weights_now.items()
                    if weight > 0
                }
                if gross_now > 0 and risk.target_gross_cap > 0
                else {}
            )
            return self._targets(
                proposed=proposed,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason=(
                    "severe crisis capital protection"
                    if risk.target_gross_cap <= 0
                    else "graded crisis risk reduction"
                ),
            )

        if account.protected_weights and risk.shock_state == "RECOVERY":
            account.candidate_tenure["post_shock_recovery"] = int(
                account.shock_severity == "SEVERE"
            )
            proposed = {
                symbol: min(self.cfg.max_symbol_weight, weight)
                for symbol, weight in account.protected_weights.items()
                if symbol in user_panel
            }
            total = sum(proposed.values())
            cap = min(self.cfg.recovery_target_gross, risk.target_gross_cap)
            if account.shock_severity == "SEVERE":
                cap = min(cap, self.cfg.severe_recovery_gross)
            elif account.shock_severity == "CONCENTRATED":
                cap = min(cap, self.cfg.concentrated_recovery_gross)
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
        weak_secular_market = max(
            float(risk.evidence.get("broad_ret120", 0.0)),
            float(risk.evidence.get("tech_ret120", 0.0)),
        ) <= self.cfg.recovery_cohort_weak_market_ret120
        graduation_days = (
            self.cfg.recovery_cohort_weak_graduation_days
            if weak_secular_market
            else self.cfg.recovery_cohort_graduation_days
        )
        graduation_ready = (
            bool(anchored_held)
            and account.candidate_tenure.get("recovery_cohort_graduated", 0) == 0
            and anchor_elapsed >= graduation_days
            and risk.state.value in {"NORMAL", "CAUTION"}
            and opportunity
            in {Opportunity.CHOPPY, Opportunity.TREND, Opportunity.STRONG_TREND}
            and (
                leader_cycle_armed
                or (
                    account.candidate_tenure.get("recovery_cohort_locked", 0)
                    == 1
                    and account.candidate_tenure.get("tactical_promoted", 0)
                    == 0
                )
            )
        )
        if graduation_ready:
            account.active_leaders = sorted(
                anchored_held,
                key=lambda symbol: (-leaders[symbol].score, symbol),
            )
            account.anchor_weights.clear()
            account.recovery_anchor_date = ""
            account.candidate_tenure["recovery_cohort_locked"] = 0
            account.candidate_tenure["recovery_cohort_graduated"] = 1
            account.candidate_tenure["diversification_capped"] = 0
            account.candidate_tenure["confirmed_anchor_pair"] = 0
            anchored_held = {}
        substitution = self._recovery_anchor_substitution(
            date=date,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            anchor_elapsed=anchor_elapsed,
        )
        if substitution is not None:
            return substitution
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

        # A recovery label must not evict a healthy trend core. During a
        # possible V-repair it freezes new risk and lets the existing lifecycle
        # continue; recovery-cohort construction is reserved for an empty book
        # or an already established strategic anchor.
        has_general_core = (
            not account.anchor_weights
            and any(
                position.shares > 0
                and position.lifecycle
                in {
                    Lifecycle.CORE.value,
                    Lifecycle.ADD1.value,
                    Lifecycle.ADD2.value,
                    Lifecycle.SATELLITE.value,
                }
                for position in account.positions.values()
            )
        )
        if opportunity is Opportunity.RECOVERY and has_general_core:
            recovery_hold = self._leader_targets(
                date=date,
                opportunity=opportunity,
                risk=risk,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
                prices=prices,
            )
            if recovery_hold is not None:
                return recovery_hold

        if (
            not account.positions
            and not account.anchor_weights
            and account.candidate_tenure.get("tactical_active", 0) == 0
            and account.candidate_tenure.get("tactical_cooldown", 0) == 0
            and opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}
            and risk.state.value in {"NORMAL", "CAUTION"}
        ):
            deep_recovery: list[tuple[LeaderScore, float, float]] = []
            rebound: list[LeaderScore] = []
            fast_rebound: list[tuple[LeaderScore, float, float]] = []
            required_notional = (
                account.initial_cash * self.cfg.tactical_rebound_weight * 0.90
            )
            for symbol, score in leaders.items():
                if symbol not in user_panel or date not in user_panel[symbol].index:
                    continue
                row = user_panel[symbol].loc[date]
                close = scalar(row, "close")
                ma120 = scalar(row, f"ma{self.cfg.trend_slow}")
                ret5 = scalar(row, "ret5", -1.0)
                ret20 = scalar(row, f"ret{self.cfg.trend_fast}", -1.0)
                ret120 = scalar(row, f"ret{self.cfg.trend_slow}", 0.0)
                ret1 = float(
                    user_panel[symbol]
                    .loc[:date, "close"]
                    .pct_change(fill_method=None)
                    .iloc[-1]
                )
                if (
                    ret120 <= -0.35
                    and ret20 >= -0.12
                    and ret5 >= -0.06
                    and ret1 <= -0.05
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(
                        user_panel[symbol], date, required_notional
                    )
                ):
                    deep_recovery.append((score, ret20, ret120))
                if (
                    ret20 <= -0.15
                    and math.isfinite(close)
                    and math.isfinite(ma120)
                    and close >= ma120
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(
                        user_panel[symbol], date, required_notional
                    )
                ):
                    rebound.append(score)
                if (
                    account.candidate_tenure.get("fast_v_recovery", 0) == 1
                    and ret5 >= 0.10
                    and ret20 < 0
                    and math.isfinite(close)
                    and math.isfinite(ma120)
                    and close >= ma120
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(
                        user_panel[symbol], date, required_notional
                    )
                ):
                    fast_rebound.append((score, ret5, ret20))
            if len(deep_recovery) < 2:
                deep_recovery = [
                    item
                    for item in deep_recovery
                    if item[0].symbol in self.cfg.strategic_cohort_symbols
                ]
            if deep_recovery or rebound or fast_rebound:
                if fast_rebound:
                    pick = max(
                        fast_rebound,
                        key=lambda item: (
                            item[1],
                            item[2],
                            item[0].score,
                            item[0].symbol,
                        ),
                    )[0]
                    account.candidate_tenure["tactical_promotable"] = 1
                    account.tactical_anchor_symbol = pick.symbol
                elif deep_recovery:
                    # Recovery probes are meant to capture convexity after a
                    # genuine crash. Rank by observable crash depth, then by
                    # stabilization and leader quality; no future price enters.
                    pick = max(
                        deep_recovery,
                        key=lambda item: (
                            -item[2],
                            item[1],
                            item[0].score,
                            item[0].symbol,
                        ),
                    )[0]
                    account.candidate_tenure["tactical_promotable"] = 1
                    account.tactical_anchor_symbol = pick.symbol
                else:
                    pick = max(rebound, key=lambda item: (item.score, item.symbol))
                    fast_v_candidate = (
                        account.candidate_tenure.get("fast_v_recovery", 0) == 1
                    )
                    account.candidate_tenure["tactical_promotable"] = int(
                        fast_v_candidate
                    )
                    account.tactical_anchor_symbol = (
                        pick.symbol if fast_v_candidate else ""
                    )
                account.candidate_tenure["tactical_active"] = 1
                return self._targets(
                    proposed={
                        pick.symbol: min(
                            self.cfg.tactical_rebound_weight,
                            risk.target_gross_cap,
                        )
                    },
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="controlled oversold rebound probe",
                )

        if opportunity is Opportunity.RECOVERY:
            proposed = dict(anchored_held)
            if account.candidate_tenure.get("recovery_cohort_locked", 0) == 1:
                missing = {
                    symbol: min(
                        self.cfg.max_symbol_weight,
                        max(0.0, target_weight),
                    )
                    for symbol, target_weight in account.anchor_weights.items()
                    if symbol not in proposed
                }
                gross_budget = min(
                    self.cfg.max_gross,
                    self.cfg.recovery_target_gross,
                    max(0.0, risk.target_gross_cap),
                )
                held_gross = sum(
                    min(self.cfg.max_symbol_weight, max(0.0, weight))
                    for weight in proposed.values()
                )
                requested = sum(missing.values())
                remaining = max(0.0, gross_budget - held_gross)
                scale = min(1.0, remaining / requested) if requested > 0 else 0.0
                proposed.update(
                    {
                        symbol: target_weight * scale
                        for symbol, target_weight in missing.items()
                        if target_weight * scale > 1e-12
                    }
                )
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
            admission_depth = (
                -0.15
                if deep_count >= 2 or (deep_count >= 1 and bool(account.anchor_weights))
                else -0.30
            )
            candidates = [
                item
                for item in candidates
                if crash_depth.get(item.symbol, 0.0) <= admission_depth
            ]
            if (
                weak_secular_market
                and not account.anchor_weights
                and (
                    len(candidates) < 2
                    or max(
                        float(risk.evidence.get("broad_ret60", -math.inf)),
                        float(risk.evidence.get("tech_ret60", -math.inf)),
                    )
                    < self.cfg.recovery_weak_market_min_index_ret60
                )
            ):
                candidates = []
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
                proposed = {
                    lead: min(
                        self.cfg.max_symbol_weight,
                        self.cfg.tactical_rebound_weight,
                        self.cfg.recovery_target_gross,
                    )
                }
                if len(secondaries) == 1:
                    # Reserve room for a third independently confirmed core.
                    # This prevents a two-name interim cohort from being bought
                    # to full gross and then immediately rebalanced when the
                    # third member confirms a day or two later.
                    proposed[secondaries[0]] = min(
                        0.20,
                        max(0.0, self.cfg.recovery_target_gross - proposed[lead]),
                    )
                if len(secondaries) >= 2:
                    secondary_weight = max(
                        0.0,
                        self.cfg.recovery_target_gross - proposed[lead],
                    ) / len(secondaries)
                    proposed.update(
                        {symbol: secondary_weight for symbol in secondaries}
                    )
                elif (
                    len(secondaries) == 1
                    and account.recovery_anchor_date
                    and recovery_elapsed > self.cfg.recovery_add_window_days
                ):
                    proposed[secondaries[0]] = max(
                        proposed[secondaries[0]],
                        self.cfg.recovery_target_gross - proposed[lead],
                    )
                    account.candidate_tenure["confirmed_anchor_pair"] = 1
                account.anchor_weights = dict(proposed)
                if len(selected) == 2 and all(
                    crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected
                ):
                    account.candidate_tenure["confirmed_anchor_pair"] = 1
                if len(selected) == min(3, self.cfg.max_positions) and all(
                    crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected
                ):
                    account.candidate_tenure["recovery_cohort_locked"] = 1
                if not account.recovery_anchor_date:
                    account.recovery_anchor_date = str(date.date())
                    account.candidate_tenure["recovery_reserve_qualified"] = 0
                    account.candidate_tenure["recovery_substitution_pending"] = 0
                    account.candidate_tenure["recovery_substitution_completed"] = 0
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

        if leader_cycle_armed:
            leader_targets = self._leader_targets(
                date=date,
                opportunity=opportunity,
                risk=risk,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
                weights_now=weights_now,
                prices=prices,
            )
            if leader_targets is not None:
                return leader_targets

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
