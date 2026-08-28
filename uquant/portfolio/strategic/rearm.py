"""Bounded strategic reauthorization for a settled all-cash account."""

from __future__ import annotations

import math
from copy import deepcopy
from types import MappingProxyType

from ...config import SystemConfig, config_fingerprint
from ...models.strategic_universe import StrategicUniverseRoles
from ...models.strategic_grant import StrategicQualificationObservation
from ...models.strategic_rearm import (
    FlatBookCapitalRepairResetReason,
    FlatBookCapitalRepairState,
    FlatBookCapitalRepairStatus,
    StrategicCashRearmPredicate,
    StrategicCashRearmRejectionReason,
    StrategicCashRearmState,
    StrategicCashRearmStatus,
    derive_flat_book_capital_repair_episode_id,
    derive_strategic_cash_rearm_authorization_id,
)
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    Risk,
    RiskAssessment,
)
from .authority import (
    assess_strategic_capital_authority,
    normalize_orphan_strategic_capital_residue,
)
from .quorum import route_consistent_owner_quality


CASH_REARM_HEALTHY_SESSION_LIMITS = MappingProxyType(
    {
        1: 20,
        2: 40,
        3: 60,
        4: 60,
    }
)


def flat_book_capital_repair_requirement(capital_budget_level: int) -> tuple[int, int]:
    """Map the persisted damage tier to its repaired tier and frozen bound."""

    if isinstance(capital_budget_level, bool) or capital_budget_level not in (
        CASH_REARM_HEALTHY_SESSION_LIMITS
    ):
        raise ValueError("flat-book capital repair requires a damaged budget tier")
    return (
        capital_budget_level - 1,
        CASH_REARM_HEALTHY_SESSION_LIMITS[capital_budget_level],
    )

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


def _unfilled_authorized_grant_attempt(
    account: AccountState,
    *,
    live_authority_fields: tuple[str, ...],
) -> bool:
    """Return whether live state is only the consumed grant's unfilled attempt."""

    authorization = account.strategic_cash_rearm
    grant = account.strategic_grant
    if (
        authorization.status != StrategicCashRearmStatus.CONSUMED.value
        or grant is None
        or authorization.consumed_grant_id != grant.grant_id
        or authorization.authorization_id != grant.authorization_id
        or grant.filled_shares > 0
        or any(position.shares > 0 for position in account.positions.values())
    ):
        return False
    grant_epochs = [
        epoch for epoch in account.strategic_epochs if epoch.grant_id == grant.grant_id
    ]
    if any(epoch.first_fill_session or epoch.active_session for epoch in grant_epochs):
        return False
    if any(
        order.grant_id != grant.grant_id
        for order in account.pending_orders
    ) or any(
        order.grant_id != grant.grant_id
        for order in account.order_ledger
        if order.status not in {"FILLED", "CANCELLED", "REPLACED"}
    ):
        return False
    allowed_fields = {
        "late_fill_pending",
        "pending_orders",
        "strategic_cohort_symbols",
        "strategic_cohort_targets",
        "strategic_epochs",
        "strategic_grant",
        "unsettled_execution",
    }
    return set(live_authority_fields) <= allowed_fields


def observe_flat_book_capital_repair_state(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    observed_session: str,
    cfg: SystemConfig,
) -> FlatBookCapitalRepairState:
    """Advance one account-owned repair episode without reading qualification."""

    previous = account.flat_book_capital_repair
    account_identity = _ensure_account_identity(
        account,
        observed_session=observed_session,
    )
    config_identity = config_fingerprint(cfg)
    initial_authority = assess_strategic_capital_authority(account)
    normalized_orphans = normalize_orphan_strategic_capital_residue(account)
    authority = assess_strategic_capital_authority(account)
    if account.capital_budget_level not in CASH_REARM_HEALTHY_SESSION_LIMITS:
        if previous.repair_episode_id:
            cleared = deepcopy(previous)
            cleared.last_observed_session = observed_session
            cleared.healthy_session_count = 0
            cleared.status = FlatBookCapitalRepairStatus.RESET.value
            cleared.reset_reason = (
                FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_CLEARED.value
            )
            cleared.last_reset_session = observed_session
            account.flat_book_capital_repair = cleared
        else:
            account.flat_book_capital_repair = FlatBookCapitalRepairState()
        return account.flat_book_capital_repair

    target_level, required = flat_book_capital_repair_requirement(
        account.capital_budget_level
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
        not risk_unavailable
        and _finite_at_least(evidence.get("reference_coverage"), 1.0)
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
    record(
        "NO_LIVE_CAPITAL_AUTHORITY",
        not authority.live_authority_fields,
        {"live_fields": list(authority.live_authority_fields)},
        StrategicCashRearmRejectionReason.LIVE_CAPITAL_AUTHORITY,
        economic_authority=True,
    )
    record(
        "ORPHAN_RESIDUE_NORMALIZED",
        not authority.orphan_residue_fields,
        {
            "detected_fields": list(initial_authority.orphan_residue_fields),
            "normalized_fields": list(normalized_orphans),
        },
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
    record(
        "OPPORTUNITY_TREND",
        account.opportunity
        in {Opportunity.TREND.value, Opportunity.STRONG_TREND.value},
        {"opportunity": account.opportunity},
        StrategicCashRearmRejectionReason.OPPORTUNITY_NOT_TREND,
        economic_authority=True,
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
        "RISK_REFERENCE_COVERAGE_COMPLETE",
        reference_coverage,
        {
            "reference_coverage": evidence.get("reference_coverage"),
            "risk_anchor_group_count": evidence.get("risk_anchor_group_count"),
        },
        StrategicCashRearmRejectionReason.REFERENCE_COVERAGE_INCOMPLETE,
    )
    record(
        "DEPLOYMENT_BLOCK_REPAIRABLE",
        bool(risk.freeze_new_risk or account.capital_budget_level > 0),
        {
            "freeze_new_risk": risk.freeze_new_risk,
            "capital_budget_level": account.capital_budget_level,
        },
        StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE,
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

    reset_reason = ""
    start_new_episode = not previous.repair_episode_id
    if previous.repair_episode_id:
        if account_identity != previous.account_identity:
            reset_reason = (
                FlatBookCapitalRepairResetReason.ACCOUNT_IDENTITY_CHANGED.value
            )
        elif account.capital_budget_level > previous.capital_budget_level:
            reset_reason = (
                FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_WORSENED.value
            )
        elif config_identity != previous.config_identity:
            reset_reason = (
                FlatBookCapitalRepairResetReason.CONFIG_IDENTITY_CHANGED.value
            )
        elif (
            universe.risk_reference_identity
            != previous.risk_reference_universe_identity
        ):
            reset_reason = (
                FlatBookCapitalRepairResetReason.RISK_REFERENCE_IDENTITY_CHANGED.value
            )
        elif previous.status == FlatBookCapitalRepairStatus.RESET.value:
            start_new_episode = not authority.has_live_authority
        if reset_reason:
            start_new_episode = True

    unfilled_attempt = _unfilled_authorized_grant_attempt(
        account,
        live_authority_fields=authority.live_authority_fields,
    )
    if authority.has_live_authority and unfilled_attempt:
        state = deepcopy(previous)
        state.last_observed_session = observed_session
        state.status = FlatBookCapitalRepairStatus.CONSUMED.value
        state.predicate_results = predicates
        state.rejection_reasons = rejection_reasons
        state.reset_reason = ""
        account.flat_book_capital_repair = state
        return state

    if authority.has_live_authority:
        state = deepcopy(previous)
        if not state.repair_episode_id:
            state = FlatBookCapitalRepairState(
                repair_episode_id=derive_flat_book_capital_repair_episode_id(
                    account_identity=account_identity,
                    capital_budget_level=account.capital_budget_level,
                    first_observed_session=observed_session,
                    risk_reference_universe_identity=universe.risk_reference_identity,
                    config_identity=config_identity,
                ),
                account_identity=account_identity,
                capital_budget_level=account.capital_budget_level,
                repair_target_level=target_level,
                first_observed_session=observed_session,
                required_healthy_sessions=required,
                risk_reference_universe_identity=universe.risk_reference_identity,
                config_identity=config_identity,
            )
        state.last_observed_session = observed_session
        state.healthy_session_count = 0
        state.last_counted_session = ""
        state.status = FlatBookCapitalRepairStatus.RESET.value
        state.predicate_results = predicates
        state.rejection_reasons = rejection_reasons
        state.reset_reason = (
            FlatBookCapitalRepairResetReason.LIVE_CAPITAL_AUTHORITY.value
        )
        state.last_reset_session = observed_session
        state.last_ready_session = ""
        account.flat_book_capital_repair = state
        return state

    if start_new_episode:
        state = FlatBookCapitalRepairState(
            repair_episode_id=derive_flat_book_capital_repair_episode_id(
                account_identity=account_identity,
                capital_budget_level=account.capital_budget_level,
                first_observed_session=observed_session,
                risk_reference_universe_identity=universe.risk_reference_identity,
                config_identity=config_identity,
            ),
            account_identity=account_identity,
            capital_budget_level=account.capital_budget_level,
            repair_target_level=target_level,
            first_observed_session=observed_session,
            required_healthy_sessions=required,
            risk_reference_universe_identity=universe.risk_reference_identity,
            config_identity=config_identity,
            reset_reason=reset_reason,
            last_reset_session=observed_session if reset_reason else "",
        )
    else:
        state = deepcopy(previous)
    state.last_observed_session = observed_session
    state.predicate_results = predicates
    state.rejection_reasons = rejection_reasons
    if rejection_reasons:
        state.status = FlatBookCapitalRepairStatus.BLOCKED.value
    else:
        if state.last_counted_session != observed_session:
            state.healthy_session_count = min(
                state.required_healthy_sessions,
                state.healthy_session_count + 1,
            )
            state.last_counted_session = observed_session
        if state.healthy_session_count >= state.required_healthy_sessions:
            state.status = FlatBookCapitalRepairStatus.READY.value
            state.last_ready_session = state.last_ready_session or observed_session
            state.reset_reason = ""
        else:
            state.status = FlatBookCapitalRepairStatus.ACCUMULATING.value
    account.flat_book_capital_repair = state
    return state


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
    """Authorize only the current candidate after account repair is ready."""

    previous = account.strategic_cash_rearm
    if (
        previous.status == StrategicCashRearmStatus.CONSUMED.value
        and account.strategic_grant is not None
        and not account.strategic_grant.terminal
        and previous.consumed_grant_id == account.strategic_grant.grant_id
    ):
        return previous
    repair = observe_flat_book_capital_repair_state(
        account=account,
        risk=risk,
        universe=universe,
        observed_session=observed_session,
        cfg=cfg,
    )
    if not repair.repair_episode_id:
        account.strategic_cash_rearm = StrategicCashRearmState()
        return account.strategic_cash_rearm
    incoming_complete = bool(
        observation.candidate_symbol
        and observation.qualification_signature
        and observation.qualification_route
        and observation.qualification_quorum
        and observation.qualification_evidence_sha256
    )
    if not incoming_complete:
        if previous.authorization_id:
            invalidated = deepcopy(previous)
            invalidated.observed_session = observed_session
            invalidated.status = StrategicCashRearmStatus.INVALIDATED.value
            invalidated.authorized = False
            invalidated.authorization_id = ""
            invalidated.authorized_session = ""
            invalidated.consumed_grant_id = ""
            invalidated.qualification_ready = False
            invalidated.rejection_reasons = [
                StrategicCashRearmRejectionReason.QUALIFICATION_NOT_READY.value
            ]
            account.strategic_cash_rearm = invalidated
        else:
            account.strategic_cash_rearm = StrategicCashRearmState()
        return account.strategic_cash_rearm

    candidate_symbol = observation.candidate_symbol
    route_quality = route_consistent_owner_quality(
        symbol=candidate_symbol,
        qualification_route=observation.qualification_route,
        quorum_route=observation.qualification_quorum,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
        cfg=cfg,
    )
    rearmable_block = bool(
        observation.deployment_blocked
        and observation.deployment_block_reason
        in {"freeze_new_risk", "capital_budget"}
    )
    predicates = [
        _predicate(
            "QUALIFICATION_READY",
            observation.qualification_ready,
            {
                "candidate_symbol": candidate_symbol,
                "qualification_streak": observation.qualification_streak,
            },
        ),
        _predicate(
            "ROUTE_CONSISTENT_OWNER_QUALITY",
            route_quality,
            {
                "candidate_symbol": candidate_symbol,
                "qualification_route": observation.qualification_route,
                "qualification_quorum": observation.qualification_quorum,
            },
        ),
        _predicate(
            "CANDIDATE_TRADABLE",
            candidate_symbol in universe.tradable_symbols,
            {
                "candidate_symbol": candidate_symbol,
                "tradable_symbols": list(universe.tradable_symbols),
            },
            economic_authority=True,
        ),
        _predicate(
            "DEPLOYMENT_BLOCK_REARMABLE",
            rearmable_block,
            {
                "deployment_blocked": observation.deployment_blocked,
                "deployment_block_reason": observation.deployment_block_reason,
                "allowed_reasons": ["capital_budget", "freeze_new_risk"],
            },
            economic_authority=True,
        ),
        _predicate(
            "FLAT_BOOK_REPAIR_READY",
            repair.status == FlatBookCapitalRepairStatus.READY.value,
            {
                "repair_episode_id": repair.repair_episode_id,
                "repair_status": repair.status,
                "healthy_session_count": repair.healthy_session_count,
                "required_healthy_sessions": repair.required_healthy_sessions,
            },
            economic_authority=True,
        ),
    ]
    failures: list[StrategicCashRearmRejectionReason] = []
    if not observation.qualification_ready:
        failures.append(StrategicCashRearmRejectionReason.QUALIFICATION_NOT_READY)
    if not route_quality:
        failures.append(
            StrategicCashRearmRejectionReason.ROUTE_ABSOLUTE_QUALITY_FAILED
        )
    if candidate_symbol not in universe.tradable_symbols:
        failures.append(StrategicCashRearmRejectionReason.CANDIDATE_NOT_TRADABLE)
    if not rearmable_block:
        failures.append(
            StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE
        )
    if repair.status != FlatBookCapitalRepairStatus.READY.value:
        failures.append(
            StrategicCashRearmRejectionReason.FLAT_BOOK_REPAIR_NOT_READY
        )
    reason_order = {
        reason.value: index
        for index, reason in enumerate(StrategicCashRearmRejectionReason)
    }
    rejection_reasons = sorted(
        {reason.value for reason in failures},
        key=reason_order.__getitem__,
    )
    authorized = not rejection_reasons
    current = StrategicCashRearmState(
        observed_session=observed_session,
        repair_episode_id=repair.repair_episode_id,
        candidate_symbol=candidate_symbol,
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
        status=(
            StrategicCashRearmStatus.AUTHORIZED.value
            if authorized
            else StrategicCashRearmStatus.INVALIDATED.value
            if previous.authorization_id
            else StrategicCashRearmStatus.OBSERVING.value
        ),
        authorized_session=observed_session if authorized else "",
        predicate_results=predicates,
        rejection_reasons=rejection_reasons,
        qualification_ready=observation.qualification_ready,
        route_consistent_absolute_quality=route_quality,
        authorized=authorized,
    )
    if authorized:
        current.authorization_id = derive_strategic_cash_rearm_authorization_id(
            account_identity=_ensure_account_identity(
                account,
                observed_session=observed_session,
            ),
            repair_episode_id=repair.repair_episode_id,
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
            authorized_session=observed_session,
        )
    account.strategic_cash_rearm = current
    return current


def consume_strategic_cash_rearm_authorization(
    account: AccountState,
    *,
    grant_id: str,
) -> StrategicCashRearmState:
    """Consume one authorized candidate identity into exactly one grant."""

    if not isinstance(grant_id, str) or not grant_id.startswith("grant_") or len(grant_id) != 70:
        raise ValueError("strategic cash rearm consumption requires a grant identity")
    state = account.strategic_cash_rearm
    if state.status == StrategicCashRearmStatus.CONSUMED.value:
        raise RuntimeError("strategic cash rearm authorization is already consumed")
    if state.status != StrategicCashRearmStatus.AUTHORIZED.value or not state.authorization_id:
        raise RuntimeError("strategic cash rearm authorization is not available")
    consumed = deepcopy(state)
    consumed.status = StrategicCashRearmStatus.CONSUMED.value
    consumed.authorized = False
    consumed.consumed_grant_id = grant_id
    repair = account.flat_book_capital_repair
    if (
        repair.status != FlatBookCapitalRepairStatus.READY.value
        or repair.repair_episode_id != consumed.repair_episode_id
    ):
        raise RuntimeError("strategic rearm authorization lost its ready repair episode")
    consumed_repair = deepcopy(repair)
    consumed_repair.status = FlatBookCapitalRepairStatus.CONSUMED.value
    account.flat_book_capital_repair = consumed_repair
    account.strategic_cash_rearm = consumed
    return consumed


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
    """Observe typed evidence and expose only its one-shot authorization."""

    del candidate_symbol, qualification_ready, previous_observed_session
    state = observe_strategic_cash_rearm_state(
        account=account,
        risk=risk,
        universe=universe,
        snapshots=snapshots,
        leaders=leaders,
        observation=account.strategic_qualification,
        observed_session=observed_session,
        cfg=cfg,
    )
    return state.status == StrategicCashRearmStatus.AUTHORIZED.value


def strategic_cash_rearm_grant_open(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> bool:
    """Allow only the bound probe grant through an otherwise active capital freeze."""

    grant = account.strategic_grant
    observation = account.strategic_qualification
    rearm = account.strategic_cash_rearm
    if (
        grant is None
        or grant.terminal
        or not grant.authorization_id
        or rearm.status != StrategicCashRearmStatus.CONSUMED.value
        or rearm.authorization_id != grant.authorization_id
        or rearm.consumed_grant_id != grant.grant_id
        or not grant.epoch_id
        or observation.candidate_symbol != grant.candidate_symbol
        or not observation.qualification_ready
        or (
            observation.deployment_blocked
            and observation.deployment_block_reason != "pending_execution"
        )
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
    "consume_strategic_cash_rearm_authorization",
    "flat_book_capital_repair_requirement",
    "observe_flat_book_capital_repair_state",
    "observe_strategic_cash_rearm",
    "observe_strategic_cash_rearm_state",
    "strategic_cash_rearm_grant_open",
    "strategic_cash_rearm_weight",
)
