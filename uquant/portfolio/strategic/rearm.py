"""Bounded strategic reauthorization for a settled all-cash account."""

from __future__ import annotations

import math
from types import MappingProxyType

from ...config import SystemConfig
from ...models.strategic_universe import StrategicUniverseRoles
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    OrderStatus,
    Risk,
    RiskAssessment,
)
from .quorum import strict_absolute_owner_quality


CASH_REARM_HEALTHY_SESSION_LIMITS = MappingProxyType(
    {
        1: 20,
        2: 40,
        3: 60,
        4: 60,
    }
)
CASH_REARM_PROBE_HEALTHY_SESSIONS = CASH_REARM_HEALTHY_SESSION_LIMITS[1]

_REARM_LEVEL_KEY = "strategic_cash_rearm_budget_level"
_REARM_HEALTHY_KEY = "strategic_cash_rearm_healthy_sessions"
_REARM_AUTHORIZED_KEY = "strategic_cash_rearm_authorized"
_REARM_GRANT_KEY = "strategic_cash_rearm_grant"
_REARM_STRICT_KEY = "strategic_cash_rearm_candidate_strict"


def _finite_at_least(value: object, minimum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    converted = float(value)
    return math.isfinite(converted) and converted >= minimum


def _reference_coverage_complete(
    *,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles | None,
    cfg: SystemConfig,
) -> bool:
    if universe is not None and universe.unavailable_reference_symbols:
        return False
    evidence = risk.evidence
    return bool(
        _finite_at_least(evidence.get("reference_coverage"), 1.0)
        and _finite_at_least(evidence.get("risk_anchor_group_count"), 0.0)
        and all(
            _finite_at_least(evidence.get(name), -math.inf)
            for name in (
                "breadth20",
                "broad_ret20",
                "tech_ret20",
                "broad_ret120",
                "tech_ret120",
            )
        )
    )


def _unsettled_execution(account: AccountState) -> bool:
    if account.pending_orders:
        return True
    terminal = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    return any(
        order.status not in terminal
        or (
            order.status == OrderStatus.CANCELLED.value
            and order.remaining_shares > 0
            and order.last_event != "BROKER_CANCELLED"
        )
        for order in account.order_ledger
    )


def _risk_and_market_healthy(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> bool:
    evidence = risk.evidence
    return bool(
        risk.state is Risk.NORMAL
        and account.opportunity
        in {Opportunity.TREND.value, Opportunity.STRONG_TREND.value}
        and risk.target_gross_cap > 0.0
        and risk.votes == 0
        and risk.shock_state == "NONE"
        and _finite_at_least(evidence.get("transition_damage"), 0.0)
        and float(evidence["transition_damage"]) <= cfg.transition_damage_repair
        and not bool(evidence.get("sector_guard_active", False))
        and not bool(evidence.get("strategic_damage_guard", False))
        and not bool(evidence.get("acute_sector_evacuation", False))
        and not bool(evidence.get("sentinel_freeze_new_risk", False))
        and account.chronic_level == 0
        and account.capital_budget_level in CASH_REARM_HEALTHY_SESSION_LIMITS
    )


def _settled_cash_account(account: AccountState) -> bool:
    grant = account.strategic_grant
    return bool(
        not account.positions
        and account.cash > 0.0
        and not _unsettled_execution(account)
        and not account.active_strategic_epoch_id
        and all(epoch.terminal for epoch in account.strategic_epochs)
        and (grant is None or grant.terminal)
        and not account.strategic_cohort_symbols
        and not account.strategic_cohort_targets
        and not account.anchor_weights
        and not account.protected_weights
        and not account.strategic_restore_weights
        and not account.recovery_owner_epoch_id
        and not account.recovery_conviction_symbol
        and not account.tactical_anchor_symbol
    )


def _strict_candidates(
    *,
    universe: StrategicUniverseRoles,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    cfg: SystemConfig,
) -> tuple[str, ...]:
    return tuple(
        symbol
        for symbol in universe.tradable_symbols
        if strict_absolute_owner_quality(
            symbol=symbol,
            snapshots=snapshots,
            leaders=leaders,
            cfg=cfg,
        )
    )


def observe_strategic_cash_rearm(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    candidate_symbol: str,
    qualification_ready: bool,
    observed_session: str,
    previous_observed_session: str,
    cfg: SystemConfig,
) -> bool:
    """Count frozen healthy sessions and authorize one bounded formal probe."""

    level = account.capital_budget_level
    previous_level = account.candidate_tenure.get(_REARM_LEVEL_KEY)
    if previous_level != level:
        account.candidate_tenure[_REARM_LEVEL_KEY] = level
        account.candidate_tenure[_REARM_HEALTHY_KEY] = 0
    strict_candidates = _strict_candidates(
        universe=universe,
        snapshots=snapshots,
        leaders=leaders,
        cfg=cfg,
    )
    healthy = bool(
        _settled_cash_account(account)
        and _risk_and_market_healthy(account=account, risk=risk, cfg=cfg)
        and _reference_coverage_complete(risk=risk, universe=universe, cfg=cfg)
        and strict_candidates
    )
    if healthy and previous_observed_session != observed_session:
        account.candidate_tenure[_REARM_HEALTHY_KEY] = (
            account.candidate_tenure.get(_REARM_HEALTHY_KEY, 0) + 1
        )
    candidate_strict = candidate_symbol in strict_candidates
    authorized = bool(
        healthy
        and qualification_ready
        and candidate_strict
        and account.candidate_tenure.get(_REARM_HEALTHY_KEY, 0)
        >= CASH_REARM_PROBE_HEALTHY_SESSIONS
    )
    account.candidate_tenure[_REARM_AUTHORIZED_KEY] = int(authorized)
    account.candidate_tenure[_REARM_STRICT_KEY] = int(candidate_strict)
    return authorized


def mark_strategic_cash_rearm_grant(account: AccountState) -> None:
    """Consume the one-session authorization into the resulting grant identity."""

    account.candidate_tenure[_REARM_AUTHORIZED_KEY] = 0
    account.candidate_tenure[_REARM_GRANT_KEY] = 1
    account.candidate_tenure[_REARM_STRICT_KEY] = 1
    account.candidate_tenure[_REARM_HEALTHY_KEY] = 0


def set_strategic_cash_rearm_strict(account: AccountState, *, qualified: bool) -> None:
    """Record current strict owner quality for a bounded grant retry."""

    account.candidate_tenure[_REARM_STRICT_KEY] = int(qualified)


def strategic_cash_rearm_grant_open(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> bool:
    """Allow only the bound probe grant through an otherwise active capital freeze."""

    grant = account.strategic_grant
    observation = account.strategic_qualification
    if (
        account.candidate_tenure.get(_REARM_GRANT_KEY, 0) != 1
        or grant is None
        or grant.terminal
        or not grant.epoch_id
        or observation.candidate_symbol != grant.candidate_symbol
        or not observation.qualification_ready
        or (
            observation.deployment_blocked
            and observation.deployment_block_reason != "pending_execution"
        )
        or account.candidate_tenure.get(_REARM_STRICT_KEY, 0) != 1
    ):
        return False
    epoch = next(
        (item for item in account.strategic_epochs if item.epoch_id == grant.epoch_id),
        None,
    )
    if epoch is None or epoch.terminal or epoch.grant_id != grant.grant_id:
        return False
    return bool(
        _risk_and_market_healthy(account=account, risk=risk, cfg=cfg)
        and _reference_coverage_complete(risk=risk, universe=None, cfg=cfg)
        and not observation.unavailable_reference_symbols
        and not account.anchor_weights
        and not account.protected_weights
        and not account.strategic_restore_weights
        and not account.recovery_owner_epoch_id
    )


def strategic_cash_rearm_weight(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> float:
    """Return the existing core probe bounded by every current risk cap."""

    level_cap = (
        cfg.market_crisis_gross
        if account.capital_budget_level >= 4
        else cfg.capital_budget_level3_cap
        if account.capital_budget_level == 3
        else cfg.capital_budget_level2_cap
        if account.capital_budget_level == 2
        else risk.target_gross_cap
    )
    return max(
        0.0,
        min(
            cfg.core_admission_weight,
            cfg.max_symbol_weight,
            risk.target_gross_cap,
            level_cap,
        ),
    )


__all__ = (
    "CASH_REARM_HEALTHY_SESSION_LIMITS",
    "CASH_REARM_PROBE_HEALTHY_SESSIONS",
    "mark_strategic_cash_rearm_grant",
    "observe_strategic_cash_rearm",
    "set_strategic_cash_rearm_strict",
    "strategic_cash_rearm_grant_open",
    "strategic_cash_rearm_weight",
)
