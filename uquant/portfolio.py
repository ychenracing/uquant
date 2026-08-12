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
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
)

__all__ = ["PortfolioAllocator", "current_weights", "effective_n"]


class PortfolioAllocator(RecoveryPortfolioPolicy):
    """Compose strategic, leader, recovery, and hard-constraint policies.

    The allocator remains the sole target-weight owner. The policy layers
    contain evidence and lifecycle behavior only; none can submit an order.
    """

    def _confirmed_recovery_gross(
        self,
        *,
        risk: RiskAssessment,
        account: AccountState,
    ) -> float:
        """Return one stable aggregate cap for a locked three-member cohort."""
        explicit_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
        fully_repaired = bool(
            risk.state is Risk.NORMAL
            and not risk.freeze_new_risk
            and not bool(risk.evidence.get("freeze_new_risk", False))
            and account.capital_budget_level == 0
            and account.chronic_level == 0
        )
        return (
            explicit_cap
            if fully_repaired
            else min(self.cfg.recovery_target_gross, explicit_cap)
        )

    def _risk_retention_score(
        self,
        target: Target,
        account: AccountState,
    ) -> float:
        """Value healthy winners and Core lots above fragile incremental risk."""
        position = account.positions.get(target.symbol)
        if position is None:
            return target.alpha_score
        peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
        tranche_value = 0.0
        total_shares = sum(item.shares for item in position.tranches)
        lifecycle_value = {
            Lifecycle.CORE.value: 0.18,
            Lifecycle.RECOVERY.value: 0.04,
            Lifecycle.ADD1.value: -0.05,
            Lifecycle.ADD2.value: -0.10,
            Lifecycle.SATELLITE.value: -0.16,
        }
        if total_shares > 0:
            tranche_value = sum(
                item.shares
                / total_shares
                * (
                    lifecycle_value.get(item.lifecycle, 0.0)
                    + 0.05 * item.entry_score
                    + 0.05 * max(-0.50, item.mae)
                )
                for item in position.tranches
            )
        conviction_bonus = (
            self.cfg.recovery_conviction_retention_bonus
            if self.cfg.recovery_conviction_weighting_enabled
            and account.recovery_conviction_symbol == target.symbol
            and any(
                tranche.lifecycle == Lifecycle.RECOVERY.value
                for tranche in position.tranches
            )
            else 0.0
        )
        return (
            target.alpha_score
            + tranche_value
            + min(0.20, 0.50 * max(0.0, peak_mfe))
            + conviction_bonus
        )

    @staticmethod
    def _risk_retention_vector(
        target: Target,
        account: AccountState,
        retained_weight: float,
        current_weight: float,
    ) -> tuple[float, float, float, float, float, float]:
        """Return the lifecycle composition left after a risk-priority sale.

        Retaining healthy Core must dominate retaining damaged Core, Recovery,
        ADD1, ADD2, or Satellite exposure in that order. Sparse symbol changes
        are a tie-break only after lifecycle composition is equivalent.
        Execution consumes the selected symbol's lots in the exact reverse
        order, so a partial target retains the best lots rather than a
        proportional slice of every tranche.
        """
        locked_recovery_anchor = bool(
            account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
            and target.symbol in account.anchor_weights
        )
        conviction_owner = bool(
            account.recovery_conviction_symbol == target.symbol
            and target.symbol in account.positions
        )
        position = account.positions.get(target.symbol)
        if position is None or not position.tranches:
            lifecycle = target.lifecycle
            buckets = [0.0] * 6
            index = (
                0
                if lifecycle == Lifecycle.RECOVERY.value
                and (locked_recovery_anchor or conviction_owner)
                else {
                    Lifecycle.CORE.value: 0,
                    Lifecycle.RECOVERY.value: 2,
                    Lifecycle.ADD1.value: 3,
                    Lifecycle.ADD2.value: 4,
                    Lifecycle.SATELLITE.value: 5,
                }.get(lifecycle, 1)
            )
            buckets[index] = retained_weight
            return (
                buckets[0],
                buckets[1],
                buckets[2],
                buckets[3],
                buckets[4],
                buckets[5],
            )

        total_shares = sum(max(0, tranche.shares) for tranche in position.tranches)
        if total_shares <= 0:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        classified: list[tuple[int, float]] = []
        for tranche in position.tranches:
            if tranche.shares <= 0:
                continue
            if tranche.lifecycle == Lifecycle.CORE.value or (
                tranche.lifecycle == Lifecycle.RECOVERY.value
                and (locked_recovery_anchor or conviction_owner)
            ):
                # MAE is causal tranche evidence.  A deeply impaired Core is
                # still retained after every incremental lifecycle, but before
                # a healthy Core rather than because of its symbol-level alpha.
                # Three independently confirmed locked anchors share this
                # durable priority even when their entry lot still records the
                # RECOVERY provenance used for attribution.
                index = 0 if tranche.mae > -0.15 else 1
            else:
                index = {
                    Lifecycle.RECOVERY.value: 2,
                    Lifecycle.ADD1.value: 3,
                    Lifecycle.ADD2.value: 4,
                    Lifecycle.SATELLITE.value: 5,
                }.get(tranche.lifecycle, 1)
            classified.append(
                (
                    index,
                    max(0.0, current_weight) * tranche.shares / total_shares,
                )
            )
        buckets = [0.0] * 6
        remaining = min(max(0.0, retained_weight), max(0.0, current_weight))
        for index, tranche_weight in sorted(classified, key=lambda item: item[0]):
            kept = min(remaining, tranche_weight)
            buckets[index] += kept
            remaining -= kept
            if remaining <= 1e-12:
                break
        return (
            buckets[0],
            buckets[1],
            buckets[2],
            buckets[3],
            buckets[4],
            buckets[5],
        )

    @staticmethod
    def _risk_lifecycle_rank(
        retained: tuple[float, float, float, float, float, float],
    ) -> tuple[float, float, float, float, float, float]:
        """Rank equal-gross plans by the absolute lifecycle sell order.

        Every candidate passed here retains the same total gross.  Maximizing
        healthy Core first, then damaged Core, Recovery, ADD1, ADD2, and
        Satellite is therefore exactly equivalent to selling in the reverse
        order.  Gross equality is essential: otherwise an empty portfolio
        would look structurally perfect and over-reduce below the risk cap.
        """
        return tuple(max(0.0, value) for value in retained)  # type: ignore[return-value]

    def _subset_retention_vector(
        self,
        targets: tuple[Target, ...],
        account: AccountState,
        retained_weights: dict[str, float],
        weights_now: dict[str, float],
    ) -> tuple[float, float, float, float, float, float]:
        vectors = [
            self._risk_retention_vector(
                target,
                account,
                retained_weights[target.symbol],
                weights_now.get(target.symbol, 0.0),
            )
            for target in targets
        ]
        return tuple(sum(vector[index] for vector in vectors) for index in range(6))  # type: ignore[return-value]

    def _sparse_risk_reduce(
        self,
        *,
        targets: tuple[Target, ...],
        weights_now: dict[str, float],
        account: AccountState,
        gross_cap: float,
        risk_reason: str = "portfolio risk gross cap",
        risk_reason_code: str = "risk_gross_cap",
        risk_exit_kind: str = "risk",
        prices: dict[str, float] | None = None,
    ) -> tuple[Target, ...]:
        """Meet every risk cap with one deterministic sparse reduction.

        The lexicographic objective is cap compliance, safer normalized
        lifecycle composition, the fewest changed symbols among lifecycle-
        equivalent plans, stronger retention utility, and the smallest
        residual boundary change. At most one symbol receives a partial
        boundary trim. A guard can only retain or reduce current exposure; it
        never buys while protection is active.
        """
        safe_weights = {
            target.symbol: min(
                self.cfg.max_symbol_weight,
                max(0.0, target.weight),
                max(0.0, weights_now.get(target.symbol, 0.0)),
            )
            for target in targets
        }
        eligible = tuple(
            sorted(
                (target for target in targets if safe_weights.get(target.symbol, 0.0) > 1e-12),
                key=lambda target: target.symbol,
            )
        )
        desired_gross = min(
            max(0.0, gross_cap),
            sum(safe_weights[target.symbol] for target in eligible),
        )
        target_by_symbol = {target.symbol: target for target in targets}
        full_vectors = {
            target.symbol: self._risk_retention_vector(
                target,
                account,
                safe_weights[target.symbol],
                weights_now.get(target.symbol, 0.0),
            )
            for target in eligible
        }

        # First solve the economic problem globally: retain every healthier
        # lifecycle bucket before any weaker bucket.  Only then solve the
        # turnover tie-break inside the single boundary bucket.  Enumerating
        # whole symbols first cannot express a mixed CORE/SATELLITE position
        # whose Satellite should be sold before another symbol's ADD2.
        retained_by_bucket = [0.0] * 6
        remaining_gross = desired_gross
        for index in range(6):
            available = sum(vector[index] for vector in full_vectors.values())
            retained_by_bucket[index] = min(available, remaining_gross)
            remaining_gross -= retained_by_bucket[index]
        if remaining_gross > 1e-8:
            raise RuntimeError("allocator lifecycle buckets do not reconcile to target gross")

        base = {target.symbol: 0.0 for target in targets}
        boundary_index: int | None = None
        boundary_required = 0.0
        for index, retained_bucket in enumerate(retained_by_bucket):
            available = sum(vector[index] for vector in full_vectors.values())
            if retained_bucket >= available - 1e-12:
                for symbol, vector in full_vectors.items():
                    base[symbol] += vector[index]
                continue
            if retained_bucket > 1e-12:
                boundary_index = index
                boundary_required = retained_bucket
            break

        candidate_plans: list[dict[str, float]] = []
        if boundary_index is None:
            candidate_plans.append(base)
        else:
            boundary_capacity = {
                symbol: vector[boundary_index]
                for symbol, vector in full_vectors.items()
                if vector[boundary_index] > 1e-12
            }
            boundary_symbols = tuple(sorted(boundary_capacity))
            for size in range(len(boundary_symbols) + 1):
                for subset in combinations(boundary_symbols, size):
                    subset_total = sum(boundary_capacity[symbol] for symbol in subset)
                    if subset_total > boundary_required + 1e-12:
                        continue
                    remainder = max(0.0, boundary_required - subset_total)
                    if remainder <= 1e-12:
                        plan = dict(base)
                        for symbol in subset:
                            plan[symbol] += boundary_capacity[symbol]
                        candidate_plans.append(plan)
                        continue
                    for boundary_symbol in boundary_symbols:
                        if boundary_symbol in subset:
                            continue
                        if remainder > boundary_capacity[boundary_symbol] + 1e-12:
                            continue
                        plan = dict(base)
                        for symbol in subset:
                            plan[symbol] += boundary_capacity[symbol]
                        plan[boundary_symbol] += min(
                            remainder,
                            boundary_capacity[boundary_symbol],
                        )
                        candidate_plans.append(plan)
        if not candidate_plans:
            raise RuntimeError("allocator could not construct an exact sparse risk plan")

        def plan_rank(plan: dict[str, float]) -> tuple[object, ...]:
            vectors = [
                self._risk_retention_vector(
                    target_by_symbol[symbol],
                    account,
                    weight,
                    weights_now.get(symbol, 0.0),
                )
                for symbol, weight in plan.items()
                if weight > 1e-12
            ]
            retained_vector = tuple(sum(vector[index] for vector in vectors) for index in range(6))
            unchanged = sum(
                abs(plan.get(target.symbol, 0.0) - safe_weights[target.symbol]) <= 1e-12
                for target in eligible
            )
            utility = sum(
                self._risk_retention_score(target, account) * plan.get(target.symbol, 0.0)
                for target in eligible
            )
            sector_guard_health = (
                sum(
                    weight
                    * (
                        (prices or {}).get(symbol, account.positions[symbol].highest_close)
                        / max(account.positions[symbol].highest_close, 1e-12)
                        - 1.0
                    )
                    for symbol, weight in plan.items()
                    if weight > 1e-12 and symbol in account.positions
                )
                if risk_reason_code == "sector_guard"
                else 0.0
            )
            return (
                self._risk_lifecycle_rank(retained_vector),  # type: ignore[arg-type]
                sector_guard_health,
                unchanged,
                utility,
                tuple(symbol for symbol in sorted(plan) if plan[symbol] > 1e-12),
            )

        retained = max(candidate_plans, key=plan_rank)

        capped: list[Target] = []
        current_gross = sum(max(0.0, value) for value in weights_now.values())
        for target in targets:
            weight = retained.get(target.symbol, 0.0)
            reason = target.reason
            reduction_policy = target.reduction_policy
            reason_code = target.reason_code
            exit_kind = target.exit_kind
            # Preserve an already-more-conservative strategy exit (rotation,
            # lifecycle expiry, stop, and so on).  Risk owns the metadata and
            # tranche priority only when this reducer lowers the strategy's
            # original target as well as the live exposure.
            current_weight = weights_now.get(target.symbol, 0.0)
            reducer_lowered_target = weight + 1e-12 < target.weight
            risk_must_force_positive_trim = bool(
                current_gross > gross_cap + 1e-12
                and target.weight > 1e-12
                and weight + 1e-12 < current_weight
            )
            if weight + 1e-12 < current_weight and (reducer_lowered_target or risk_must_force_positive_trim):
                reason = f"{risk_reason}; {reason}"
                reduction_policy = ReductionPolicy.RISK_PRIORITY.value
                reason_code = risk_reason_code
                exit_kind = risk_exit_kind
            capped.append(
                replace(
                    target,
                    weight=weight,
                    reason=reason,
                    reduction_policy=reduction_policy,
                    reason_code=reason_code,
                    exit_kind=exit_kind,
                )
            )
        if sum(item.weight for item in capped if item.weight > 0) > gross_cap + 1e-8:
            raise RuntimeError("allocator failed to enforce sector risk gross cap")
        return tuple(capped)

    @staticmethod
    def _risk_reduction_metadata(risk: RiskAssessment) -> tuple[str, str, str]:
        """Return the causal owner of a hard portfolio gross reduction."""
        if bool(risk.evidence.get("sector_guard_active", False)):
            return ("sector guard gross cap", "sector_guard", "sector_guard")
        if risk.state is Risk.RISK_OFF:
            return ("portfolio risk-off gross cap", "risk_off", "risk_off")
        if risk.state is Risk.CRISIS:
            return ("portfolio crisis gross cap", "crisis", "crisis")
        capital_level = risk.evidence.get("capital_budget_level", 0)
        if isinstance(capital_level, (int, float)) and int(capital_level) >= 2:
            return ("capital budget gross cap", "capital_budget", "capital_budget")
        return ("portfolio risk gross cap", "risk_gross_cap", "risk")

    # Compatibility for downstream research callers; all production cap paths
    # use the generalized reducer below.
    def _turnover_aware_sector_cap(
        self,
        *,
        targets: tuple[Target, ...],
        weights_now: dict[str, float],
        account: AccountState,
        gross_cap: float,
    ) -> tuple[Target, ...]:
        return self._sparse_risk_reduce(
            targets=targets,
            weights_now=weights_now,
            account=account,
            gross_cap=gross_cap,
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
        risk_reason, risk_reason_code, risk_exit_kind = self._risk_reduction_metadata(risk)
        target_gross = sum(item.weight for item in targets if item.weight > 0)
        weights_now, _ = current_weights(account, prices)
        current_gross = sum(weight for weight in weights_now.values() if weight > 0)
        if target_gross <= gross_cap + 1e-12:
            if current_gross <= gross_cap + 1e-12:
                return targets
            return self._sparse_risk_reduce(
                targets=targets,
                weights_now=weights_now,
                account=account,
                gross_cap=gross_cap,
                risk_reason=risk_reason,
                risk_reason_code=risk_reason_code,
                risk_exit_kind=risk_exit_kind,
                prices=prices,
            )
        return self._sparse_risk_reduce(
            targets=targets,
            weights_now=weights_now,
            account=account,
            gross_cap=gross_cap,
            risk_reason=risk_reason,
            risk_reason_code=risk_reason_code,
            risk_exit_kind=risk_exit_kind,
            prices=prices,
        )

    @staticmethod
    def _frozen_existing_targets(
        *,
        strategy_targets: tuple[Target, ...] | None,
        leaders: dict[str, LeaderScore],
        account: AccountState,
        weights_now: dict[str, float],
    ) -> tuple[Target, ...]:
        """Block additions while preserving every durable reduction intent.

        A risk freeze cancels unfinished BUYs but is not an exit signal.  An
        existing SELL must retain its exact immutable order intent, and a new
        strategy-owned reduction (for example a strategic trailing band) must
        remain executable.  Only proposed increases are replaced by the live
        marked weight.
        """
        proposed_by_symbol = {target.symbol: target for target in strategy_targets or ()}
        frozen: list[Target] = []
        for symbol in sorted(account.positions):
            position = account.positions[symbol]
            if position.shares <= 0:
                continue
            current_weight = weights_now.get(symbol, 0.0)
            pending_sell = next(
                (
                    order
                    for order in account.pending_orders
                    if order.symbol == symbol and order.side == "SELL"
                ),
                None,
            )
            if pending_sell is not None and current_weight > pending_sell.target_weight + 1e-12:
                frozen.append(
                    Target(
                        symbol=symbol,
                        weight=pending_sell.target_weight,
                        lifecycle=pending_sell.lifecycle,
                        alpha_score=pending_sell.entry_score,
                        confidence=pending_sell.entry_confidence,
                        reason=pending_sell.reason,
                        reduction_policy=pending_sell.reduction_policy,
                        reason_code=pending_sell.reason_code,
                        exit_kind=pending_sell.exit_kind,
                        entry_industry_strength=pending_sell.entry_industry_strength,
                    )
                )
                continue
            strategy_target = proposed_by_symbol.get(symbol)
            if strategy_target is not None and strategy_target.weight + 1e-12 < current_weight:
                frozen.append(strategy_target)
                continue
            score = leaders.get(symbol)
            frozen.append(
                Target(
                    symbol=symbol,
                    weight=current_weight,
                    lifecycle=position.lifecycle,
                    alpha_score=score.score if score else 0.0,
                    confidence=score.confidence if score else 0.0,
                    reason="level-1 risk freeze; retain existing exposure",
                    reason_code="risk_freeze_hold",
                    exit_kind="risk",
                )
            )
        return tuple(frozen)

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
        weights_now, equity = current_weights(account, prices)
        self._release_stale_recovery_anchor(
            risk=risk,
            account=account,
            weights_now=weights_now,
        )
        failed_restoration = bool(
            risk.state is Risk.CRISIS
            and any(
                marker in risk.reasons
                for marker in (
                    "capital drawdown relapse in restored holdings",
                    "capital guard cooldown after failed restoration",
                )
            )
        )
        if failed_restoration:
            # A failed economic restore is a final lifecycle break, not another
            # temporary cap.  Settle every restoration owner before the
            # strategic early-return path can recapture and later resurrect the
            # same cohort.
            self._release_recovery_anchor(account)
            account.protected_weights.clear()
            for symbol in tuple(account.strategic_cohort_targets):
                self._retire_strategic_member(account, symbol)
            account.candidate_tenure["post_shock_restore_complete"] = 0
        freeze_active = bool(
            risk.freeze_new_risk
            or risk.evidence.get("freeze_new_risk", False)
            or risk.state in {Risk.RISK_OFF, Risk.CRISIS}
        )
        repair_observation = bool(
            risk.state in {Risk.NORMAL, Risk.CAUTION}
            and risk.reduction_level <= 1
            and risk.votes <= 1
            and float(risk.evidence.get("transition_damage", math.inf)) <= self.cfg.transition_damage_repair
        )
        general_core_symbols = {
            symbol
            for symbol, position in account.positions.items()
            if position.shares > 0
            and position.lifecycle
            in {
                Lifecycle.CORE.value,
                Lifecycle.ADD1.value,
                Lifecycle.ADD2.value,
                Lifecycle.SATELLITE.value,
            }
        }
        risk_neutral_recovery_handoff = bool(
            opportunity is Opportunity.RECOVERY
            and freeze_active
            and risk.state in {Risk.NORMAL, Risk.CAUTION}
            and risk.shock_state
            in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
            and risk.votes <= 1
            and float(risk.evidence.get("transition_damage", math.inf))
            <= self.cfg.transition_damage_repair
            and bool(account.last_shock_date)
            and account.capital_budget_level >= 1
            and bool(general_core_symbols)
            and not account.pending_orders
            and not account.anchor_weights
            and set(account.protected_weights) <= general_core_symbols
            and not account.strategic_restore_weights
            and not account.strategic_cohort_targets
            and sum(max(0.0, weight) for weight in weights_now.values())
            <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
        )
        risk_neutral_recovery_expansion = bool(
            opportunity is Opportunity.RECOVERY
            and freeze_active
            and risk.state in {Risk.NORMAL, Risk.CAUTION}
            and risk.shock_state
            in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
            and risk.votes <= 1
            and float(risk.evidence.get("transition_damage", math.inf))
            <= self.cfg.transition_damage_repair
            and bool(account.last_shock_date)
            and account.capital_budget_level >= 1
            and account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
            and bool(account.anchor_weights)
            and not account.pending_orders
            and not account.protected_weights
            and not account.strategic_restore_weights
            and not account.strategic_cohort_targets
            and sum(max(0.0, weight) for weight in weights_now.values())
            <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
        )
        risk_neutral_recovery_transfer = bool(
            risk_neutral_recovery_handoff or risk_neutral_recovery_expansion
        )
        level1_recovery_repair = bool(
            freeze_active
            and repair_observation
            and account.capital_budget_level == 1
            and account.capital_budget_repair_streak >= 2
        )
        protected_level1_restore = bool(
            freeze_active
            and repair_observation
            and risk.state is Risk.CAUTION
            and risk.shock_state in {"RECOVERY", "ROTATION_RECOVERY", "FAST_V_RECOVERY"}
            and account.capital_budget_level == 1
            and account.capital_budget_repair_streak >= 1
            and bool(account.protected_weights or account.strategic_restore_weights)
        )
        synchronized_protected_restore = bool(
            freeze_active
            and risk.state is Risk.CAUTION
            and risk.shock_state == "RECOVERY"
            and "two-day synchronized leader repair" in risk.reasons
            and account.capital_budget_level <= 1
            and account.chronic_level <= 1
            and bool(account.protected_weights)
        )
        user_repair_industries = {
            score.industry
            for score in leaders.values()
            if score.industry != "unknown"
            and score.confidence >= self.cfg.leader_min_confidence
        }
        incumbent_repair_industries = {
            leaders[symbol].industry
            for symbol in account.anchor_weights
            if symbol in leaders and leaders[symbol].industry != "unknown"
        }
        independent_user_repair_industries = (
            user_repair_industries - incumbent_repair_industries
        )
        unsupported_locked_restore = bool(
            synchronized_protected_restore
            and account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
            # A homogeneous recovery cohort is already three-name internal
            # confirmation even when the wider opportunity set contains
            # unrelated industries. Requiring leaders outside that cohort
            # would make restoration depend on symbols that never owned the
            # crash decision. Genuinely mixed-industry cohorts still require
            # independent user-side breadth before missing members are rebought.
            and len(incumbent_repair_industries) != 1
            and len(independent_user_repair_industries)
            < self.cfg.strategic_cohort_min_size
        )
        if unsupported_locked_restore:
            # Reference sentinels can confirm that market stress repaired, but
            # they cannot manufacture breadth inside the user's opportunity
            # set. A concentrated old cohort whose missing members lack three
            # independent user industries is retired in place: keep the live
            # survivor, discard only stale rebuy rights, and require any later
            # expansion to clear ordinary admission again.
            live_anchors = {
                symbol: weights_now.get(symbol, 0.0)
                for symbol in account.anchor_weights
                if weights_now.get(symbol, 0.0) > 1e-12
            }
            retired = set(account.anchor_weights) - set(live_anchors)
            account.anchor_weights = live_anchors
            account.protected_weights.clear()
            for symbol in retired:
                account.strategic_restore_weights.pop(symbol, None)
            account.candidate_tenure["recovery_cohort_locked"] = 0
            account.candidate_tenure["post_shock_restore_complete"] = 1
            account.candidate_tenure["recovery_substitution_pending"] = 0
            synchronized_protected_restore = False
        bounded_caution_recovery_probe = bool(
            freeze_active
            and risk.state is Risk.CAUTION
            and account.capital_budget_level == 0
            and account.chronic_level == 0
            and not account.positions
            and not account.anchor_weights
            and not account.protected_weights
            and not account.strategic_restore_weights
        )
        live_anchor_count = sum(
            1
            for symbol in account.anchor_weights
            if account.positions.get(symbol) is not None
            and account.positions[symbol].shares > 0
        )
        required_damage_ratio = (
            min(2, live_anchor_count) / live_anchor_count
            if live_anchor_count > 0
            else 1.0
        )
        unbroken_recovery_epoch = bool(
            not account.last_shock_date
            or (
                account.recovery_anchor_date
                and pd.Timestamp(account.recovery_anchor_date)
                > pd.Timestamp(account.last_shock_date)
            )
        )
        confirmed_recovery_trail = bool(
            freeze_active
            and risk.state is Risk.CAUTION
            and risk.votes >= 2
            and bool(account.anchor_weights)
            and not account.protected_weights
            # Once risk has broken and restored this same cohort, the risk
            # state machine owns any later relapse; the pre-shock winner trail
            # must not liquidate the durable restore a second time.
            and unbroken_recovery_epoch
            and float(risk.evidence.get("held_damage_ratio", 0.0))
            >= required_damage_ratio - 1e-12
            and float(risk.evidence.get("sector_stress_ratio", 0.0)) >= 0.50
        )
        hard_risk_trail_signal = bool(
            freeze_active
            and risk.state in {Risk.RISK_OFF, Risk.CRISIS}
            and bool(account.anchor_weights)
            and any(
                marker in risk.reasons
                for marker in (
                    "confirmed synchronized holdings shock",
                    "confirmed dynamic cohort structural break",
                )
            )
        )
        anchor_industries = {
            leaders[symbol].industry
            for symbol in account.anchor_weights
            if symbol in leaders and leaders[symbol].industry != "unknown"
        }
        if hard_risk_trail_signal and len(anchor_industries) >= 2:
            account.candidate_tenure["cross_industry_hard_risk_trail"] = 1
        confirmed_hard_risk_trail = bool(
            freeze_active
            and risk.state in {Risk.RISK_OFF, Risk.CRISIS}
            and bool(account.anchor_weights)
            and account.candidate_tenure.get("cross_industry_hard_risk_trail", 0) == 1
        )
        reason_clean_caution_anchor_cap = bool(
            freeze_active
            and risk.state is Risk.CAUTION
            and not risk.reasons
            and account.capital_budget_level == 0
            and account.chronic_level == 0
            and live_anchor_count == 1
            and bool(account.recovery_anchor_date)
            and not account.protected_weights
            and not account.strategic_restore_weights
        )
        bounded_recovery_repair = bool(
            level1_recovery_repair
            or protected_level1_restore
            or synchronized_protected_restore
            or bounded_caution_recovery_probe
            or risk_neutral_recovery_transfer
            # This exception can only submit strategy-owned SELLs from the
            # already deployed recovery book; it never opens new exposure.
            or confirmed_recovery_trail
            or confirmed_hard_risk_trail
            or (
                freeze_active
                and repair_observation
                and account.capital_budget_level <= 1
                and account.chronic_level >= 1
                and account.chronic_repair_streak >= 2
            )
        )
        strategic_live = account.candidate_tenure.get("strategic_cohort_active", 0) == 1
        bounded_strategic_restore = bool(
            freeze_active
            and strategic_live
            and self._bounded_strategic_restore_risk_open(risk=risk, account=account)
        )
        strategic_discovery_open = bool(
            opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
            and risk.state is Risk.NORMAL
            and not freeze_active
            and not (
                account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
                and bool(account.anchor_weights)
            )
        )
        strategic_observation_open = bool(
            opportunity
            in {
                Opportunity.CHOPPY,
                Opportunity.WEAK,
                Opportunity.TREND,
                Opportunity.STRONG_TREND,
            }
            and risk.state is Risk.NORMAL
            and not freeze_active
        )
        # RECOVERY remains owned by the crash-recovery policy. CHOPPY/WEAK are
        # observation-only for ordinary factor cohorts; the strategic policy
        # may admit there only through its separately confirmed persistent or
        # synchronized-reversal industry route. A live strategic cohort is
        # evaluated through every regime so its exits remain durable.
        strategic = (
            self._strategic_cohort_targets(
                date=date,
                risk=risk,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
                prices=prices,
                weights_now=weights_now,
                admission_open=strategic_discovery_open,
            )
            if strategic_live or strategic_observation_open
            else None
        )
        if strategic is not None:
            if freeze_active and not bounded_strategic_restore:
                return self._frozen_existing_targets(
                    strategy_targets=strategic,
                    leaders=leaders,
                    account=account,
                    weights_now=weights_now,
                )
            return strategic

        if freeze_active:
            # A confirmed recovery-anchor substitution may still identify its
            # structurally broken sell leg. The freeze overlay below clamps the
            # replacement leg to live exposure, so it cannot create a BUY.
            if risk.state is Risk.CAUTION:
                anchor_elapsed = 0
                if account.recovery_anchor_date:
                    anchor_elapsed = self._session_distance(
                        self._session_clock(user_panel, date),
                        account.recovery_anchor_date,
                        date,
                    )
                substitution = self._recovery_anchor_substitution(
                    date=date,
                    risk=risk,
                    user_panel=user_panel,
                    leaders=leaders,
                    account=account,
                    weights_now=weights_now,
                    anchor_elapsed=anchor_elapsed,
                    risk_neutral_only=True,
                )
                if substitution is not None:
                    return substitution
            if reason_clean_caution_anchor_cap:
                anchored_held = {
                    symbol: weights_now.get(symbol, 0.0)
                    for symbol in account.anchor_weights
                    if weights_now.get(symbol, 0.0) > 0
                }
                capped_anchors, capped = self._cap_underdiversified(anchored_held, account)
                if capped:
                    cap_targets = self._targets(
                        proposed=capped_anchors,
                        leaders=leaders,
                        account=account,
                        lifecycle=Lifecycle.RECOVERY,
                        reason="under-diversified recovery cap",
                    )
                    return self._frozen_existing_targets(
                        strategy_targets=cap_targets,
                        leaders=leaders,
                        account=account,
                        weights_now=weights_now,
                    )
            # A freeze is not an implicit exit. A durable reduction remains
            # executable through every freeze, but unfinished additions stop
            # until risk explicitly reopens.  Confirmed level-1/chronic repair
            # and an independently observable empty-book crash probe are the
            # bounded exceptions; neither can reach generic leader admission.
            if not bounded_recovery_repair:
                return self._frozen_existing_targets(
                    strategy_targets=None,
                    leaders=leaders,
                    account=account,
                    weights_now=weights_now,
                )
        strategic_handoff_pending = bool(
            account.strategic_epochs_completed > 0
            and account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
            and account.candidate_tenure.get("leader_cycle_handoff_epoch", 0)
            < account.strategic_epochs_completed
            and account.strategic_rearm_date
            and not account.positions
            and not account.pending_orders
            and not account.anchor_weights
            and not account.protected_weights
        )
        strategic_handoff_ready = bool(
            strategic_handoff_pending
            and date.normalize() >= pd.Timestamp(account.strategic_rearm_date).normalize()
        )
        leader_cycle_armed = self._update_leader_cycle_arm(
            opportunity=opportunity,
            risk=risk,
            leaders=leaders,
            account=account,
            strategic_handoff_blocked=(
                strategic_handoff_pending and not strategic_handoff_ready
            ),
            strategic_handoff_ready=strategic_handoff_ready,
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
                and (not account.tactical_anchor_symbol or position.symbol == account.tactical_anchor_symbol)
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
            account.anchor_weights = {position.symbol: weights_now.get(position.symbol, 0.0)}
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
            and leaders[tactical[0].symbol].confidence >= self.cfg.leader_min_confidence
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
                # This is a final strategy exit, not a temporary risk trim.
                # Any saved restore intent for the same tactical position must
                # retire atomically; otherwise an already sold probe can leave
                # ``protected_weights`` alive forever and block every later
                # strategic cohort in a long replay.
                account.protected_weights.pop(position.symbol, None)
                account.strategic_restore_weights.pop(position.symbol, None)
                account.candidate_tenure["tactical_active"] = 0
                account.candidate_tenure["tactical_cooldown"] = self.cfg.tactical_rebound_cooldown_days
                account.tactical_anchor_symbol = ""
                return self._targets(
                    proposed={},
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="controlled rebound exit",
                )
            pending_buy = next(
                (
                    order
                    for order in account.pending_orders
                    if order.symbol == position.symbol and order.side == "BUY"
                ),
                None,
            )
            safe_partial_completion = bool(
                pending_buy is not None
                and risk.votes <= 2
                and float(risk.evidence.get("transition_damage", 1.0)) < self.cfg.transition_damage_freeze
            )
            held_target = weights_now.get(position.symbol, 0.0)
            if safe_partial_completion and pending_buy is not None:
                held_target = max(held_target, pending_buy.target_weight)
            return self._targets(
                proposed={position.symbol: held_target},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="controlled rebound probe",
            )

        if risk.state.value == "CRISIS" and not confirmed_hard_risk_trail:
            if account.anchor_weights:
                account.candidate_tenure["risk_trimmed"] = 1
            # Preserve the live economic targets here.  The single outer
            # sparse reducer owns every gross-cap cut, including CRISIS; doing
            # a proportional pre-scale here manufactured one sell per symbol
            # and defeated late-add-first retention.
            proposed = {symbol: weight for symbol, weight in weights_now.items() if weight > 0}
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

        if (
            account.protected_weights
            and risk.state.value in {"NORMAL", "CAUTION"}
            and not risk_neutral_recovery_handoff
        ):
            # Restoration intent survives transient FAILED_REPAIR observations.
            # The CAUTION freeze above still blocks a buy while continuous
            # damage is active; once independent repair clears it, retaining
            # the protected target prevents a one-day state label from
            # permanently stranding the book at crisis gross.
            account.candidate_tenure["post_shock_recovery"] = int(account.shock_severity == "SEVERE")
            proposed = {
                symbol: min(self.cfg.max_symbol_weight, weight)
                for symbol, weight in account.protected_weights.items()
                if symbol in user_panel and symbol not in account.strategic_cohort_targets
            }
            total = sum(proposed.values())
            explicit_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
            current_gross = sum(max(0.0, weight) for weight in weights_now.values())
            if total > explicit_cap and total > 0 and current_gross <= explicit_cap + 1e-12:
                proposed = {symbol: weight * explicit_cap / total for symbol, weight in proposed.items()}
            fully_repaired = bool(
                risk.state.value == "NORMAL"
                and risk.shock_state == "NONE"
                and not risk.freeze_new_risk
                and account.capital_budget_level == 0
                and account.chronic_level == 0
            )
            ordinary_level1_stage = bool(
                account.candidate_tenure.get("recovery_owner_rearm_complete", 0)
                == 0
                and account.capital_budget_level == 1
                and not fully_repaired
                and total > self.cfg.tactical_probe_weight + 1e-12
                and (
                    protected_level1_restore
                    or synchronized_protected_restore
                    or account.candidate_tenure.get(
                        "post_shock_restore_staged", 0
                    )
                    == 1
                )
            )
            if ordinary_level1_stage:
                staged_cap = min(
                    explicit_cap,
                    max(current_gross, self.cfg.tactical_probe_weight),
                )
                staged_base = {
                    symbol: max(0.0, weights_now.get(symbol, 0.0))
                    for symbol in proposed
                }
                buy_gaps = {
                    symbol: max(0.0, desired - staged_base[symbol])
                    for symbol, desired in proposed.items()
                }
                requested = sum(buy_gaps.values())
                remaining = max(0.0, staged_cap - sum(staged_base.values()))
                scale = min(1.0, remaining / requested) if requested > 0 else 0.0
                proposed = {
                    symbol: staged_base[symbol] + buy_gaps[symbol] * scale
                    for symbol in proposed
                    if staged_base[symbol] + buy_gaps[symbol] * scale > 1e-12
                }
                account.candidate_tenure["post_shock_restore_staged"] = 1
            # RiskAssessment is the only owner of the day's gross cap.  A book
            # already below the allowance may BUY saved intent up to that cap.
            # An overweight book keeps full targets here so the single outer
            # reducer can apply global tranche priority to the required SELL.
            # ``protected_weights`` remains alive until the risk engine has
            # observed structural normalization, but it must not remain a
            # permanent rebalancing target.  Once the one economic restore is
            # filled (including every capacity-limited child fill), switch to
            # the same drift-tolerant hold semantics as a mature anchor.  A
            # later independent crisis resets this marker when it captures a
            # fresh protected book.
            restore_complete_key = "post_shock_restore_complete"
            restore_submitted_key = "post_shock_restore_submitted"
            restore_previously_submitted = (
                account.candidate_tenure.get(restore_submitted_key, 0) == 1
            )
            pending_restore_buys = {
                order.symbol
                for order in account.pending_orders
                if order.side == "BUY" and order.symbol in proposed
            }
            executable_buy_gap = {
                symbol: max(
                    0.0,
                    (desired - weights_now.get(symbol, 0.0)) * equity,
                )
                for symbol, desired in proposed.items()
            }
            restoration_trade_threshold = self.cfg.restoration_min_trade_weight * equity
            economic_restore_complete = bool(
                proposed
                and not ordinary_level1_stage
                and not pending_restore_buys
                and (
                    restore_previously_submitted
                    or (
                        fully_repaired
                        and all(
                            gap < restoration_trade_threshold
                            or weights_now.get(symbol, 0.0) >= 0.95 * proposed[symbol]
                            for symbol, gap in executable_buy_gap.items()
                        )
                    )
                )
            )
            if synchronized_protected_restore and proposed and not ordinary_level1_stage:
                # This exact risk transition owns one restoration submission.
                # Once its durable children finish, later price drift must not
                # manufacture a fresh rebalance order for the same event.
                account.candidate_tenure[restore_submitted_key] = 1
            if economic_restore_complete:
                account.candidate_tenure[restore_complete_key] = 1
                account.candidate_tenure[restore_submitted_key] = 0
                account.candidate_tenure["post_shock_restore_staged"] = 0
            if account.candidate_tenure.get(restore_complete_key, 0) == 1:
                return self._targets(
                    proposed={
                        symbol: weights_now.get(symbol, 0.0)
                        for symbol in proposed
                        if weights_now.get(symbol, 0.0) > 1e-12
                    },
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.RECOVERY,
                    reason="completed post-shock restoration; retain price drift",
                )
            # Once risk has fully reopened, restoration is buy-only.  A
            # winner that drifted above its saved target receives a sticky
            # hold reason while lagging members retain the one saved BUY
            # target. During an incomplete repair the severity cap remains a
            # genuine risk reduction and must not receive this exemption.
            restore_reasons = (
                {
                    symbol: "post-shock restoration; retain winner drift"
                    for symbol, desired in proposed.items()
                    if weights_now.get(symbol, 0.0) >= desired - 1e-12
                }
                if fully_repaired
                else None
            )
            return self._targets(
                proposed=proposed,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="confirmed post-shock restoration",
                reasons=restore_reasons,
            )

        # A strategic anchor is deliberately sticky: price drift is allowed to
        # concentrate winners, while account risk remains the sole cut authority.
        anchored_held = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in account.anchor_weights
            if weights_now.get(symbol, 0.0) > 0
        }
        trailed_winners: list[str] = []
        trail_allowed = confirmed_recovery_trail or confirmed_hard_risk_trail
        hard_trail_prefix = "hard_risk_winner_trail:"
        live_gross = sum(max(0.0, weight) for weight in weights_now.values())
        hard_trail_cap_satisfied = bool(
            live_gross
            <= min(self.cfg.max_gross, risk.target_gross_cap) + 1e-12
        )
        if not confirmed_hard_risk_trail:
            for key in tuple(account.replacement_tenure):
                if key.startswith(hard_trail_prefix):
                    account.replacement_tenure[key] = 0
        if trail_allowed:
            for symbol in sorted(anchored_held):
                recovery_position = account.positions.get(symbol)
                price = prices.get(symbol, 0.0)
                if recovery_position is None or price <= 0 or recovery_position.avg_cost <= 0:
                    account.replacement_tenure[f"{hard_trail_prefix}{symbol}"] = 0
                    continue
                mfe = recovery_position.highest_close / recovery_position.avg_cost - 1.0
                peak_giveback = price / max(recovery_position.highest_close, 1e-12) - 1.0
                trail_observed = bool(
                    mfe >= self.cfg.recovery_winner_mfe_arm
                    and peak_giveback <= -self.cfg.recovery_winner_trail
                )
                hard_trail_key = f"{hard_trail_prefix}{symbol}"
                hard_trail_pending = False
                if confirmed_hard_risk_trail:
                    prior_hard_trail = account.replacement_tenure.get(hard_trail_key, 0)
                    hard_trail_pending = prior_hard_trail > 0
                    account.replacement_tenure[hard_trail_key] = (
                        prior_hard_trail + 1
                        if trail_observed or hard_trail_pending
                        else 0
                    )
                if not trail_observed and not hard_trail_pending:
                    continue
                if (
                    confirmed_hard_risk_trail
                    and account.replacement_tenure[hard_trail_key]
                    < self.cfg.concentrated_break_confirm_days
                ):
                    # The outer sparse reducer still enforces the hard gross
                    # cap immediately.  A permanent member exit additionally
                    # waits for the next session to confirm that the same hard
                    # portfolio risk persists; a prior cap trim is not that
                    # second observation.
                    continue
                trailed_winners.append(symbol)
        if trailed_winners:
            proposed = {
                symbol: (
                    min(weight, account.anchor_weights.get(symbol, weight))
                    if confirmed_hard_risk_trail and not hard_trail_cap_satisfied
                    else weight
                )
                for symbol, weight in anchored_held.items()
                if symbol not in trailed_winners
            }
            reasons = {
                symbol: (
                    "recovery winner peak-giveback exit"
                    if symbol in trailed_winners
                    else "mature anchored leader"
                )
                for symbol in anchored_held
            }
            for symbol in trailed_winners:
                account.anchor_weights.pop(symbol, None)
                account.protected_weights.pop(symbol, None)
                account.strategic_restore_weights.pop(symbol, None)
            if not account.anchor_weights:
                self._release_recovery_anchor(account)
                account.candidate_tenure["tactical_cooldown"] = max(
                    account.candidate_tenure.get("tactical_cooldown", 0),
                    self.cfg.tactical_rebound_cooldown_days,
                )
            return self._targets(
                proposed=proposed,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="mature anchored leader",
                reasons=reasons,
            )
        owner_rearm_open = bool(
            account.candidate_tenure.get("recovery_owner_handoff", 0) == 1
            and bool(account.anchor_weights)
            and risk.state is Risk.NORMAL
            and risk.shock_state == "NONE"
            and not risk.freeze_new_risk
            and not bool(risk.evidence.get("freeze_new_risk", False))
            and account.capital_budget_level == 0
            and account.chronic_level == 0
            and not account.protected_weights
            and not account.strategic_restore_weights
            and not account.strategic_cohort_targets
        )
        if owner_rearm_open:
            owner_targets = {
                symbol: min(self.cfg.max_symbol_weight, max(0.0, weight))
                for symbol, weight in account.anchor_weights.items()
                if symbol in user_panel
            }
            explicit_cap = min(
                self.cfg.max_gross,
                max(0.0, risk.target_gross_cap),
            )
            target_gross = sum(owner_targets.values())
            if target_gross > explicit_cap and target_gross > 0:
                owner_targets = {
                    symbol: weight * explicit_cap / target_gross
                    for symbol, weight in owner_targets.items()
                }
            pending_owner_buys = {
                order.symbol
                for order in account.pending_orders
                if order.side == "BUY" and order.symbol in owner_targets
            }
            rearm_submitted_key = "recovery_owner_rearm_submitted"
            previously_submitted = bool(
                account.candidate_tenure.get(rearm_submitted_key, 0) == 1
            )
            rearm_complete = bool(
                previously_submitted
                and not pending_owner_buys
                and all(
                    desired - weights_now.get(symbol, 0.0)
                    < self.cfg.restoration_min_trade_weight
                    or weights_now.get(symbol, 0.0) >= 0.95 * desired
                    for symbol, desired in owner_targets.items()
                )
            )
            if rearm_complete:
                account.candidate_tenure["recovery_owner_handoff"] = 0
                account.candidate_tenure[rearm_submitted_key] = 0
                account.candidate_tenure["recovery_owner_rearm_complete"] = 1
                return self._targets(
                    proposed=anchored_held,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.CORE,
                    reason="completed recovery owner rearm; retain price drift",
                )
            current_owner = {
                symbol: max(0.0, weights_now.get(symbol, 0.0))
                for symbol in owner_targets
            }
            buy_gaps = {
                symbol: max(0.0, desired - current_owner[symbol])
                for symbol, desired in owner_targets.items()
            }
            requested = sum(buy_gaps.values())
            remaining = max(0.0, explicit_cap - sum(current_owner.values()))
            scale = min(1.0, remaining / requested) if requested > 0 else 0.0
            proposed = {
                symbol: current_owner[symbol] + buy_gaps[symbol] * scale
                for symbol in owner_targets
                if current_owner[symbol] + buy_gaps[symbol] * scale > 1e-12
            }
            account.candidate_tenure[rearm_submitted_key] = 1
            return self._targets(
                proposed=proposed,
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="confirmed recovery owner capital rearm",
            )
        anchor_elapsed = 0
        if account.recovery_anchor_date and user_panel:
            anchor_elapsed = self._session_distance(
                self._session_clock(user_panel, date),
                account.recovery_anchor_date,
                date,
            )
        broad_ret120 = float(risk.evidence.get("broad_ret120", 0.0))
        tech_ret120 = float(risk.evidence.get("tech_ret120", 0.0))
        market_ret120_low = min(broad_ret120, tech_ret120)
        market_ret120_high = max(broad_ret120, tech_ret120)
        weak_secular_market = market_ret120_high <= self.cfg.recovery_cohort_weak_market_ret120
        transitional_recovery_market = bool(
            market_ret120_low <= self.cfg.recovery_transition_weak_leg_ret120
            and market_ret120_high <= self.cfg.recovery_transition_strong_leg_max_ret120
            and market_ret120_high - market_ret120_low >= self.cfg.recovery_transition_min_divergence
        )
        tactical_recovery_market = weak_secular_market or transitional_recovery_market
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
            and opportunity in {Opportunity.CHOPPY, Opportunity.TREND, Opportunity.STRONG_TREND}
            and (
                leader_cycle_armed
                or (
                    account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
                    and account.candidate_tenure.get("tactical_promoted", 0) == 0
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
            and not any(
                order.side == "BUY" and order.symbol in account.anchor_weights
                for order in account.pending_orders
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
        has_general_core = not account.anchor_weights and bool(general_core_symbols)
        if (
            opportunity is Opportunity.RECOVERY
            and has_general_core
            and not risk_neutral_recovery_handoff
        ):
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
            and (
                not bool(risk.evidence.get("freeze_new_risk", False))
                or level1_recovery_repair
                or bounded_recovery_repair
            )
            and opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}
            and risk.state.value in {"NORMAL", "CAUTION"}
        ):
            deep_recovery: list[tuple[LeaderScore, float, float]] = []
            rebound: list[LeaderScore] = []
            secular_rebound: list[LeaderScore] = []
            fast_rebound: list[tuple[LeaderScore, float, float]] = []
            required_notional = account.initial_cash * self.cfg.tactical_probe_weight * 0.90
            for symbol, score in leaders.items():
                if symbol not in user_panel or date not in user_panel[symbol].index:
                    continue
                row = user_panel[symbol].loc[date]
                close = scalar(row, "close")
                ma120 = scalar(row, f"ma{self.cfg.trend_slow}")
                ret5 = scalar(row, "ret5", -1.0)
                ret20 = scalar(row, f"ret{self.cfg.trend_fast}", -1.0)
                ret120 = scalar(row, f"ret{self.cfg.trend_slow}", math.nan)
                ret1 = float(user_panel[symbol].loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
                if (
                    ret120 <= -0.35
                    and ret20 >= -0.12
                    and ret5 >= -0.06
                    and ret1 <= -0.05
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(user_panel[symbol], date, required_notional)
                ):
                    deep_recovery.append((score, ret20, ret120))
                if (
                    ret20 <= -0.15
                    and math.isfinite(close)
                    and math.isfinite(ma120)
                    and close >= ma120
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(user_panel[symbol], date, required_notional)
                ):
                    rebound.append(score)
                    if (
                        score.confidence >= self.cfg.leader_min_confidence
                        and math.isfinite(ret120)
                        and ret120 >= 0.0
                        and score.score >= self.cfg.recovery_reserve_min_score
                    ):
                        secular_rebound.append(score)
                if (
                    account.candidate_tenure.get("fast_v_recovery", 0) == 1
                    and ret5 >= 0.10
                    and ret20 < 0
                    and math.isfinite(close)
                    and math.isfinite(ma120)
                    and close >= ma120
                    and self._liquidity_confirmed(user_panel[symbol], date)
                    and self._capacity_confirmed(user_panel[symbol], date, required_notional)
                ):
                    fast_rebound.append((score, ret5, ret20))
            if len(deep_recovery) < 2:
                deep_recovery = [
                    item
                    for item in deep_recovery
                    if item[0].confidence >= self.cfg.leader_min_confidence
                    and item[0].score >= self.cfg.recovery_reserve_min_score
                ]
            if not tactical_recovery_market:
                # A single-name route is still admissible after an observable
                # 35% collapse plus a fresh lower-limit-like washout.  That is
                # independent crash evidence, not a generic market dip.  The
                # shallower rebound/fast-V routes continue to require broad or
                # transitional six-month weakness.
                rebound = secular_rebound
                fast_rebound = []
            if transitional_recovery_market and not weak_secular_market:
                # A divergent index transition is a narrow exception for an
                # independently promotable, deep-crash repair.  It must not
                # turn the ordinary rebound or fast-V branches into dip-buy
                # shortcuts inside a market whose stronger leg is not
                # secularly weak.  The broad weak-market route intentionally
                # keeps its existing candidate set and shorter graduation.
                rebound = secular_rebound
                fast_rebound = []
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
                    fast_v_candidate = account.candidate_tenure.get("fast_v_recovery", 0) == 1
                    account.candidate_tenure["tactical_promotable"] = int(fast_v_candidate)
                    account.tactical_anchor_symbol = pick.symbol if fast_v_candidate else ""
                account.candidate_tenure["tactical_active"] = 1
                return self._targets(
                    proposed={
                        pick.symbol: min(
                            self.cfg.tactical_probe_weight,
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
                pending_buys = {
                    order.symbol
                    for order in account.pending_orders
                    if order.side == "BUY" and order.symbol in account.anchor_weights
                }
                unfinished = {
                    symbol: min(self.cfg.max_symbol_weight, max(0.0, target_weight))
                    for symbol, target_weight in account.anchor_weights.items()
                    if symbol not in proposed or symbol in pending_buys
                }
                if risk.freeze_new_risk and not bounded_recovery_repair:
                    unfinished = {}
                gross_budget = min(
                    self.cfg.max_gross,
                    self._confirmed_recovery_gross(risk=risk, account=account),
                )
                held_gross = sum(
                    min(self.cfg.max_symbol_weight, max(0.0, weight)) for weight in proposed.values()
                )
                requested = sum(
                    max(0.0, target_weight - proposed.get(symbol, 0.0))
                    for symbol, target_weight in unfinished.items()
                )
                remaining = max(0.0, gross_budget - held_gross)
                scale = min(1.0, remaining / requested) if requested > 0 else 0.0
                proposed.update(
                    {
                        symbol: proposed.get(symbol, 0.0)
                        + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale
                        for symbol, target_weight in unfinished.items()
                        if proposed.get(symbol, 0.0)
                        + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale
                        > 1e-12
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
                -0.15 if deep_count >= 2 or (deep_count >= 1 and bool(account.anchor_weights)) else -0.30
            )
            candidates = [item for item in candidates if crash_depth.get(item.symbol, 0.0) <= admission_depth]
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
            candidates.sort(key=lambda item: (crash_depth.get(item.symbol, 0.0), -item.score, item.symbol))
            continuous_freeze = bool(risk.evidence.get("freeze_new_risk", False))
            if (
                continuous_freeze
                and not level1_recovery_repair
                and not risk_neutral_recovery_transfer
            ):
                candidates = []
            if account.anchor_weights and account.recovery_anchor_date:
                recovery_elapsed = self._session_distance(
                    self._session_clock(user_panel, date),
                    account.recovery_anchor_date,
                    date,
                )
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
                candidate_members = set(selected)
                independently_deep_empty_entry = bool(
                    freeze_active
                    and risk.state is Risk.CAUTION
                    and not previous_members
                    and len(candidate_members) == 1
                    and deep_count == 1
                    and all(
                        crash_depth.get(symbol, 0.0) <= admission_depth
                        for symbol in candidate_members
                    )
                )
                if (
                    candidate_members != previous_members
                    and len(candidate_members) < min(3, self.cfg.max_positions)
                    and not independently_deep_empty_entry
                ):
                    admission_key = "recovery_admission:" + ",".join(sorted(candidate_members))
                    for tenure_key in tuple(account.replacement_tenure):
                        if tenure_key.startswith("recovery_admission:") and tenure_key != admission_key:
                            account.replacement_tenure[tenure_key] = 0
                    account.replacement_tenure[admission_key] = (
                        account.replacement_tenure.get(admission_key, 0) + 1
                    )
                    if account.replacement_tenure[admission_key] < self.cfg.recovery_member_confirm_days:
                        return self._targets(
                            proposed=anchored_held,
                            leaders=leaders,
                            account=account,
                            lifecycle=Lifecycle.RECOVERY,
                            reason="awaiting recovery cohort member confirmation",
                        )
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
                    independent_recovery_breadth = bool(
                        int(risk.evidence.get("risk_anchor_group_count", 0))
                        >= self.cfg.risk_anchor_min_groups
                    )
                    if previous_members or not independent_recovery_breadth:
                        # A live tactical anchor already survived its own
                        # causal probe. Expanding its cohort must not sell that
                        # owner merely to manufacture equal starting weights.
                        # The same ownership rule applies when three crash
                        # candidates appear together but independent market
                        # anchors do not yet cover multiple industries: name
                        # count alone is not confirmation of a broad repair.
                        secondary_weight = max(
                            0.0,
                            self.cfg.recovery_target_gross - proposed[lead],
                        ) / len(secondaries)
                        proposed.update(
                            {symbol: secondary_weight for symbol in secondaries}
                        )
                    else:
                        # No member owns an earlier entry. Preserve the causal
                        # crash-depth winner as the conviction anchor while
                        # independent breadth diversifies the residual budget.
                        # Equal weighting erased this evidence and materially
                        # reduced continuous wealth without improving the hard
                        # drawdown line.
                        cohort_gross = self._confirmed_recovery_gross(
                            risk=risk,
                            account=account,
                        )
                        expansive_empty_cohort = bool(
                            not previous_members
                            and int(risk.evidence.get("configured_user_universe_size", 0))
                            >= self.cfg.strategic_expansive_universe_min_size
                        )
                        if expansive_empty_cohort:
                            # Wide pools create more chances for three transient
                            # crash breakouts to coincide. Bound only their first
                            # deployment while preserving the conviction ratio.
                            cohort_gross = min(
                                cohort_gross,
                                self.cfg.recovery_expansive_universe_gross,
                            )
                        if self.cfg.recovery_conviction_weighting_enabled:
                            lead_weight = min(
                                self.cfg.max_symbol_weight,
                                self.cfg.tactical_rebound_weight,
                                cohort_gross
                                * self.cfg.tactical_rebound_weight
                                / self.cfg.recovery_target_gross,
                            )
                            secondary_weight = max(
                                0.0,
                                cohort_gross - lead_weight,
                            ) / len(secondaries)
                            proposed = {
                                lead: lead_weight,
                                **{
                                    symbol: secondary_weight
                                    for symbol in secondaries
                                },
                            }
                        else:
                            member_weight = cohort_gross / len(selected)
                            proposed = {symbol: member_weight for symbol in selected}
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
                owner_targets = dict(proposed)
                if risk_neutral_recovery_transfer:
                    # A confirmed recovery may replace an obsolete Alpha owner
                    # while the capital-budget owner remains closed. Transfer
                    # only the already-deployed gross: this is a sell-funded
                    # ownership handoff, never an exposure exception.
                    handoff_gross = min(
                        sum(max(0.0, weight) for weight in weights_now.values()),
                        self.cfg.max_gross,
                        max(0.0, risk.target_gross_cap),
                    )
                    requested_gross = sum(max(0.0, weight) for weight in proposed.values())
                    if requested_gross > 0:
                        scale = min(1.0, handoff_gross / requested_gross)
                        proposed = {
                            symbol: max(0.0, weight) * scale
                            for symbol, weight in proposed.items()
                            if max(0.0, weight) * scale > 1e-12
                        }
                        if risk_neutral_recovery_handoff:
                            account.protected_weights.clear()
                            account.candidate_tenure["post_shock_restore_complete"] = 0
                            account.candidate_tenure["post_shock_restore_submitted"] = 0
                        account.candidate_tenure["recovery_owner_handoff"] = 1
                account.anchor_weights = owner_targets
                if self.cfg.recovery_conviction_weighting_enabled:
                    # Preserve which name causally led the recovery after the
                    # temporary cohort weights graduate. Crisis reducers can
                    # then retain the evidence owner without treating every
                    # old recovery lot as equally informative.
                    account.recovery_conviction_symbol = lead
                account.candidate_tenure["recovery_cohort_graduated"] = 0
                if len(selected) == 2 and all(crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected):
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
                for tenure_key in tuple(account.replacement_tenure):
                    if tenure_key.startswith("recovery_admission:"):
                        account.replacement_tenure[tenure_key] = 0
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

        if freeze_active and bounded_recovery_repair:
            if any(position.shares > 0 for position in account.positions.values()):
                # The bounded exception reopens only a confirmed recovery BUY;
                # failing to find one does not manufacture an exit for a live
                # generic owner. Preserve existing exposure (and any durable
                # pending reduction) until risk or the strategy explicitly
                # emits a sell.
                return self._frozen_existing_targets(
                    strategy_targets=None,
                    leaders=leaders,
                    account=account,
                    weights_now=weights_now,
                )
            return self._targets(
                proposed={},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.RECOVERY,
                reason="confirmed repair has no bounded recovery candidate",
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
