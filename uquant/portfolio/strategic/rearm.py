"""Bounded strategic reauthorization for a settled all-cash account."""

from __future__ import annotations

import math
from copy import deepcopy
from types import MappingProxyType

from ...config import SystemConfig, config_fingerprint
from ...models.strategic_grant import (
    StrategicGrantIntent,
    StrategicQualificationObservation,
)
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
from ...models.strategic_universe import StrategicUniverseRoles
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    Risk,
    RiskAssessment,
)
from .authority import (
    StrategicCapitalAuthorityAssessment,
    assess_strategic_capital_authority,
    normalize_orphan_strategic_capital_residue,
)
from .quorum import route_consistent_owner_quality
from .rearm_predicates import (
    candidate_rearm_predicates,
    flat_book_repair_predicates,
)

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
        return _clear_flat_book_repair(
            account,
            previous=previous,
            observed_session=observed_session,
        )
    target_level, required = flat_book_capital_repair_requirement(
        account.capital_budget_level
    )
    predicates, rejection_reasons = flat_book_repair_predicates(
        account=account,
        risk=risk,
        universe=universe,
        cfg=cfg,
        authority=authority,
        initial_authority=initial_authority,
        normalized_orphans=normalized_orphans,
    )
    reset_reason, start_new_episode = _repair_episode_transition(
        previous=previous,
        account_identity=account_identity,
        capital_budget_level=account.capital_budget_level,
        config_identity=config_identity,
        risk_reference_identity=universe.risk_reference_identity,
        authority=authority,
    )
    if authority.has_live_authority:
        state = _live_authority_repair_state(
            account,
            previous=previous,
            authority=authority,
            account_identity=account_identity,
            capital_budget_level=account.capital_budget_level,
            target_level=target_level,
            required=required,
            observed_session=observed_session,
            risk_reference_identity=universe.risk_reference_identity,
            config_identity=config_identity,
            predicates=predicates,
            rejection_reasons=rejection_reasons,
        )
        account.flat_book_capital_repair = state
        return state
    state = (
        _new_flat_book_repair_state(
            account_identity=account_identity,
            capital_budget_level=account.capital_budget_level,
            target_level=target_level,
            required=required,
            observed_session=observed_session,
            risk_reference_identity=universe.risk_reference_identity,
            config_identity=config_identity,
            reset_reason=reset_reason,
        )
        if start_new_episode
        else deepcopy(previous)
    )
    _advance_flat_book_repair_state(
        state,
        observed_session=observed_session,
        predicates=predicates,
        rejection_reasons=rejection_reasons,
    )
    account.flat_book_capital_repair = state
    return state


def _clear_flat_book_repair(
    account: AccountState,
    *,
    previous: FlatBookCapitalRepairState,
    observed_session: str,
) -> FlatBookCapitalRepairState:
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



def _repair_episode_transition(
    *,
    previous: FlatBookCapitalRepairState,
    account_identity: str,
    capital_budget_level: int,
    config_identity: str,
    risk_reference_identity: str,
    authority: StrategicCapitalAuthorityAssessment,
) -> tuple[str, bool]:
    reset_reason = ""
    start_new_episode = not previous.repair_episode_id
    if not previous.repair_episode_id:
        return reset_reason, start_new_episode
    if account_identity != previous.account_identity:
        reset_reason = FlatBookCapitalRepairResetReason.ACCOUNT_IDENTITY_CHANGED.value
    elif capital_budget_level > previous.capital_budget_level:
        reset_reason = FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_WORSENED.value
    elif capital_budget_level < previous.capital_budget_level:
        reset_reason = FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_IMPROVED.value
    elif config_identity != previous.config_identity:
        reset_reason = FlatBookCapitalRepairResetReason.CONFIG_IDENTITY_CHANGED.value
    elif risk_reference_identity != previous.risk_reference_universe_identity:
        reset_reason = (
            FlatBookCapitalRepairResetReason.RISK_REFERENCE_IDENTITY_CHANGED.value
        )
    elif previous.status == FlatBookCapitalRepairStatus.RESET.value:
        start_new_episode = not authority.has_live_authority
    if reset_reason:
        start_new_episode = True
    return reset_reason, start_new_episode


def _new_flat_book_repair_state(
    *,
    account_identity: str,
    capital_budget_level: int,
    target_level: int,
    required: int,
    observed_session: str,
    risk_reference_identity: str,
    config_identity: str,
    reset_reason: str = "",
) -> FlatBookCapitalRepairState:
    return FlatBookCapitalRepairState(
        repair_episode_id=derive_flat_book_capital_repair_episode_id(
            account_identity=account_identity,
            capital_budget_level=capital_budget_level,
            first_observed_session=observed_session,
            risk_reference_universe_identity=risk_reference_identity,
            config_identity=config_identity,
        ),
        account_identity=account_identity,
        capital_budget_level=capital_budget_level,
        repair_target_level=target_level,
        first_observed_session=observed_session,
        required_healthy_sessions=required,
        risk_reference_universe_identity=risk_reference_identity,
        config_identity=config_identity,
        reset_reason=reset_reason,
        last_reset_session=observed_session if reset_reason else "",
    )


def _live_authority_repair_state(
    account: AccountState,
    *,
    previous: FlatBookCapitalRepairState,
    authority: StrategicCapitalAuthorityAssessment,
    account_identity: str,
    capital_budget_level: int,
    target_level: int,
    required: int,
    observed_session: str,
    risk_reference_identity: str,
    config_identity: str,
    predicates: list[StrategicCashRearmPredicate],
    rejection_reasons: list[str],
) -> FlatBookCapitalRepairState:
    if _unfilled_authorized_grant_attempt(
        account,
        live_authority_fields=authority.live_authority_fields,
    ):
        state = deepcopy(previous)
        state.last_observed_session = observed_session
        state.status = FlatBookCapitalRepairStatus.CONSUMED.value
        state.predicate_results = predicates
        state.rejection_reasons = rejection_reasons
        state.reset_reason = ""
        return state
    state = (
        deepcopy(previous)
        if previous.repair_episode_id
        else _new_flat_book_repair_state(
            account_identity=account_identity,
            capital_budget_level=capital_budget_level,
            target_level=target_level,
            required=required,
            observed_session=observed_session,
            risk_reference_identity=risk_reference_identity,
            config_identity=config_identity,
        )
    )
    state.last_observed_session = observed_session
    state.healthy_session_count = 0
    state.last_counted_session = ""
    state.status = FlatBookCapitalRepairStatus.RESET.value
    state.predicate_results = predicates
    state.rejection_reasons = rejection_reasons
    state.reset_reason = FlatBookCapitalRepairResetReason.LIVE_CAPITAL_AUTHORITY.value
    state.last_reset_session = observed_session
    state.last_ready_session = ""
    return state


def _advance_flat_book_repair_state(
    state: FlatBookCapitalRepairState,
    *,
    observed_session: str,
    predicates: list[StrategicCashRearmPredicate],
    rejection_reasons: list[str],
) -> None:
    state.last_observed_session = observed_session
    state.predicate_results = predicates
    state.rejection_reasons = rejection_reasons
    if rejection_reasons:
        state.status = FlatBookCapitalRepairStatus.BLOCKED.value
        return
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
    if _consumed_rearm_attempt_open(account, previous=previous):
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
    if not _qualification_identity_complete(observation):
        return _invalidate_incomplete_rearm(
            account,
            previous=previous,
            observed_session=observed_session,
        )
    route_quality = route_consistent_owner_quality(
        symbol=observation.candidate_symbol,
        qualification_route=observation.qualification_route,
        quorum_route=observation.qualification_quorum,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
        cfg=cfg,
    )
    predicates, rejection_reasons = candidate_rearm_predicates(
        observation=observation,
        route_quality=route_quality,
        repair=repair,
        universe=universe,
    )
    current = _candidate_rearm_state(
        account,
        previous=previous,
        observation=observation,
        repair=repair,
        universe=universe,
        observed_session=observed_session,
        route_quality=route_quality,
        predicates=predicates,
        rejection_reasons=rejection_reasons,
    )
    if current.authorized:
        _bind_candidate_rearm_authorization(
            account,
            current=current,
            observed_session=observed_session,
        )
    account.strategic_cash_rearm = current
    return current


def _consumed_rearm_attempt_open(
    account: AccountState,
    *,
    previous: StrategicCashRearmState,
) -> bool:
    grant = account.strategic_grant
    return bool(
        previous.status == StrategicCashRearmStatus.CONSUMED.value
        and grant is not None
        and not grant.terminal
        and previous.consumed_grant_id == grant.grant_id
    )


def _qualification_identity_complete(
    observation: StrategicQualificationObservation,
) -> bool:
    return bool(
        observation.candidate_symbol
        and observation.qualification_signature
        and observation.qualification_route
        and observation.qualification_quorum
        and observation.qualification_evidence_sha256
    )


def _invalidate_incomplete_rearm(
    account: AccountState,
    *,
    previous: StrategicCashRearmState,
    observed_session: str,
) -> StrategicCashRearmState:
    if not previous.authorization_id:
        account.strategic_cash_rearm = StrategicCashRearmState()
        return account.strategic_cash_rearm
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
    return invalidated



def _candidate_rearm_state(
    account: AccountState,
    *,
    previous: StrategicCashRearmState,
    observation: StrategicQualificationObservation,
    repair: FlatBookCapitalRepairState,
    universe: StrategicUniverseRoles,
    observed_session: str,
    route_quality: bool,
    predicates: list[StrategicCashRearmPredicate],
    rejection_reasons: list[str],
) -> StrategicCashRearmState:
    authorized = not rejection_reasons
    return StrategicCashRearmState(
        observed_session=observed_session,
        repair_episode_id=repair.repair_episode_id,
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


def _bind_candidate_rearm_authorization(
    account: AccountState,
    *,
    current: StrategicCashRearmState,
    observed_session: str,
) -> None:
    current.authorization_id = derive_strategic_cash_rearm_authorization_id(
        account_identity=_ensure_account_identity(
            account,
            observed_session=observed_session,
        ),
        repair_episode_id=current.repair_episode_id,
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
    if not _rearm_grant_identity_open(
        grant=grant,
        observation=observation,
        rearm=rearm,
    ):
        return False
    if grant is None:
        raise RuntimeError("open strategic rearm grant disappeared")
    epoch = next(
        (item for item in account.strategic_epochs if item.epoch_id == grant.epoch_id),
        None,
    )
    if epoch is None or epoch.terminal or epoch.grant_id != grant.grant_id:
        return False
    return _rearm_capital_context_clear(
        account=account,
        risk=risk,
        cfg=cfg,
    )


def _rearm_grant_identity_open(
    *,
    grant: StrategicGrantIntent | None,
    observation: StrategicQualificationObservation,
    rearm: StrategicCashRearmState,
) -> bool:
    return bool(
        grant is not None
        and not grant.terminal
        and bool(grant.authorization_id)
        and rearm.status == StrategicCashRearmStatus.CONSUMED.value
        and rearm.authorization_id == grant.authorization_id
        and rearm.consumed_grant_id == grant.grant_id
        and bool(grant.epoch_id)
        and observation.candidate_symbol == grant.candidate_symbol
        and observation.qualification_ready
        and not (
            observation.deployment_blocked
            and observation.deployment_block_reason != "pending_execution"
        )
    )


def _rearm_capital_context_clear(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> bool:
    observation = account.strategic_qualification
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
