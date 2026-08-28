"""Durable bounded strategic cash reauthorization evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Any


class StrategicCashRearmStatus(str, Enum):
    """Lifecycle of one candidate-bound cash reauthorization observation."""

    OBSERVING = "OBSERVING"
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class StrategicCashRearmStreakTransition(str, Enum):
    """Why the durable consecutive-session count changed or stayed fixed."""

    INITIALIZED = "INITIALIZED"
    INCREMENTED = "INCREMENTED"
    HELD_DUPLICATE_SESSION = "HELD_DUPLICATE_SESSION"
    RESET_IDENTITY = "RESET_IDENTITY"
    RESET_UNHEALTHY = "RESET_UNHEALTHY"
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class StrategicCashRearmRejectionReason(str, Enum):
    """Stable fail-closed reasons in canonical diagnostic order."""

    QUALIFICATION_NOT_READY = "QUALIFICATION_NOT_READY"
    ROUTE_ABSOLUTE_QUALITY_FAILED = "ROUTE_ABSOLUTE_QUALITY_FAILED"
    NOT_ALL_CASH = "NOT_ALL_CASH"
    PENDING_EXECUTION = "PENDING_EXECUTION"
    UNSETTLED_EXECUTION = "UNSETTLED_EXECUTION"
    LATE_FILL_PENDING = "LATE_FILL_PENDING"
    ACTIVE_EPOCH = "ACTIVE_EPOCH"
    NONTERMINAL_EPOCH = "NONTERMINAL_EPOCH"
    NONTERMINAL_GRANT = "NONTERMINAL_GRANT"
    LIVE_COHORT_AUTHORITY = "LIVE_COHORT_AUTHORITY"
    RECOVERY_OWNER = "RECOVERY_OWNER"
    PROTECTED_OWNER = "PROTECTED_OWNER"
    RESTORE_OWNER = "RESTORE_OWNER"
    TACTICAL_OWNER = "TACTICAL_OWNER"
    RISK_CAUTION = "RISK_CAUTION"
    RISK_OFF = "RISK_OFF"
    RISK_CRISIS = "RISK_CRISIS"
    RISK_NOT_NORMAL = "RISK_NOT_NORMAL"
    RISK_VOTES = "RISK_VOTES"
    TARGET_GROSS_CLOSED = "TARGET_GROSS_CLOSED"
    SHOCK_ACTIVE = "SHOCK_ACTIVE"
    TRANSITION_DAMAGE_UNREPAIRED = "TRANSITION_DAMAGE_UNREPAIRED"
    SECTOR_GUARD = "SECTOR_GUARD"
    STRATEGIC_DAMAGE_GUARD = "STRATEGIC_DAMAGE_GUARD"
    ACUTE_EVACUATION = "ACUTE_EVACUATION"
    SENTINEL_FREEZE = "SENTINEL_FREEZE"
    CHRONIC_DAMAGE = "CHRONIC_DAMAGE"
    OPPORTUNITY_NOT_TREND = "OPPORTUNITY_NOT_TREND"
    QUALIFICATION_REFERENCE_UNAVAILABLE = "QUALIFICATION_REFERENCE_UNAVAILABLE"
    RISK_REFERENCE_UNAVAILABLE = "RISK_REFERENCE_UNAVAILABLE"
    REFERENCE_COVERAGE_INCOMPLETE = "REFERENCE_COVERAGE_INCOMPLETE"
    CAPITAL_BUDGET_NOT_REARMABLE = "CAPITAL_BUDGET_NOT_REARMABLE"
    DEPLOYMENT_BLOCK_NOT_REARMABLE = "DEPLOYMENT_BLOCK_NOT_REARMABLE"
    ORPHAN_RESIDUE_NOT_NORMALIZED = "ORPHAN_RESIDUE_NOT_NORMALIZED"


_REJECTION_ORDER = {
    reason.value: index for index, reason in enumerate(StrategicCashRearmRejectionReason)
}


@dataclass(slots=True)
class StrategicCashRearmPredicate:
    """One auditable safety predicate and the authoritative state it read."""

    code: str
    passed: bool
    authoritative_state: dict[str, Any] = field(default_factory=dict)
    economic_authority: bool = False
    orphan_residue: bool = False


@dataclass(slots=True)
class StrategicCashRearmState:
    """Persistent candidate-bound bounded reauthorization state."""

    observed_session: str = ""
    candidate_symbol: str = ""
    qualification_signature: str = ""
    qualification_route: str = ""
    qualification_quorum: str = ""
    qualification_evidence_sha256: str = ""
    capital_budget_level: int = 0
    tradable_universe_identity: str = ""
    qualification_reference_universe_identity: str = ""
    risk_reference_universe_identity: str = ""
    point_in_time_industry_identity: str = ""
    required_healthy_sessions: int = 0
    consecutive_healthy_sessions: int = 0
    status: str = StrategicCashRearmStatus.OBSERVING.value
    authorization_id: str = ""
    authorized_session: str = ""
    consumed_grant_id: str = ""
    predicate_results: list[StrategicCashRearmPredicate] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    qualification_ready: bool = False
    route_consistent_absolute_quality: bool = False
    healthy: bool = False
    authorized: bool = False
    streak_transition: str = StrategicCashRearmStreakTransition.INITIALIZED.value


def _require_text(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"strategic cash rearm {field_name} must be non-empty text")
    return value


def _require_session(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    text = _require_text(value, field_name=field_name, allow_empty=allow_empty)
    if allow_empty and not text:
        return text
    try:
        date_type.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"strategic cash rearm {field_name} must be an ISO date") from exc
    return text


def _require_sha256(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    text = _require_text(value, field_name=field_name, allow_empty=allow_empty)
    if allow_empty and not text:
        return text
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"strategic cash rearm {field_name} must be SHA-256")
    return text


def derive_strategic_cash_rearm_authorization_id(
    *,
    account_identity: str,
    candidate_symbol: str,
    qualification_signature: str,
    qualification_route: str,
    qualification_quorum: str,
    qualification_evidence_sha256: str,
    capital_budget_level: int,
    tradable_universe_identity: str,
    qualification_reference_universe_identity: str,
    risk_reference_universe_identity: str,
    point_in_time_industry_identity: str,
    required_healthy_sessions: int,
) -> str:
    """Derive a stable one-shot authorization from causal economic evidence."""

    for field_name, value in (
        ("account_identity", account_identity),
        ("candidate_symbol", candidate_symbol),
        ("qualification_signature", qualification_signature),
        ("qualification_route", qualification_route),
        ("qualification_quorum", qualification_quorum),
    ):
        _require_text(value, field_name=field_name)
    for field_name, value in (
        ("qualification_evidence_sha256", qualification_evidence_sha256),
        ("tradable_universe_identity", tradable_universe_identity),
        (
            "qualification_reference_universe_identity",
            qualification_reference_universe_identity,
        ),
        ("risk_reference_universe_identity", risk_reference_universe_identity),
        ("point_in_time_industry_identity", point_in_time_industry_identity),
    ):
        _require_sha256(value, field_name=field_name)
    if isinstance(capital_budget_level, bool) or capital_budget_level not in {1, 2, 3, 4}:
        raise ValueError("strategic cash rearm capital_budget_level must be between one and four")
    if (
        isinstance(required_healthy_sessions, bool)
        or not isinstance(required_healthy_sessions, int)
        or required_healthy_sessions <= 0
    ):
        raise ValueError("strategic cash rearm required_healthy_sessions must be positive")
    payload = {
        "account_identity": account_identity,
        "candidate_symbol": candidate_symbol,
        "qualification_signature": qualification_signature,
        "qualification_route": qualification_route,
        "qualification_quorum": qualification_quorum,
        "qualification_evidence_sha256": qualification_evidence_sha256,
        "capital_budget_level": capital_budget_level,
        "tradable_universe_identity": tradable_universe_identity,
        "qualification_reference_universe_identity": (
            qualification_reference_universe_identity
        ),
        "risk_reference_universe_identity": risk_reference_universe_identity,
        "point_in_time_industry_identity": point_in_time_industry_identity,
        "required_healthy_sessions": required_healthy_sessions,
        "schema": "uquant.strategic-cash-rearm-authorization",
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "rearm_" + hashlib.sha256(encoded).hexdigest()


def _validate_predicate(predicate: StrategicCashRearmPredicate) -> None:
    _require_text(predicate.code, field_name="predicate code")
    if type(predicate.passed) is not bool:
        raise ValueError("strategic cash rearm predicate passed must be boolean")
    if type(predicate.economic_authority) is not bool or type(predicate.orphan_residue) is not bool:
        raise ValueError("strategic cash rearm predicate authority flags must be boolean")
    if not isinstance(predicate.authoritative_state, dict):
        raise ValueError("strategic cash rearm predicate authoritative_state must be an object")
    try:
        json.dumps(predicate.authoritative_state, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "strategic cash rearm predicate authoritative_state must be canonical JSON"
        ) from exc


def validate_strategic_cash_rearm_state(state: StrategicCashRearmState) -> None:
    """Reject malformed, non-deterministic, or contradictory durable rearm state."""

    status = StrategicCashRearmStatus(state.status)
    StrategicCashRearmStreakTransition(state.streak_transition)
    empty = not state.observed_session and not state.candidate_symbol
    if empty:
        if state != StrategicCashRearmState():
            raise ValueError("empty strategic cash rearm state contains durable evidence")
        return
    _require_session(state.observed_session, field_name="observed_session")
    for field_name, value in (
        ("candidate_symbol", state.candidate_symbol),
        ("qualification_signature", state.qualification_signature),
        ("qualification_route", state.qualification_route),
        ("qualification_quorum", state.qualification_quorum),
    ):
        _require_text(value, field_name=field_name)
    for field_name, value in (
        ("qualification_evidence_sha256", state.qualification_evidence_sha256),
        ("tradable_universe_identity", state.tradable_universe_identity),
        (
            "qualification_reference_universe_identity",
            state.qualification_reference_universe_identity,
        ),
        ("risk_reference_universe_identity", state.risk_reference_universe_identity),
        ("point_in_time_industry_identity", state.point_in_time_industry_identity),
    ):
        _require_sha256(value, field_name=field_name)
    for field_name, value in (
        ("capital_budget_level", state.capital_budget_level),
        ("required_healthy_sessions", state.required_healthy_sessions),
        ("consecutive_healthy_sessions", state.consecutive_healthy_sessions),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"strategic cash rearm {field_name} must be non-negative")
    if state.capital_budget_level > 4:
        raise ValueError("strategic cash rearm capital budget exceeds its ladder")
    if state.required_healthy_sessions <= 0:
        raise ValueError("strategic cash rearm required healthy sessions must be positive")
    if state.consecutive_healthy_sessions > state.required_healthy_sessions:
        raise ValueError("strategic cash rearm healthy streak exceeds its bound")
    if any(type(value) is not bool for value in (
        state.qualification_ready,
        state.route_consistent_absolute_quality,
        state.healthy,
        state.authorized,
    )):
        raise ValueError("strategic cash rearm result flags must be boolean")
    predicate_codes = [predicate.code for predicate in state.predicate_results]
    if len(predicate_codes) != len(set(predicate_codes)):
        raise ValueError("strategic cash rearm predicate codes must be unique")
    for predicate in state.predicate_results:
        _validate_predicate(predicate)
    try:
        canonical_rejections = [
            StrategicCashRearmRejectionReason(reason).value
            for reason in state.rejection_reasons
        ]
    except ValueError as exc:
        raise ValueError("strategic cash rearm rejection reason is invalid") from exc
    if len(canonical_rejections) != len(set(canonical_rejections)):
        raise ValueError("strategic cash rearm rejection reasons must be unique")
    if canonical_rejections != sorted(
        canonical_rejections,
        key=_REJECTION_ORDER.__getitem__,
    ):
        raise ValueError("rearm rejection reasons must be ordered")
    _require_session(state.authorized_session, field_name="authorized_session", allow_empty=True)
    _require_text(state.consumed_grant_id, field_name="consumed_grant_id", allow_empty=True)
    if status is StrategicCashRearmStatus.AUTHORIZED:
        if state.rejection_reasons:
            raise ValueError("authorized rearm cannot retain rejection reasons")
        if not (
            state.authorized
            and state.healthy
            and state.qualification_ready
            and state.route_consistent_absolute_quality
            and state.capital_budget_level in {1, 2, 3, 4}
            and state.consecutive_healthy_sessions == state.required_healthy_sessions
            and state.authorized_session
        ):
            raise ValueError("authorized strategic cash rearm state is incomplete")
        if not state.authorization_id.startswith("rearm_") or len(state.authorization_id) != 70:
            raise ValueError("strategic cash rearm authorization identity is invalid")
        if state.consumed_grant_id:
            raise ValueError("authorized strategic cash rearm cannot already be consumed")
    elif state.authorized:
        raise ValueError("only AUTHORIZED strategic cash rearm can deploy capital")
    if status is StrategicCashRearmStatus.CONSUMED:
        if not state.authorization_id or not state.authorized_session or not state.consumed_grant_id:
            raise ValueError("consumed strategic cash rearm requires authorization and grant identity")
    elif state.consumed_grant_id:
        raise ValueError("unconsumed strategic cash rearm cannot retain a grant identity")


def validate_strategic_cash_rearm_account_binding(
    state: StrategicCashRearmState,
    *,
    account_identity: str,
) -> None:
    """Validate authorization identity against its durable account owner."""

    validate_strategic_cash_rearm_state(state)
    if state.status not in {
        StrategicCashRearmStatus.AUTHORIZED.value,
        StrategicCashRearmStatus.CONSUMED.value,
    }:
        return
    expected = derive_strategic_cash_rearm_authorization_id(
        account_identity=account_identity,
        candidate_symbol=state.candidate_symbol,
        qualification_signature=state.qualification_signature,
        qualification_route=state.qualification_route,
        qualification_quorum=state.qualification_quorum,
        qualification_evidence_sha256=state.qualification_evidence_sha256,
        capital_budget_level=state.capital_budget_level,
        tradable_universe_identity=state.tradable_universe_identity,
        qualification_reference_universe_identity=(
            state.qualification_reference_universe_identity
        ),
        risk_reference_universe_identity=state.risk_reference_universe_identity,
        point_in_time_industry_identity=state.point_in_time_industry_identity,
        required_healthy_sessions=state.required_healthy_sessions,
    )
    if state.authorization_id != expected:
        raise ValueError(
            "strategic cash rearm authorization identity differs from account evidence"
        )


def strategic_cash_rearm_from_payload(
    value: Mapping[str, Any] | None,
) -> StrategicCashRearmState:
    """Decode one typed durable rearm state from account JSON."""

    raw = dict(value or {})
    raw["predicate_results"] = [
        StrategicCashRearmPredicate(**dict(item))
        for item in raw.get("predicate_results", [])
    ]
    state = StrategicCashRearmState(**raw)
    validate_strategic_cash_rearm_state(state)
    return state


__all__ = (
    "StrategicCashRearmPredicate",
    "StrategicCashRearmRejectionReason",
    "StrategicCashRearmState",
    "StrategicCashRearmStatus",
    "StrategicCashRearmStreakTransition",
    "derive_strategic_cash_rearm_authorization_id",
    "strategic_cash_rearm_from_payload",
    "validate_strategic_cash_rearm_account_binding",
    "validate_strategic_cash_rearm_state",
)
