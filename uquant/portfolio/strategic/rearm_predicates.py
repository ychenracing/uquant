"""Auditable predicate families for bounded strategic reauthorization."""

from __future__ import annotations

import math

from ...config import SystemConfig
from ...models.strategic_grant import StrategicQualificationObservation
from ...models.strategic_rearm import (
    FlatBookCapitalRepairState,
    FlatBookCapitalRepairStatus,
    StrategicCashRearmPredicate,
    StrategicCashRearmRejectionReason,
)
from ...models.strategic_universe import StrategicUniverseRoles
from ...types import AccountState, Opportunity, Risk, RiskAssessment
from .authority import StrategicCapitalAuthorityAssessment


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


def _repair_predicate_row(
    code: str,
    passed: bool,
    source: dict[str, object],
    reason: StrategicCashRearmRejectionReason,
    *,
    economic_authority: bool = False,
    orphan_residue: bool = False,
) -> tuple[StrategicCashRearmPredicate, StrategicCashRearmRejectionReason]:
    return (
        _predicate(
            code,
            passed,
            source,
            economic_authority=economic_authority,
            orphan_residue=orphan_residue,
        ),
        reason,
    )


def _authority_repair_predicates(
    *,
    account: AccountState,
    authority: StrategicCapitalAuthorityAssessment,
    initial_authority: StrategicCapitalAuthorityAssessment,
    normalized_orphans: tuple[str, ...],
) -> list[tuple[StrategicCashRearmPredicate, StrategicCashRearmRejectionReason]]:
    return [
        _repair_predicate_row(
            "ALL_CASH",
            authority.all_cash,
            {"positive_position_symbols": list(authority.positive_position_symbols)},
            StrategicCashRearmRejectionReason.NOT_ALL_CASH,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "PENDING_EXECUTION_CLEAR",
            not authority.pending_execution_symbols,
            {"symbols": list(authority.pending_execution_symbols)},
            StrategicCashRearmRejectionReason.PENDING_EXECUTION,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "UNSETTLED_EXECUTION_CLEAR",
            not authority.unsettled_order_ids,
            {"order_ids": list(authority.unsettled_order_ids)},
            StrategicCashRearmRejectionReason.UNSETTLED_EXECUTION,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "LATE_FILL_CLEAR",
            not authority.late_fill_order_ids,
            {"order_ids": list(authority.late_fill_order_ids)},
            StrategicCashRearmRejectionReason.LATE_FILL_PENDING,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "NO_ACTIVE_EPOCH",
            not authority.active_epoch_ids
            and not account.active_strategic_epoch_id,
            {
                "active_epoch_ids": list(authority.active_epoch_ids),
                "active_pointer": account.active_strategic_epoch_id,
            },
            StrategicCashRearmRejectionReason.ACTIVE_EPOCH,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "NO_NONTERMINAL_EPOCH",
            not authority.nonterminal_epoch_ids,
            {"epoch_ids": list(authority.nonterminal_epoch_ids)},
            StrategicCashRearmRejectionReason.NONTERMINAL_EPOCH,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "NO_NONTERMINAL_GRANT",
            not authority.nonterminal_grant_id,
            {"grant_id": authority.nonterminal_grant_id},
            StrategicCashRearmRejectionReason.NONTERMINAL_GRANT,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "NO_LIVE_CAPITAL_AUTHORITY",
            not authority.live_authority_fields,
            {"live_fields": list(authority.live_authority_fields)},
            StrategicCashRearmRejectionReason.LIVE_CAPITAL_AUTHORITY,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "ORPHAN_RESIDUE_NORMALIZED",
            not authority.orphan_residue_fields,
            {
                "detected_fields": list(initial_authority.orphan_residue_fields),
                "normalized_fields": list(normalized_orphans),
            },
            StrategicCashRearmRejectionReason.ORPHAN_RESIDUE_NOT_NORMALIZED,
            orphan_residue=True,
        ),
    ]


def _risk_repair_predicates(
    *,
    account: AccountState,
    risk: RiskAssessment,
    cfg: SystemConfig,
) -> list[tuple[StrategicCashRearmPredicate, StrategicCashRearmRejectionReason]]:
    evidence = risk.evidence
    transition_damage = evidence.get("transition_damage")
    transition_repaired = bool(
        isinstance(transition_damage, (int, float))
        and not isinstance(transition_damage, bool)
        and _finite_repair_evidence(transition_damage, 0.0)
        and float(transition_damage) <= cfg.transition_damage_repair
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
    rows = [
        _repair_predicate_row(
            "RISK_NORMAL",
            risk.state is Risk.NORMAL,
            {"risk": risk.state.value},
            risk_reason,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "RISK_VOTES_CLEAR",
            risk.votes == 0,
            {"votes": risk.votes},
            StrategicCashRearmRejectionReason.RISK_VOTES,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "TARGET_GROSS_OPEN",
            risk.target_gross_cap > 0.0,
            {"target_gross_cap": risk.target_gross_cap},
            StrategicCashRearmRejectionReason.TARGET_GROSS_CLOSED,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "SHOCK_CLEAR",
            risk.shock_state == "NONE",
            {"shock_state": risk.shock_state},
            StrategicCashRearmRejectionReason.SHOCK_ACTIVE,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "TRANSITION_DAMAGE_REPAIRED",
            transition_repaired,
            {
                "transition_damage": transition_damage,
                "repair_threshold": cfg.transition_damage_repair,
            },
            StrategicCashRearmRejectionReason.TRANSITION_DAMAGE_UNREPAIRED,
            economic_authority=True,
        ),
    ]
    guard_states = (
        (
            "SECTOR_GUARD_CLEAR",
            bool(
                evidence.get("sector_guard_active", False)
                or account.sector_guard_active
            ),
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
    )
    rows.extend(
        _repair_predicate_row(
            code,
            not active,
            {"active": active},
            reason,
            economic_authority=True,
        )
        for code, active, reason in guard_states
    )
    rows.extend(
        (
            _repair_predicate_row(
                "CHRONIC_CLEAR",
                account.chronic_level == 0,
                {"chronic_level": account.chronic_level},
                StrategicCashRearmRejectionReason.CHRONIC_DAMAGE,
                economic_authority=True,
            ),
            _repair_predicate_row(
                "OPPORTUNITY_TREND",
                account.opportunity
                in {Opportunity.TREND.value, Opportunity.STRONG_TREND.value},
                {"opportunity": account.opportunity},
                StrategicCashRearmRejectionReason.OPPORTUNITY_NOT_TREND,
                economic_authority=True,
            ),
        )
    )
    return rows


def _reference_repair_predicates(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
) -> list[tuple[StrategicCashRearmPredicate, StrategicCashRearmRejectionReason]]:
    unavailable = tuple(
        symbol
        for symbol in universe.risk_reference_symbols
        if symbol not in universe.available_symbols
    )
    evidence = risk.evidence
    coverage = bool(
        not unavailable
        and _finite_repair_evidence(evidence.get("reference_coverage"), 1.0)
        and _finite_repair_evidence(evidence.get("risk_anchor_group_count"), 0.0)
        and all(
            _finite_repair_evidence(evidence.get(name), -math.inf)
            for name in (
                "breadth20",
                "broad_ret20",
                "tech_ret20",
                "broad_ret120",
                "tech_ret120",
            )
        )
    )
    return [
        _repair_predicate_row(
            "RISK_REFERENCES_AVAILABLE",
            not unavailable,
            {
                "expected_symbols": list(universe.risk_reference_symbols),
                "expected_but_unavailable": list(unavailable),
            },
            StrategicCashRearmRejectionReason.RISK_REFERENCE_UNAVAILABLE,
        ),
        _repair_predicate_row(
            "RISK_REFERENCE_COVERAGE_COMPLETE",
            coverage,
            {
                "reference_coverage": evidence.get("reference_coverage"),
                "risk_anchor_group_count": evidence.get("risk_anchor_group_count"),
            },
            StrategicCashRearmRejectionReason.REFERENCE_COVERAGE_INCOMPLETE,
        ),
        _repair_predicate_row(
            "DEPLOYMENT_BLOCK_REPAIRABLE",
            bool(risk.freeze_new_risk or account.capital_budget_level > 0),
            {
                "freeze_new_risk": risk.freeze_new_risk,
                "capital_budget_level": account.capital_budget_level,
            },
            StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE,
            economic_authority=True,
        ),
    ]


def flat_book_repair_predicates(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    cfg: SystemConfig,
    authority: StrategicCapitalAuthorityAssessment,
    initial_authority: StrategicCapitalAuthorityAssessment,
    normalized_orphans: tuple[str, ...],
) -> tuple[list[StrategicCashRearmPredicate], list[str]]:
    rows = [
        *_authority_repair_predicates(
            account=account,
            authority=authority,
            initial_authority=initial_authority,
            normalized_orphans=normalized_orphans,
        ),
        *_risk_repair_predicates(account=account, risk=risk, cfg=cfg),
        *_reference_repair_predicates(
            account=account,
            risk=risk,
            universe=universe,
        ),
    ]
    predicates = [predicate for predicate, _reason in rows]
    failures = {reason.value for predicate, reason in rows if not predicate.passed}
    reason_order = {
        reason.value: index
        for index, reason in enumerate(StrategicCashRearmRejectionReason)
    }
    return predicates, sorted(failures, key=reason_order.__getitem__)


def candidate_rearm_predicates(
    *,
    observation: StrategicQualificationObservation,
    route_quality: bool,
    repair: FlatBookCapitalRepairState,
    universe: StrategicUniverseRoles,
) -> tuple[list[StrategicCashRearmPredicate], list[str]]:
    candidate_symbol = observation.candidate_symbol
    rearmable_block = bool(
        observation.deployment_blocked
        and observation.deployment_block_reason
        in {"freeze_new_risk", "capital_budget"}
    )
    rows = [
        _repair_predicate_row(
            "QUALIFICATION_READY",
            observation.qualification_ready,
            {
                "candidate_symbol": candidate_symbol,
                "qualification_streak": observation.qualification_streak,
            },
            StrategicCashRearmRejectionReason.QUALIFICATION_NOT_READY,
        ),
        _repair_predicate_row(
            "ROUTE_CONSISTENT_OWNER_QUALITY",
            route_quality,
            {
                "candidate_symbol": candidate_symbol,
                "qualification_route": observation.qualification_route,
                "qualification_quorum": observation.qualification_quorum,
            },
            StrategicCashRearmRejectionReason.ROUTE_ABSOLUTE_QUALITY_FAILED,
        ),
        _repair_predicate_row(
            "CANDIDATE_TRADABLE",
            candidate_symbol in universe.tradable_symbols,
            {
                "candidate_symbol": candidate_symbol,
                "tradable_symbols": list(universe.tradable_symbols),
            },
            StrategicCashRearmRejectionReason.CANDIDATE_NOT_TRADABLE,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "DEPLOYMENT_BLOCK_REARMABLE",
            rearmable_block,
            {
                "deployment_blocked": observation.deployment_blocked,
                "deployment_block_reason": observation.deployment_block_reason,
                "allowed_reasons": ["capital_budget", "freeze_new_risk"],
            },
            StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE,
            economic_authority=True,
        ),
        _repair_predicate_row(
            "FLAT_BOOK_REPAIR_READY",
            repair.status == FlatBookCapitalRepairStatus.READY.value,
            {
                "repair_episode_id": repair.repair_episode_id,
                "repair_status": repair.status,
                "healthy_session_count": repair.healthy_session_count,
                "required_healthy_sessions": repair.required_healthy_sessions,
            },
            StrategicCashRearmRejectionReason.FLAT_BOOK_REPAIR_NOT_READY,
            economic_authority=True,
        ),
    ]
    reason_order = {
        reason.value: index
        for index, reason in enumerate(StrategicCashRearmRejectionReason)
    }
    failures = {reason.value for predicate, reason in rows if not predicate.passed}
    return (
        [predicate for predicate, _reason in rows],
        sorted(failures, key=reason_order.__getitem__),
    )


def _finite_repair_evidence(value: object, minimum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    converted = float(value)
    return math.isfinite(converted) and converted >= minimum


__all__ = (
    "candidate_rearm_predicates",
    "flat_book_repair_predicates",
)
