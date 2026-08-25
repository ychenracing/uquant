"""Mechanical Task 8 owner extracted from the immutable allocator."""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import TYPE_CHECKING

from ..portfolio_core import symbol_weight_cap
from ..types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
)

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


type RetentionVector = tuple[float, float, float, float, float, float]


def _risk_attribution_mechanism(reason_code: str) -> AttributionMechanism:
    """Map the risk engine's closed structured code to one mechanism."""

    registry = {
        "sector_guard": AttributionMechanism.SECTOR_GUARD,
        "strategic_damage_guard": AttributionMechanism.STRATEGIC_DAMAGE_GUARD,
        "risk_off": AttributionMechanism.RISK_OFF,
        "crisis": AttributionMechanism.CRISIS,
        "capital_budget": AttributionMechanism.CAPITAL_BUDGET,
        "risk_gross_cap": AttributionMechanism.RISK_GROSS_CAP,
    }
    try:
        return registry[reason_code]
    except KeyError as exc:
        raise RuntimeError(f"risk attribution reason code is not registered: {reason_code}") from exc


def _risk_retention_score(
    self: PortfolioAllocator,
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
        and any(tranche.lifecycle == Lifecycle.RECOVERY.value for tranche in position.tranches)
        else 0.0
    )
    return target.alpha_score + tranche_value + min(0.20, 0.50 * max(0.0, peak_mfe)) + conviction_bonus


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
        account.recovery_conviction_symbol == target.symbol and target.symbol in account.positions
    )
    position = account.positions.get(target.symbol)
    if position is None or not position.tranches:
        lifecycle = target.lifecycle
        buckets = [0.0] * 6
        index = (
            0
            if lifecycle == Lifecycle.RECOVERY.value and (locked_recovery_anchor or conviction_owner)
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
            tranche.lifecycle == Lifecycle.RECOVERY.value and (locked_recovery_anchor or conviction_owner)
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
    return (
        max(0.0, retained[0]),
        max(0.0, retained[1]),
        max(0.0, retained[2]),
        max(0.0, retained[3]),
        max(0.0, retained[4]),
        max(0.0, retained[5]),
    )


def _retention_totals(vectors: list[RetentionVector]) -> RetentionVector:
    return (
        sum(vector[0] for vector in vectors),
        sum(vector[1] for vector in vectors),
        sum(vector[2] for vector in vectors),
        sum(vector[3] for vector in vectors),
        sum(vector[4] for vector in vectors),
        sum(vector[5] for vector in vectors),
    )


def _subset_retention_vector(
    self: PortfolioAllocator,
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
    return _retention_totals(vectors)


def _retained_lifecycle_buckets(
    *,
    full_vectors: dict[str, RetentionVector],
    desired_gross: float,
) -> list[float]:
    retained_by_bucket = [0.0] * 6
    remaining_gross = desired_gross
    for index in range(6):
        available = sum(vector[index] for vector in full_vectors.values())
        retained_by_bucket[index] = min(available, remaining_gross)
        remaining_gross -= retained_by_bucket[index]
    if remaining_gross > 1e-8:
        raise RuntimeError("allocator lifecycle buckets do not reconcile to target gross")
    return retained_by_bucket


def _risk_boundary_bucket(
    *,
    targets: tuple[Target, ...],
    full_vectors: dict[str, RetentionVector],
    retained_by_bucket: list[float],
) -> tuple[dict[str, float], int | None, float]:
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
    return base, boundary_index, boundary_required


def _risk_boundary_plans(
    *,
    base: dict[str, float],
    boundary_index: int | None,
    boundary_required: float,
    full_vectors: dict[str, RetentionVector],
) -> list[dict[str, float]]:
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
                    plan[boundary_symbol] += min(remainder, boundary_capacity[boundary_symbol])
                    candidate_plans.append(plan)
    if not candidate_plans:
        raise RuntimeError("allocator could not construct an exact sparse risk plan")
    return candidate_plans


def _risk_plan_rank(
    self: PortfolioAllocator,
    plan: dict[str, float],
    *,
    target_by_symbol: dict[str, Target],
    account: AccountState,
    weights_now: dict[str, float],
    safe_weights: dict[str, float],
    eligible: tuple[Target, ...],
    prices: dict[str, float] | None,
    risk_reason_code: str,
) -> tuple[object, ...]:
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
    retained_vector = _retention_totals(vectors)
    unchanged = sum(
        abs(plan.get(target.symbol, 0.0) - safe_weights[target.symbol]) <= 1e-12 for target in eligible
    )
    utility = sum(
        self._risk_retention_score(target, account) * plan.get(target.symbol, 0.0) for target in eligible
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
        if risk_reason_code in {"sector_guard", "strategic_damage_guard"}
        else 0.0
    )
    return (
        self._risk_lifecycle_rank(retained_vector),
        sector_guard_health,
        unchanged,
        utility,
        tuple(symbol for symbol in sorted(plan) if plan[symbol] > 1e-12),
    )


def _materialize_risk_reduction_targets(
    self: PortfolioAllocator,
    *,
    targets: tuple[Target, ...],
    retained: dict[str, float],
    weights_now: dict[str, float],
    gross_cap: float,
    risk_reason: str,
    risk_reason_code: str,
    risk_exit_kind: str,
) -> tuple[Target, ...]:
    capped: list[Target] = []
    current_gross = sum(max(0.0, value) for value in weights_now.values())
    for target in targets:
        weight = retained.get(target.symbol, 0.0)
        reason = target.reason
        reduction_policy = target.reduction_policy
        reason_code = target.reason_code
        exit_kind = target.exit_kind
        current_weight = weights_now.get(target.symbol, 0.0)
        reducer_lowered_target = weight + 1e-12 < target.weight
        risk_must_force_positive_trim = bool(
            current_gross > gross_cap + 1e-12 and target.weight > 1e-12 and weight + 1e-12 < current_weight
        )
        risk_override_applied = False
        if weight + 1e-12 < current_weight and (reducer_lowered_target or risk_must_force_positive_trim):
            reason = f"{risk_reason}; {reason}"
            reduction_policy = ReductionPolicy.RISK_PRIORITY.value
            reason_code = risk_reason_code
            exit_kind = risk_exit_kind
            risk_override_applied = True
        capped.append(
            replace(
                target,
                weight=weight,
                reason=reason,
                reduction_policy=reduction_policy,
                reason_code=reason_code,
                exit_kind=exit_kind,
                origin_subsystem=(
                    OriginSubsystem.RISK.value if risk_override_applied else target.origin_subsystem
                ),
                mechanism=(
                    self._risk_attribution_mechanism(risk_reason_code).value
                    if risk_override_applied
                    else target.mechanism
                ),
                origin_lifecycle=(target.origin_lifecycle or target.lifecycle),
                event_id=("" if risk_override_applied else target.event_id),
            )
        )
    if sum(item.weight for item in capped if item.weight > 0) > gross_cap + 1e-8:
        raise RuntimeError("allocator failed to enforce sector risk gross cap")
    return tuple(capped)


def _sparse_risk_reduce(
    self: PortfolioAllocator,
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
            symbol_weight_cap(self.cfg, account, target.symbol),
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
    retained_by_bucket = _retained_lifecycle_buckets(
        full_vectors=full_vectors,
        desired_gross=desired_gross,
    )
    base, boundary_index, boundary_required = _risk_boundary_bucket(
        targets=targets,
        full_vectors=full_vectors,
        retained_by_bucket=retained_by_bucket,
    )
    candidate_plans = _risk_boundary_plans(
        base=base,
        boundary_index=boundary_index,
        boundary_required=boundary_required,
        full_vectors=full_vectors,
    )
    retained = max(
        candidate_plans,
        key=lambda plan: _risk_plan_rank(
            self,
            plan,
            target_by_symbol=target_by_symbol,
            account=account,
            weights_now=weights_now,
            safe_weights=safe_weights,
            eligible=eligible,
            prices=prices,
            risk_reason_code=risk_reason_code,
        ),
    )
    return _materialize_risk_reduction_targets(
        self,
        targets=targets,
        retained=retained,
        weights_now=weights_now,
        gross_cap=gross_cap,
        risk_reason=risk_reason,
        risk_reason_code=risk_reason_code,
        risk_exit_kind=risk_exit_kind,
    )


def _risk_reduction_metadata(risk: RiskAssessment) -> tuple[str, str, str]:
    """Return the causal owner of a hard portfolio gross reduction."""
    if bool(risk.evidence.get("sector_guard_active", False)):
        return ("sector guard gross cap", "sector_guard", "sector_guard")
    if bool(risk.evidence.get("strategic_damage_guard", False)):
        return (
            "strategic transition damage gross cap",
            "strategic_damage_guard",
            "risk",
        )
    if risk.state is Risk.RISK_OFF:
        return ("portfolio risk-off gross cap", "risk_off", "risk_off")
    if risk.state is Risk.CRISIS:
        return ("portfolio crisis gross cap", "crisis", "crisis")
    capital_level = risk.evidence.get("capital_budget_level", 0)
    if isinstance(capital_level, (int, float)) and int(capital_level) >= 2:
        return ("capital budget gross cap", "capital_budget", "capital_budget")
    return ("portfolio risk gross cap", "risk_gross_cap", "risk")


def _turnover_aware_sector_cap(
    self: PortfolioAllocator,
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


risk_attribution_mechanism = _risk_attribution_mechanism
risk_lifecycle_rank = _risk_lifecycle_rank
risk_reduction_metadata = _risk_reduction_metadata
risk_retention_score = _risk_retention_score
risk_retention_vector = _risk_retention_vector
sparse_risk_reduce = _sparse_risk_reduce
subset_retention_vector = _subset_retention_vector
turnover_aware_sector_cap = _turnover_aware_sector_cap
