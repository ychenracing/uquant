"""Bounded strategic reauthorization for a settled all-cash account."""

from __future__ import annotations

import math
from types import MappingProxyType

from ...config import SystemConfig
from ...models.strategic_universe import StrategicUniverseRoles
from ...models.strategic_grant import StrategicQualificationObservation
from ...models.strategic_rearm import (
    StrategicCashRearmPredicate,
    StrategicCashRearmRejectionReason,
    StrategicCashRearmState,
    StrategicCashRearmStatus,
    StrategicCashRearmStreakTransition,
    derive_strategic_cash_rearm_authorization_id,
)
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    OrderStatus,
    Risk,
    RiskAssessment,
)
from .authority import assess_strategic_capital_authority
from .quorum import route_consistent_owner_quality, strict_absolute_owner_quality


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


def _predicate(
    code: str,
    passed: bool,
    authoritative_state: dict[str, object],
    *,
    economic_authority: bool = False,
    orphan_residue: bool = False,
) -> StrategicCashRearmPredicate:
    return StrategicCashRearmPredicate(
        code=code,
        passed=bool(passed),
        authoritative_state=authoritative_state,
        economic_authority=economic_authority,
        orphan_residue=orphan_residue,
    )


def _identity_of(state: StrategicCashRearmState) -> tuple[object, ...]:
    return (
        state.candidate_symbol,
        state.qualification_signature,
        state.qualification_route,
        state.qualification_quorum,
        state.qualification_evidence_sha256,
        state.capital_budget_level,
        state.tradable_universe_identity,
        state.qualification_reference_universe_identity,
        state.risk_reference_universe_identity,
        state.point_in_time_industry_identity,
        state.required_healthy_sessions,
    )


def _ensure_account_identity(account: AccountState, *, observed_session: str) -> str:
    if not account.account_identity:
        import hashlib

        identity_payload = "|".join(
            (
                float(account.initial_cash).hex(),
                account.code_hash or "unbound-production-source",
                observed_session,
            )
        )
        account.account_identity = "account_" + hashlib.sha256(
            identity_payload.encode()
        ).hexdigest()
    return account.account_identity


def observe_strategic_cash_rearm_state(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    observation: StrategicQualificationObservation,
    observed_session: str,
    cfg: SystemConfig,
) -> StrategicCashRearmState:
    """Persist every bounded-rearm predicate without granting economic authority."""

    previous = account.strategic_cash_rearm
    if not (
        observation.candidate_symbol
        and observation.qualification_signature
        and observation.qualification_route
        and observation.qualification_quorum
        and observation.qualification_evidence_sha256
    ):
        account.strategic_cash_rearm = StrategicCashRearmState()
        return account.strategic_cash_rearm

    authority = assess_strategic_capital_authority(account)
    qualification_unavailable = tuple(
        symbol
        for symbol in universe.qualification_reference_symbols
        if symbol not in universe.available_symbols
    )
    risk_unavailable = tuple(
        symbol
        for symbol in universe.risk_reference_symbols
        if symbol not in universe.available_symbols
    )
    evidence = risk.evidence
    transition_damage = evidence.get("transition_damage")
    transition_repaired = bool(
        _finite_at_least(transition_damage, 0.0)
        and float(transition_damage) <= cfg.transition_damage_repair
    )
    reference_coverage = bool(
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
    route_quality = route_consistent_owner_quality(
        symbol=observation.candidate_symbol,
        quorum_route=observation.qualification_quorum,
        snapshots=snapshots,
        leaders=leaders,
        cfg=cfg,
    )
    predicates: list[StrategicCashRearmPredicate] = []
    failures: list[StrategicCashRearmRejectionReason] = []

    def record(
        code: str,
        passed: bool,
        source: dict[str, object],
        reason: StrategicCashRearmRejectionReason,
        *,
        economic_authority: bool = False,
        orphan_residue: bool = False,
    ) -> None:
        predicates.append(
            _predicate(
                code,
                passed,
                source,
                economic_authority=economic_authority,
                orphan_residue=orphan_residue,
            )
        )
        if not passed:
            failures.append(reason)

    record(
        "QUALIFICATION_READY",
        observation.qualification_ready,
        {"qualification_ready": observation.qualification_ready},
        StrategicCashRearmRejectionReason.QUALIFICATION_NOT_READY,
    )
    record(
        "ROUTE_CONSISTENT_OWNER_QUALITY",
        route_quality,
        {
            "candidate_symbol": observation.candidate_symbol,
            "qualification_quorum": observation.qualification_quorum,
        },
        StrategicCashRearmRejectionReason.ROUTE_ABSOLUTE_QUALITY_FAILED,
    )
    record(
        "ALL_CASH",
        authority.all_cash,
        {"positive_position_symbols": list(authority.positive_position_symbols)},
        StrategicCashRearmRejectionReason.NOT_ALL_CASH,
        economic_authority=True,
    )
    record(
        "PENDING_EXECUTION_CLEAR",
        not authority.pending_execution_symbols,
        {"symbols": list(authority.pending_execution_symbols)},
        StrategicCashRearmRejectionReason.PENDING_EXECUTION,
        economic_authority=True,
    )
    record(
        "UNSETTLED_EXECUTION_CLEAR",
        not authority.unsettled_order_ids,
        {"order_ids": list(authority.unsettled_order_ids)},
        StrategicCashRearmRejectionReason.UNSETTLED_EXECUTION,
        economic_authority=True,
    )
    record(
        "LATE_FILL_CLEAR",
        not authority.late_fill_order_ids,
        {"order_ids": list(authority.late_fill_order_ids)},
        StrategicCashRearmRejectionReason.LATE_FILL_PENDING,
        economic_authority=True,
    )
    record(
        "NO_ACTIVE_EPOCH",
        not authority.active_epoch_ids and not account.active_strategic_epoch_id,
        {
            "active_epoch_ids": list(authority.active_epoch_ids),
            "active_pointer": account.active_strategic_epoch_id,
        },
        StrategicCashRearmRejectionReason.ACTIVE_EPOCH,
        economic_authority=True,
    )
    record(
        "NO_NONTERMINAL_EPOCH",
        not authority.nonterminal_epoch_ids,
        {"epoch_ids": list(authority.nonterminal_epoch_ids)},
        StrategicCashRearmRejectionReason.NONTERMINAL_EPOCH,
        economic_authority=True,
    )
    record(
        "NO_NONTERMINAL_GRANT",
        not authority.nonterminal_grant_id,
        {"grant_id": authority.nonterminal_grant_id},
        StrategicCashRearmRejectionReason.NONTERMINAL_GRANT,
        economic_authority=True,
    )
    cohort_live = bool(
        set(authority.live_authority_fields)
        & {"strategic_cohort_symbols", "strategic_cohort_targets"}
    )
    record(
        "NO_LIVE_COHORT_AUTHORITY",
        not cohort_live,
        {"live_authority": cohort_live},
        StrategicCashRearmRejectionReason.LIVE_COHORT_AUTHORITY,
        economic_authority=True,
    )
    for code, fields, reason in (
        (
            "NO_RECOVERY_OWNER",
            {"anchor_weights", "recovery_conviction_symbol", "recovery_owner_epoch_id"},
            StrategicCashRearmRejectionReason.RECOVERY_OWNER,
        ),
        (
            "NO_PROTECTED_OWNER",
            {"protected_weights", "protected_weight_epoch_ids"},
            StrategicCashRearmRejectionReason.PROTECTED_OWNER,
        ),
        (
            "NO_RESTORE_OWNER",
            {"strategic_restore_weights", "strategic_restore_epoch_ids"},
            StrategicCashRearmRejectionReason.RESTORE_OWNER,
        ),
        (
            "NO_TACTICAL_OWNER",
            {"tactical_anchor_symbol"},
            StrategicCashRearmRejectionReason.TACTICAL_OWNER,
        ),
    ):
        live = sorted(set(authority.live_authority_fields) & fields)
        record(
            code,
            not live,
            {"live_fields": live},
            reason,
            economic_authority=True,
        )
    orphan_fields = list(authority.orphan_residue_fields)
    record(
        "ORPHAN_RESIDUE_NORMALIZED",
        not orphan_fields,
        {"orphan_fields": orphan_fields},
        StrategicCashRearmRejectionReason.ORPHAN_RESIDUE_NOT_NORMALIZED,
        orphan_residue=True,
    )

    risk_reason = (
        StrategicCashRearmRejectionReason.RISK_CAUTION
        if risk.state is Risk.CAUTION
        else StrategicCashRearmRejectionReason.RISK_OFF
        if risk.state is Risk.RISK_OFF
        else StrategicCashRearmRejectionReason.RISK_CRISIS
        if risk.state is Risk.CRISIS
        else StrategicCashRearmRejectionReason.RISK_NOT_NORMAL
    )
    record(
        "RISK_NORMAL",
        risk.state is Risk.NORMAL,
        {"risk": risk.state.value},
        risk_reason,
        economic_authority=True,
    )
    record(
        "RISK_VOTES_CLEAR",
        risk.votes == 0,
        {"votes": risk.votes},
        StrategicCashRearmRejectionReason.RISK_VOTES,
        economic_authority=True,
    )
    record(
        "TARGET_GROSS_OPEN",
        risk.target_gross_cap > 0.0,
        {"target_gross_cap": risk.target_gross_cap},
        StrategicCashRearmRejectionReason.TARGET_GROSS_CLOSED,
        economic_authority=True,
    )
    record(
        "SHOCK_CLEAR",
        risk.shock_state == "NONE",
        {"shock_state": risk.shock_state},
        StrategicCashRearmRejectionReason.SHOCK_ACTIVE,
        economic_authority=True,
    )
    record(
        "TRANSITION_DAMAGE_REPAIRED",
        transition_repaired,
        {
            "transition_damage": transition_damage,
            "repair_threshold": cfg.transition_damage_repair,
        },
        StrategicCashRearmRejectionReason.TRANSITION_DAMAGE_UNREPAIRED,
        economic_authority=True,
    )
    for code, active, reason in (
        (
            "SECTOR_GUARD_CLEAR",
            bool(evidence.get("sector_guard_active", False) or account.sector_guard_active),
            StrategicCashRearmRejectionReason.SECTOR_GUARD,
        ),
        (
            "STRATEGIC_DAMAGE_GUARD_CLEAR",
            bool(evidence.get("strategic_damage_guard", False)),
            StrategicCashRearmRejectionReason.STRATEGIC_DAMAGE_GUARD,
        ),
        (
            "ACUTE_EVACUATION_CLEAR",
            bool(evidence.get("acute_sector_evacuation", False)),
            StrategicCashRearmRejectionReason.ACUTE_EVACUATION,
        ),
        (
            "SENTINEL_FREEZE_CLEAR",
            bool(evidence.get("sentinel_freeze_new_risk", False)),
            StrategicCashRearmRejectionReason.SENTINEL_FREEZE,
        ),
    ):
        record(
            code,
            not active,
            {"active": active},
            reason,
            economic_authority=True,
        )
    record(
        "CHRONIC_CLEAR",
        account.chronic_level == 0,
        {"chronic_level": account.chronic_level},
        StrategicCashRearmRejectionReason.CHRONIC_DAMAGE,
        economic_authority=True,
    )
    opportunity_healthy = account.opportunity in {
        Opportunity.TREND.value,
        Opportunity.STRONG_TREND.value,
    }
    record(
        "OPPORTUNITY_TREND",
        opportunity_healthy,
        {"opportunity": account.opportunity},
        StrategicCashRearmRejectionReason.OPPORTUNITY_NOT_TREND,
        economic_authority=True,
    )
    record(
        "QUALIFICATION_REFERENCES_AVAILABLE",
        not qualification_unavailable,
        {
            "expected_symbols": list(universe.qualification_reference_symbols),
            "expected_but_unavailable": list(qualification_unavailable),
        },
        StrategicCashRearmRejectionReason.QUALIFICATION_REFERENCE_UNAVAILABLE,
    )
    record(
        "RISK_REFERENCES_AVAILABLE",
        not risk_unavailable,
        {
            "expected_symbols": list(universe.risk_reference_symbols),
            "expected_but_unavailable": list(risk_unavailable),
        },
        StrategicCashRearmRejectionReason.RISK_REFERENCE_UNAVAILABLE,
    )
    record(
        "REFERENCE_COVERAGE_COMPLETE",
        reference_coverage,
        {
            "reference_coverage": evidence.get("reference_coverage"),
            "risk_anchor_group_count": evidence.get("risk_anchor_group_count"),
        },
        StrategicCashRearmRejectionReason.REFERENCE_COVERAGE_INCOMPLETE,
    )
    budget_rearmable = account.capital_budget_level in CASH_REARM_HEALTHY_SESSION_LIMITS
    record(
        "CAPITAL_BUDGET_REARMABLE",
        budget_rearmable,
        {"capital_budget_level": account.capital_budget_level},
        StrategicCashRearmRejectionReason.CAPITAL_BUDGET_NOT_REARMABLE,
        economic_authority=True,
    )

    reason_order = {
        reason.value: index
        for index, reason in enumerate(StrategicCashRearmRejectionReason)
    }
    rejection_reasons = sorted(
        {reason.value for reason in failures},
        key=reason_order.__getitem__,
    )
    required = CASH_REARM_PROBE_HEALTHY_SESSIONS
    current = StrategicCashRearmState(
        observed_session=observed_session,
        candidate_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_quorum=observation.qualification_quorum,
        qualification_evidence_sha256=observation.qualification_evidence_sha256,
        capital_budget_level=account.capital_budget_level,
        tradable_universe_identity=universe.tradable_identity,
        qualification_reference_universe_identity=(
            universe.qualification_reference_identity
        ),
        risk_reference_universe_identity=universe.risk_reference_identity,
        point_in_time_industry_identity=universe.point_in_time_industry_identity,
        required_healthy_sessions=required,
        predicate_results=predicates,
        rejection_reasons=rejection_reasons,
        qualification_ready=observation.qualification_ready,
        route_consistent_absolute_quality=route_quality,
        healthy=not rejection_reasons,
    )
    same_identity = bool(previous.candidate_symbol) and _identity_of(previous) == _identity_of(current)
    if rejection_reasons:
        current.consecutive_healthy_sessions = 0
        current.status = (
            StrategicCashRearmStatus.INVALIDATED.value
            if previous.consecutive_healthy_sessions or previous.authorization_id
            else StrategicCashRearmStatus.OBSERVING.value
        )
        current.streak_transition = StrategicCashRearmStreakTransition.RESET_UNHEALTHY.value
    elif same_identity and previous.observed_session == observed_session:
        current.consecutive_healthy_sessions = previous.consecutive_healthy_sessions
        current.streak_transition = (
            StrategicCashRearmStreakTransition.HELD_DUPLICATE_SESSION.value
        )
    elif same_identity:
        current.consecutive_healthy_sessions = min(
            required,
            previous.consecutive_healthy_sessions + 1,
        )
        current.streak_transition = StrategicCashRearmStreakTransition.INCREMENTED.value
    else:
        current.consecutive_healthy_sessions = 1
        current.streak_transition = (
            StrategicCashRearmStreakTransition.RESET_IDENTITY.value
            if previous.candidate_symbol
            else StrategicCashRearmStreakTransition.INITIALIZED.value
        )
    if current.healthy and current.consecutive_healthy_sessions >= required:
        account_identity = _ensure_account_identity(
            account,
            observed_session=observed_session,
        )
        current.status = StrategicCashRearmStatus.AUTHORIZED.value
        current.authorized = True
        current.authorized_session = observed_session
        current.authorization_id = derive_strategic_cash_rearm_authorization_id(
            account_identity=account_identity,
            candidate_symbol=current.candidate_symbol,
            qualification_signature=current.qualification_signature,
            qualification_route=current.qualification_route,
            qualification_quorum=current.qualification_quorum,
            qualification_evidence_sha256=current.qualification_evidence_sha256,
            capital_budget_level=current.capital_budget_level,
            tradable_universe_identity=current.tradable_universe_identity,
            qualification_reference_universe_identity=(
                current.qualification_reference_universe_identity
            ),
            risk_reference_universe_identity=current.risk_reference_universe_identity,
            point_in_time_industry_identity=current.point_in_time_industry_identity,
            required_healthy_sessions=current.required_healthy_sessions,
        )
        current.streak_transition = StrategicCashRearmStreakTransition.AUTHORIZED.value
    account.strategic_cash_rearm = current
    return current


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

    observe_strategic_cash_rearm_state(
        account=account,
        risk=risk,
        universe=universe,
        snapshots=snapshots,
        leaders=leaders,
        observation=account.strategic_qualification,
        observed_session=observed_session,
        cfg=cfg,
    )

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
    "observe_strategic_cash_rearm_state",
    "set_strategic_cash_rearm_strict",
    "strategic_cash_rearm_grant_open",
    "strategic_cash_rearm_weight",
)
