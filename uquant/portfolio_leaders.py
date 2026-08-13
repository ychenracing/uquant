"""Dynamic leader, industry hand-off, and lifecycle allocation policy."""

from __future__ import annotations

import math
from itertools import combinations
from typing import cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from .features import scalar
from .portfolio_core import effective_n
from .portfolio_strategic import StrategicPortfolioPolicy
from .types import AccountState, LeaderScore, Lifecycle, Opportunity, RiskAssessment, Target


class LeaderPortfolioPolicy(StrategicPortfolioPolicy):
    """Own dynamic K, admissions, additions, satellites, and leader rotation."""

    def _cap_opportunity_gross(
        self,
        *,
        proposed: dict[str, float],
        gross_cap: float,
        weights_now: dict[str, float],
        leaders: dict[str, LeaderScore],
        reasons: dict[str, str],
        opportunity: Opportunity,
    ) -> dict[str, float]:
        """Limit new opportunity risk without manufacturing incumbent sells.

        CHOPPY/WEAK are alpha-budget observations. Confirmed structural risk
        overlays own forced reductions. This distinction gives the continuous
        opportunity axis an economic hysteresis band: existing exposure may
        drift above the entry budget, while only proposed increments are
        sparsely removed.
        """
        capped = dict(proposed)
        increments = {
            symbol: max(0.0, weight - max(0.0, weights_now.get(symbol, 0.0)))
            for symbol, weight in capped.items()
        }
        baseline_total = sum(capped.values()) - sum(increments.values())
        allowed_total = max(gross_cap, baseline_total)
        excess = max(0.0, sum(max(0.0, value) for value in capped.values()) - allowed_total)
        if excess <= 1e-12:
            return capped
        symbols = tuple(sorted(symbol for symbol, weight in increments.items() if weight > 1e-12))
        feasible = [
            subset
            for size in range(1, len(symbols) + 1)
            for subset in combinations(symbols, size)
            if sum(increments[symbol] for symbol in subset) >= excess - 1e-12
        ]
        selected = min(
            feasible,
            key=lambda subset: (
                len(subset),
                sum(leaders[symbol].score if symbol in leaders else 0.0 for symbol in subset),
                -sum(increments[symbol] for symbol in subset),
                subset,
            ),
        )
        remaining = excess
        for symbol in sorted(
            selected,
            key=lambda item: (
                leaders[item].score if item in leaders else 0.0,
                -increments[item],
                item,
            ),
        ):
            reduction = min(increments[symbol], remaining)
            if reduction <= 1e-12:
                continue
            capped[symbol] = max(0.0, capped[symbol] - reduction)
            reasons[symbol] = f"{opportunity.value.lower()} opportunity gross contraction"
            remaining -= reduction
        if remaining > 1e-8:
            raise RuntimeError("leader opportunity cap could not be reconciled")
        return capped

    def _conviction_shares(
        self,
        symbols: list[str],
        leaders: dict[str, LeaderScore],
        *,
        evidence_qualified: bool,
    ) -> NDArray[np.float64]:
        """Map score dispersion only after the joint evidence gate passes."""
        scores: NDArray[np.float64] = np.asarray(
            [leaders[symbol].score for symbol in symbols],
            dtype=np.float64,
        )
        if len(scores) <= 1:
            return np.ones(len(scores), dtype=np.float64)
        if not evidence_qualified or not self.cfg.conviction_weighting_enabled or np.ptp(scores) < 0.03:
            return np.full(len(scores), 1.0 / len(scores), dtype=np.float64)
        weights: NDArray[np.float64] = np.asarray(
            np.exp(6.0 * (scores - float(scores.max()))),
            dtype=np.float64,
        )
        weights /= weights.sum()
        cap = self.cfg.max_symbol_weight
        # Capped-simplex projection redistributes excess instead of leaving
        # avoidable cash after a high-conviction first entry.
        for _ in range(len(weights) + 1):
            excess = float(np.maximum(weights - cap, 0.0).sum())
            weights = np.minimum(weights, cap)
            room = np.maximum(cap - weights, 0.0)
            if excess <= 1e-12 or float(room.sum()) <= 1e-12:
                break
            weights += excess * room / room.sum()
        return np.asarray(weights / weights.sum(), dtype=np.float64)

    def _conviction_evidence_qualified(
        self,
        *,
        symbols: list[str],
        leaders: dict[str, LeaderScore],
        user_panel: dict[str, pd.DataFrame],
        date: pd.Timestamp,
        high_confidence: bool,
    ) -> bool:
        """Require independent quality and diversification evidence to concentrate."""
        if not high_confidence or len(symbols) < 2:
            return False
        factor_floor = self.cfg.high_confidence_entry_breadth
        if any(
            leaders[symbol].components.get("resilience", 0.0) < factor_floor
            or leaders[symbol].components.get("relative_strength", 0.0) < factor_floor
            or leaders[symbol].components.get("liquidity", 0.0) < self.cfg.leader_min_confidence
            for symbol in symbols
        ):
            return False
        correlations = self._correlations(user_panel, symbols, date)
        pairwise: list[float] = []
        for index, left in enumerate(symbols):
            for right in symbols[index + 1 :]:
                if left not in correlations.index or right not in correlations.columns:
                    continue
                value = float(cast(float, correlations.at[left, right]))
                if math.isfinite(value):
                    pairwise.append(abs(value))
        return bool(pairwise and max(pairwise) <= self.cfg.risk_correlation)

    @staticmethod
    def _session_clock(
        user_panel: dict[str, pd.DataFrame],
        date: pd.Timestamp,
    ) -> pd.DatetimeIndex:
        """Return the deterministic union of all visible user sessions."""
        clock = pd.DatetimeIndex([])
        for frame in user_panel.values():
            sessions = pd.DatetimeIndex(frame.index)
            clock = clock.union(sessions[sessions <= date])
        return clock.sort_values()

    @staticmethod
    def _session_distance(
        clock: pd.DatetimeIndex,
        start: str | pd.Timestamp,
        end: pd.Timestamp,
    ) -> int:
        bounded = clock[(clock >= pd.Timestamp(start)) & (clock <= end)]
        return max(0, len(bounded) - 1)

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
        if risk.freeze_new_risk or risk.state.value in {"RISK_OFF", "CRISIS"}:
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
                and max(item.score for item in trial) - min(item.score for item in trial) <= 0.12
            )
            account.candidate_tenure["evidence_concentration"] = int(concentrated_conviction)
            if effective_n(equal, correlations) < 1.60 and not concentrated_conviction:
                target = 2
        target = max(0, target)
        if account.candidate_tenure.get("leader_cycle_staged_handoff", 0) == 1:
            # An empty book can retain stale K from an owner that has already
            # completed. The staged handoff owns a fresh K epoch and must not
            # inherit that breadth before any new position exists.
            account.dynamic_k = min(1, target)
            account.last_k_change_date = str(date.date())
            return account.dynamic_k
        if account.dynamic_k <= 0:
            account.dynamic_k = target
            account.last_k_change_date = str(date.date())
            return account.dynamic_k
        target_key = "dynamic_k_target"
        streak_key = "dynamic_k_target_streak"
        if account.candidate_tenure.get(target_key, -1) == target:
            account.candidate_tenure[streak_key] = account.candidate_tenure.get(streak_key, 0) + 1
        else:
            account.candidate_tenure[target_key] = target
            account.candidate_tenure[streak_key] = 1
        clock = self._session_clock(user_panel, date)
        elapsed = (
            self._session_distance(clock, account.last_k_change_date, date)
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

    def _rotation_allowed(
        self,
        account: AccountState,
        date: pd.Timestamp,
        user_panel: dict[str, pd.DataFrame],
    ) -> bool:
        clock = self._session_clock(user_panel, date)
        recent = [
            value
            for value in account.rotation_dates
            if pd.Timestamp(value) <= date and self._session_distance(clock, value, date) <= 20
        ]
        account.rotation_dates = recent
        return len(recent) < self.cfg.max_rotations_20d

    def _update_leader_cycle_arm(
        self,
        *,
        opportunity: Opportunity,
        risk: RiskAssessment,
        leaders: dict[str, LeaderScore],
        account: AccountState,
        strategic_handoff_blocked: bool = False,
        strategic_handoff_ready: bool = False,
    ) -> bool:
        """Require broad, persistent leader evidence before generic trend capital.

        Recovery anchors and the bounded tactical rebound have their own causal
        confirmations.  The ordinary mature-leader route is broader, so it is
        armed only after several high-confidence mature leaders coexist with a
        confirmed strong trend.  Account/market risk owns the disarm decision.
        """
        arm_key = "leader_cycle_armed"
        streak_key = "leader_cycle_evidence"
        if risk.freeze_new_risk or risk.state.value in {"RISK_OFF", "CRISIS"}:
            # Level-1 protection freezes additions, scouts, and rotations; it
            # is not an exit signal for an otherwise healthy live book.  Keep
            # the route reachable so _leader_targets can retain existing
            # active leaders while its own freeze checks block every new-risk
            # admission.  Higher risk states still disarm the cycle.
            live_active = [
                symbol
                for symbol in account.active_leaders
                if account.positions.get(symbol) is not None
                and account.positions[symbol].shares > 0
            ]
            independently_confirmed_live = bool(
                live_active
                and all(
                    symbol in leaders
                    and leaders[symbol].mature
                    and leaders[symbol].confidence >= self.cfg.leader_min_confidence
                    and leaders[symbol].score >= self.cfg.leader_cycle_min_score
                    for symbol in live_active
                )
            )
            if (
                risk.state.value in {"NORMAL", "CAUTION"}
                and independently_confirmed_live
            ):
                account.candidate_tenure[arm_key] = 1
                account.candidate_tenure[streak_key] = 0
                return True
            # A NORMAL level-1 capital freeze can be active while a valid
            # leader book temporarily has no active-leader label (for example
            # after an adaptive compatibility transition).  Preserve the
            # already armed lifecycle owner from any live Core/ADD tranche;
            # this is a hold-only rule because the outer freeze still blocks
            # admissions. CAUTION remains label-strict to fail closed.
            if (
                risk.state.value == "NORMAL"
                and account.candidate_tenure.get(arm_key, 0) == 1
                and (live_core_symbols := {
                    position.symbol
                    for position in account.positions.values()
                    if position.shares > 0
                    and position.lifecycle
                    in {
                        Lifecycle.CORE.value,
                        Lifecycle.ADD1.value,
                        Lifecycle.ADD2.value,
                    }
                })
                and all(
                    symbol in leaders
                    and leaders[symbol].mature
                    and leaders[symbol].confidence >= self.cfg.leader_min_confidence
                    and leaders[symbol].score >= self.cfg.leader_cycle_min_score
                    for symbol in live_core_symbols
                )
            ):
                account.candidate_tenure[streak_key] = 0
                return True
            account.candidate_tenure[arm_key] = 0
            account.candidate_tenure[streak_key] = 0
            return False
        if strategic_handoff_blocked:
            # A completed strategic owner records the first admissible rearm
            # session. Generic momentum must not bypass that cross-owner
            # cooldown with a streak accumulated under the old epoch.
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
            and item.score >= self.cfg.leader_cycle_min_score
            for item in leaders.values()
        )
        evidence_map = risk.evidence
        slow_market_legs = (
            float(evidence_map.get("broad_ret120", -math.inf)),
            float(evidence_map.get("tech_ret120", -math.inf)),
        )
        market_aligned = bool(
            min(slow_market_legs) >= self.cfg.leader_cycle_min_market_ret120
        )
        impulse_market_aligned = bool(
            min(slow_market_legs)
            >= self.cfg.leader_cycle_impulse_min_market_ret120
            and max(slow_market_legs) >= self.cfg.leader_cycle_min_market_ret120
        )
        impulse = (
            impulse_market_aligned
            and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
            and impulse_leader
            and risk.votes <= 1
            and float(evidence_map.get("ai_fast_return", -math.inf)) >= self.cfg.leader_cycle_impulse_return
            and float(evidence_map.get("declining_ratio", 1.0)) <= self.cfg.leader_cycle_impulse_breadth
            and float(evidence_map.get("below_ma20_ratio", 1.0)) <= self.cfg.leader_cycle_impulse_breadth
            and max(
                float(evidence_map.get("tech_speed", -math.inf)),
                float(evidence_map.get("broad_speed", -math.inf)),
            )
            >= self.cfg.leader_cycle_impulse_index_return
        )
        evidence = (
            market_aligned
            and opportunity is Opportunity.STRONG_TREND
            and risk.votes <= 1
            and credible >= self.cfg.leader_cycle_min_mature
        )
        exceptional_recovery_rearm = bool(
            account.candidate_tenure.get("recovery_cycle_rearm_pending", 0) == 1
            and account.candidate_tenure.get("tactical_cooldown", 0) <= 0
            and not account.positions
            and not account.pending_orders
            and risk.state.value == "NORMAL"
            and opportunity is Opportunity.STRONG_TREND
            and risk.votes <= 1
            and market_aligned
            and credible >= self.cfg.leader_cycle_min_mature
            and max(
                (
                    item.score
                    for item in leaders.values()
                    if item.mature and item.confidence >= self.cfg.leader_min_confidence
                ),
                default=0.0,
            )
            >= 0.90
            and float(evidence_map.get("trend_health", 0.0)) >= 0.82
        )
        if exceptional_recovery_rearm:
            # The completed cooldown and exceptional current evidence close a
            # recovery cycle without weakening the ordinary leader contract.
            account.candidate_tenure[arm_key] = 1
            account.candidate_tenure[streak_key] = 0
            account.candidate_tenure["recovery_cycle_rearm_pending"] = 0
            return True
        account.candidate_tenure[streak_key] = (
            account.candidate_tenure.get(streak_key, 0) + 1 if evidence else 0
        )
        if strategic_handoff_ready and evidence:
            # The completed cooldown is persistent cross-cycle confirmation.
            # Transfer only one admission tranche on its first healthy open
            # session; dynamic-K owns every later expansion.
            account.candidate_tenure[arm_key] = 1
            account.candidate_tenure["leader_cycle_staged_handoff"] = 1
            return True
        if account.candidate_tenure[streak_key] >= self.cfg.leader_cycle_confirm_days or impulse:
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

    def _industry_handoff(
        self,
        *,
        challenger: LeaderScore,
        incumbent: LeaderScore,
    ) -> bool:
        """Confirm a cross-industry hand-off from independent breadth evidence."""
        if (
            not self.cfg.industry_rotation_enabled
            or challenger.industry == incumbent.industry
            or challenger.industry == "unknown"
            or challenger.components.get("unknown_industry", 0.0) >= 0.5
        ):
            return False
        challenger_strength = challenger.components.get("industry_rotation_strength", 0.5)
        incumbent_strength = incumbent.components.get("industry_rotation_strength", 0.5)
        challenger_confidence = challenger.components.get("industry_confidence", 0.0)
        incumbent_breadth = incumbent.components.get("industry_breadth20", 0.0)
        return bool(
            challenger_strength >= self.cfg.industry_rotation_min_score
            and challenger_confidence >= self.cfg.industry_rotation_min_confidence
            and challenger_strength - incumbent_strength >= self.cfg.industry_rotation_edge
            and (
                incumbent_strength <= self.cfg.industry_rotation_deterioration
                or incumbent_breadth <= self.cfg.industry_rotation_breadth
            )
        )

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
                if item.emerging and self._structure_ok(user_panel[item.symbol], date)
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
        active = [symbol for symbol in account.active_leaders if symbol in held_symbols and symbol in leaders]
        for symbol in sorted(held_symbols - set(active)):
            position = account.positions[symbol]
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index or symbol not in leaders:
                continue
            row = frame.loc[date]
            proven_winner = (
                position.highest_close / max(position.avg_cost, 1e-12) - 1.0 >= 0.10
                and prices[symbol] >= position.avg_cost
                and scalar(row, "close") >= scalar(row, f"ma{self.cfg.trend_medium}")
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
                active_position = account.positions.get(symbol)
                if active_position is None:
                    continue
                proven_winner = (
                    active_position.highest_close / max(active_position.avg_cost, 1e-12) - 1.0 >= 0.10
                    and prices[symbol] / max(active_position.avg_cost, 1e-12) - 1.0 >= 0
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
            retained.extend(symbol for symbol in ranked_retention if symbol not in proven)
            retained = retained[:keep_count]
            for symbol in set(active) - set(retained):
                reasons[symbol] = "dynamic K contraction after hysteresis"
            active = retained
        available_ranked = [item for item in ranked if item.symbol not in active]
        while (
            len(active) < target_k
            and available_ranked
            and not risk.freeze_new_risk
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

        rotation_transfers: dict[str, float] = {}
        observed_rotation_key = ""
        if (
            active
            and len(active) >= target_k
            and ranked
            and not risk.freeze_new_risk
            and self._rotation_allowed(account, date, user_panel)
        ):
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
                peak_mfe = old_position.highest_close / max(old_position.avg_cost, 1e-12) - 1.0
                winner_penalty = min(0.20, 0.50 * max(0.0, peak_mfe))
                same_cluster_penalty = 0.15 if challenger.industry == leaders[weakest].industry else 0.0
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
                industry_handoff = self._industry_handoff(
                    challenger=challenger,
                    incumbent=leaders[weakest],
                )
                # Industry evidence confirms that the move is structurally
                # cross-group, but never discounts the ordinary replacement
                # edge.  A cheaper fast lane increased turnover and damaged
                # later-cycle wealth in continuous replays.
                required_edge = self.cfg.replacement_edge
                key = f"leader_rotation:{weakest}->{challenger.symbol}"
                observed_rotation_key = key
                account.replacement_tenure[key] = (
                    account.replacement_tenure.get(key, 0) + 1
                    if edge >= required_edge and old_structure_broken
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
                            "industry_handoff": industry_handoff,
                        }
                    )
                    reasons[weakest] = f"rotation exit: {challenger.symbol} confirmed edge"
                    reasons[challenger.symbol] = f"rotation entry: replaces {weakest}"
                    lifecycles[challenger.symbol] = Lifecycle.CORE
                    account.replacement_tenure[key] = 0

        for key in tuple(account.replacement_tenure):
            legacy_rotation_key = "->" in key and ":" not in key
            if (key.startswith("leader_rotation:") or legacy_rotation_key) and key != observed_rotation_key:
                account.replacement_tenure[key] = 0

        # A leader can graduate to cash when no credible replacement exists.
        # This is a lifecycle exit, not a second risk controller: it requires
        # persistent loss of both maturity and price structure.
        for symbol in list(active):
            frame = user_panel[symbol]
            row = frame.loc[date]
            exit_position = account.positions.get(symbol)
            peak_mfe = (
                exit_position.highest_close / max(exit_position.avg_cost, 1e-12) - 1.0
                if exit_position is not None
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
                and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) <= (-0.15 if protected_winner else -0.08)
            )
            key = f"lifecycle_exit:{symbol}"
            account.replacement_tenure[key] = account.replacement_tenure.get(key, 0) + 1 if broken else 0
            held_sessions = (
                len(frame.loc[pd.Timestamp(exit_position.entry_date) : date])
                if exit_position is not None and exit_position.entry_date
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
        if risk.freeze_new_risk:
            account.active_leaders = [symbol for symbol in account.active_leaders if symbol in held_symbols]
            new_core = []
        gross_cap = min(
            risk.target_gross_cap,
            self.cfg.strong_trend_gross
            if opportunity is Opportunity.STRONG_TREND
            else self.cfg.trend_target_gross
            if opportunity is Opportunity.TREND
            else self.cfg.weak_gross
            if opportunity is Opportunity.WEAK
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
        index_chase = (
            max(
                float(risk.evidence.get("broad_ret5", 0.0)),
                float(risk.evidence.get("tech_ret5", 0.0)),
            )
            >= self.cfg.add_index_chase_ret5
        )
        if not proposed and new_core:
            staged_handoff = account.candidate_tenure.get("leader_cycle_staged_handoff", 0) == 1
            high_confidence = bool(
                self.cfg.confidence_sizing_enabled
                and opportunity is Opportunity.STRONG_TREND
                and risk.state.value == "NORMAL"
                and not risk.freeze_new_risk
                and not index_chase
                and len(new_core) >= 2
                and float(risk.evidence.get("trend_health", 0.0)) >= 0.70
                and all(
                    leaders[symbol].score >= self.cfg.high_confidence_entry_score
                    and leaders[symbol].confidence >= self.cfg.leader_min_confidence
                    and leaders[symbol].components.get("industry_breadth", 0.0)
                    >= self.cfg.high_confidence_entry_breadth
                    and scalar(user_panel[symbol].loc[date], "vol20", math.inf)
                    <= self.cfg.high_confidence_entry_vol20
                    for symbol in new_core
                )
            )
            exceptional = bool(
                high_confidence
                and min(leaders[symbol].score for symbol in new_core) >= 0.90
                and float(risk.evidence.get("trend_health", 0.0)) >= 0.82
            )
            account.candidate_tenure["confidence_sized_entry"] = int(high_confidence)
            configured_entry_gross = (
                self.cfg.exceptional_entry_gross
                if exceptional
                else self.cfg.high_confidence_entry_gross
                if high_confidence
                else self.cfg.trend_entry_gross
            )
            entry_gross = min(
                max(0.0, gross_cap - satellite_reserve),
                self.cfg.core_admission_weight if staged_handoff else configured_entry_gross,
            )
            conviction_qualified = self._conviction_evidence_qualified(
                symbols=new_core,
                leaders=leaders,
                user_panel=user_panel,
                date=date,
                high_confidence=high_confidence,
            )
            account.candidate_tenure["conviction_evidence_qualified"] = int(conviction_qualified)
            raw = self._conviction_shares(
                new_core,
                leaders,
                evidence_qualified=conviction_qualified,
            )
            for symbol, share in zip(new_core, raw, strict=True):
                entry_cap = (
                    self.cfg.single_core_entry_cap if len(new_core) == 1 else self.cfg.max_symbol_weight
                )
                proposed[symbol] = min(entry_cap, entry_gross * float(share))
                lifecycles[symbol] = Lifecycle.CORE
                reasons.setdefault(
                    symbol,
                    "confirmed rearmed leader owner handoff"
                    if staged_handoff
                    else "confirmed mature leader core",
                )
            if proposed and staged_handoff:
                account.candidate_tenure["leader_cycle_staged_handoff"] = 0
                account.candidate_tenure["leader_cycle_handoff_epoch"] = (
                    account.strategic_epochs_completed
                )
            for industry in {leaders[symbol].industry for symbol in proposed}:
                members = [symbol for symbol in proposed if leaders[symbol].industry == industry]
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
                    weight for held, weight in proposed.items() if leaders[held].industry == industry
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
                members = [item for item in account.active_leaders if leaders[item].industry == industry]
                incumbents = [item for item in members if item in proposed]
                industry_weight = sum(proposed[item] for item in incumbents)
                if (
                    industry == "unknown"
                    or leaders[symbol].components.get("unknown_industry", 0.0) >= 0.5
                    or not incumbents
                    or industry_weight <= 0
                ):
                    continue
                scores = np.array(
                    [max(0.01, leaders[item].score) for item in members],
                    dtype=float,
                )
                scores /= scores.sum()
                redistributed = min(industry_weight, projected_industry_cap)
                for member_symbol, share in zip(members, scores, strict=True):
                    proposed[member_symbol] = min(
                        self.cfg.max_symbol_weight,
                        redistributed * float(share),
                    )
                lifecycles[symbol] = Lifecycle.CORE
                reasons[symbol] = "dynamic K expansion within industry cap"

        available = max(
            0.0,
            gross_cap - satellite_reserve - sum(proposed.values()),
        )
        for symbol in list(account.active_leaders):
            add_position = account.positions.get(symbol)
            if add_position is None or available < self.cfg.min_trade_weight:
                continue
            add_cooldown_complete = self._add_cooldown_complete(
                account=account,
                frame=user_panel[symbol],
                date=date,
                cooldown_sessions=self.cfg.add_tranche_cooldown_sessions,
            )
            tranche_lifecycles = {item.lifecycle for item in add_position.tranches if item.shares > 0}
            has_add1 = Lifecycle.ADD1.value in tranche_lifecycles
            has_add2 = Lifecycle.ADD2.value in tranche_lifecycles
            if not add_position.tranches:
                has_add1 = add_position.lifecycle == Lifecycle.ADD1.value
                has_add2 = add_position.lifecycle == Lifecycle.ADD2.value
            mfe = max(
                (
                    max(
                        item.mfe,
                        prices[symbol] / max(item.avg_cost, 1e-12) - 1.0,
                    )
                    for item in add_position.tranches
                    if item.shares > 0
                ),
                default=prices[symbol] / max(add_position.avg_cost, 1e-12) - 1.0,
            )
            industry = leaders[symbol].industry
            industry_weight = sum(
                weight for held, weight in proposed.items() if leaders[held].industry == industry
            )
            industry_room = max(0.0, projected_industry_cap - industry_weight)
            if (
                not has_add1
                and not has_add2
                and add_cooldown_complete
                and not index_chase
                and not risk.freeze_new_risk
                and account.candidate_tenure.get("confidence_sized_entry", 0) == 0
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
                has_add1
                and not has_add2
                and add_cooldown_complete
                and not index_chase
                and not risk.freeze_new_risk
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
            if position.shares > 0
            and (
                position.lifecycle == Lifecycle.SATELLITE.value
                or any(
                    item.shares > 0 and item.lifecycle == Lifecycle.SATELLITE.value
                    for item in position.tranches
                )
            )
        ]
        for symbol in satellites_now:
            position = account.positions[symbol]
            held = len(user_panel[symbol].loc[pd.Timestamp(position.entry_date) : date])
            if leaders.get(symbol) and leaders[symbol].mature:
                proposed[symbol] = weights_now.get(symbol, 0.0)
                lifecycles[symbol] = Lifecycle.CORE
                reasons[symbol] = "satellite promoted to mature core"
                position.lifecycle = Lifecycle.CORE.value
                promoted_shares = 0
                for tranche in position.tranches:
                    if tranche.lifecycle == Lifecycle.SATELLITE.value:
                        tranche.lifecycle = Lifecycle.CORE.value
                        promoted_shares += tranche.shares
                account.lifecycle_events.append(
                    {
                        "date": str(date.date()),
                        "symbol": symbol,
                        "from": Lifecycle.SATELLITE.value,
                        "to": Lifecycle.CORE.value,
                        "shares": promoted_shares,
                        "reason": "challenger scout confirmed",
                    }
                )
                if symbol not in account.active_leaders:
                    account.active_leaders.append(symbol)
            elif held <= self.cfg.emerging_expiry_days and self._structure_ok(user_panel[symbol], date):
                proposed[symbol] = weights_now.get(symbol, 0.0)
                lifecycles[symbol] = Lifecycle.SATELLITE
                reasons[symbol] = "emerging leader satellite observation"
            else:
                reasons[symbol] = "satellite expiry or failed confirmation"
                account.satellite_entry_dates.pop(symbol, None)
        observed_scout_keys: set[str] = set()
        if (
            self.cfg.challenger_scout_enabled
            and not risk.freeze_new_risk
            and risk.state.value == "NORMAL"
            and opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
            and len(proposed) < self.cfg.max_positions
        ):
            slots = min(
                self.cfg.max_satellites - len(satellites_now),
                self.cfg.max_positions - len(proposed),
            )
            active_industries = {
                leaders[symbol].industry
                for symbol in account.active_leaders
                if symbol in leaders and symbol in proposed
            }
            incumbent_scores = [
                leaders[symbol]
                for symbol in account.active_leaders
                if symbol in leaders and symbol in proposed
            ]
            idle_cash_weight = max(0.0, 1.0 - sum(weights_now.values()))
            incumbents_preserved = all(
                proposed.get(symbol, 0.0) + self.cfg.challenger_scout_incumbent_hysteresis
                >= weights_now.get(symbol, 0.0)
                for symbol, position in account.positions.items()
                if position.shares > 0 and symbol not in satellites_now
            )
            for item in emerging:
                if slots <= 0:
                    break
                if item.symbol in proposed:
                    continue
                weakest_score = min(
                    (incumbent.score for incumbent in incumbent_scores),
                    default=math.inf,
                )
                incumbent_fading = bool(
                    incumbent_scores
                    and any(
                        incumbent.components.get("acceleration", 0.5) < 0.50 for incumbent in incumbent_scores
                    )
                )
                scout_evidence = bool(
                    incumbent_scores
                    and item.industry not in active_industries
                    and item.industry != "unknown"
                    and item.components.get("unknown_industry", 0.0) < 0.5
                    and item.components.get("industry_rotation_strength", 0.0)
                    >= self.cfg.industry_rotation_min_score
                    and item.components.get("industry_breadth", 0.0) >= self.cfg.industry_rotation_breadth
                    and item.score - weakest_score >= self.cfg.challenger_scout_score_edge
                    and incumbent_fading
                )
                scout_key = f"challenger_scout:{item.industry}:{item.symbol}"
                observed_scout_keys.add(scout_key)
                account.replacement_tenure[scout_key] = (
                    account.replacement_tenure.get(scout_key, 0) + 1 if scout_evidence else 0
                )
                if (
                    account.replacement_tenure[scout_key] < self.cfg.challenger_scout_confirm_days
                    or not incumbents_preserved
                    or idle_cash_weight + 1e-12 < self.cfg.challenger_scout_weight
                ):
                    continue
                scout_weight = min(
                    self.cfg.challenger_scout_weight,
                    idle_cash_weight,
                    max(0.0, gross_cap - sum(proposed.values())),
                )
                if scout_weight < self.cfg.min_trade_weight:
                    continue
                industry_weight = sum(
                    weight for held, weight in proposed.items() if leaders[held].industry == item.industry
                )
                if industry_weight + scout_weight > projected_industry_cap:
                    continue
                proposed[item.symbol] = scout_weight
                lifecycles[item.symbol] = Lifecycle.SATELLITE
                reasons[item.symbol] = "idle-cash challenger scout"
                account.satellite_entry_dates[item.symbol] = str(date.date())
                account.scout_signature = scout_key
                account.scout_entry_date = str(date.date())
                idle_cash_weight -= scout_weight
                slots -= 1

        for key in tuple(account.replacement_tenure):
            if key.startswith("challenger_scout:") and key not in observed_scout_keys:
                account.replacement_tenure[key] = 0

        proposed = self._cap_opportunity_gross(
            proposed=proposed,
            gross_cap=gross_cap,
            weights_now=weights_now,
            leaders=leaders,
            reasons=reasons,
            opportunity=opportunity,
        )

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
