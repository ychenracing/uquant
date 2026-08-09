"""The only portfolio allocator: alpha and risk never submit orders directly."""

from __future__ import annotations

import math
from dataclasses import replace
from itertools import combinations

import pandas as pd

from .features import scalar
from .portfolio_core import current_weights, effective_n
from .portfolio_recovery import RecoveryPortfolioPolicy
from .types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    Opportunity,
    RiskAssessment,
    Target,
)

__all__ = ["PortfolioAllocator", "current_weights", "effective_n"]


class PortfolioAllocator(RecoveryPortfolioPolicy):
    """Compose strategic, leader, recovery, and hard-constraint policies.

    The allocator remains the sole target-weight owner. The policy layers
    contain evidence and lifecycle behavior only; none can submit an order.
    """

    @staticmethod
    def _sector_retention_score(
        target: Target,
        account: AccountState,
    ) -> float:
        """Rank already-held targets without introducing a new alpha score."""
        position = account.positions.get(target.symbol)
        if position is None:
            return target.alpha_score
        peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
        return target.alpha_score + min(0.20, 0.50 * max(0.0, peak_mfe))

    def _turnover_aware_sector_cap(
        self,
        *,
        targets: tuple[Target, ...],
        weights_now: dict[str, float],
        account: AccountState,
        gross_cap: float,
    ) -> tuple[Target, ...]:
        """Meet a synchronized-shock cap with the fewest target changes.

        At most six positive targets exist, so an exhaustive subset search is
        small and deterministic.  It first preserves the greatest number of
        existing target weights that fit, then favors stronger retained
        positions and uses at most one boundary trim.  A guard can only retain
        or reduce current exposure; it never buys while protection is active.
        """
        safe_weights = {
            target.symbol: min(
                max(0.0, target.weight),
                max(0.0, weights_now.get(target.symbol, 0.0)),
            )
            for target in targets
        }
        eligible = tuple(
            sorted(
                (
                    target
                    for target in targets
                    if safe_weights[target.symbol] > 1e-12
                ),
                key=lambda target: target.symbol,
            )
        )
        feasible = [
            subset
            for size in range(len(eligible) + 1)
            for subset in combinations(eligible, size)
            if sum(safe_weights[target.symbol] for target in subset)
            <= gross_cap + 1e-12
        ]
        unchanged = max(
            feasible,
            key=lambda subset: (
                len(subset),
                sum(
                    self._sector_retention_score(target, account)
                    for target in subset
                ),
                sum(safe_weights[target.symbol] for target in subset),
                tuple(target.symbol for target in subset),
            ),
        )
        retained = {
            target.symbol: safe_weights[target.symbol] for target in unchanged
        }
        remaining = max(0.0, gross_cap - sum(retained.values()))
        boundary = next(
            (
                target
                for target in sorted(
                    eligible,
                    key=lambda item: (
                        -self._sector_retention_score(item, account),
                        item.symbol,
                    ),
                )
                if target.symbol not in retained
            ),
            None,
        )
        if boundary is not None and remaining > 1e-12:
            retained[boundary.symbol] = min(
                safe_weights[boundary.symbol],
                remaining,
            )

        capped: list[Target] = []
        for target in targets:
            weight = retained.get(target.symbol, 0.0)
            reason = target.reason
            if weight + 1e-12 < weights_now.get(target.symbol, 0.0):
                reason = f"portfolio risk gross cap; {reason}"
            capped.append(replace(target, weight=weight, reason=reason))
        if sum(item.weight for item in capped if item.weight > 0) > gross_cap + 1e-8:
            raise RuntimeError("allocator failed to enforce sector risk gross cap")
        return tuple(capped)

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
        if risk.shock_state == "SECTOR_GUARD":
            return self._turnover_aware_sector_cap(
                targets=targets,
                weights_now=weights_now,
                account=account,
                gross_cap=gross_cap,
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
        self._release_stale_recovery_anchor(
            risk=risk,
            account=account,
            weights_now=weights_now,
        )
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
            account.candidate_tenure["recovery_cohort_graduated"] = 0
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
            if any(
                marker in risk.reasons
                for marker in (
                    "capital drawdown relapse in restored holdings",
                    "capital guard cooldown after failed restoration",
                )
            ):
                self._release_recovery_anchor(account)
                account.protected_weights.clear()
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
            self._release_recovery_anchor(account)
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
                    if item[0].confidence >= self.cfg.leader_min_confidence
                    and item[0].score >= self.cfg.recovery_reserve_min_score
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
                account.candidate_tenure["recovery_cohort_graduated"] = 0
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
