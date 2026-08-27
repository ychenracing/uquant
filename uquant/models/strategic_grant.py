"""Durable strategic qualification and capital-grant identities."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Any


class StrategicGrantStatus(str, Enum):
    """Lifecycle of one strategic capital-grant intent."""

    QUALIFIED = "QUALIFIED"
    PENDING_EXECUTION = "PENDING_EXECUTION"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS = 20


TERMINAL_STRATEGIC_GRANT_STATUSES = frozenset(
    {StrategicGrantStatus.EXPIRED.value, StrategicGrantStatus.CANCELLED.value}
)


@dataclass(slots=True)
class StrategicQualificationObservation:
    """Read-only qualification evidence retained independently of deployment."""

    candidate_symbol: str = ""
    qualification_signature: str = ""
    qualification_route: str = ""
    qualification_evidence_sha256: str = ""
    qualification_ready: bool = False
    deployment_blocked: bool = False
    deployment_block_reason: str = ""
    qualification_streak: int = 0
    qualification_last_observed_session: str = ""
    candidate_invalidation_reason: str = ""


@dataclass(slots=True)
class StrategicGrantIntent:
    """One persistent and replay-stable strategic capital deployment intent."""

    grant_id: str
    candidate_symbol: str
    qualification_signature: str
    qualification_route: str
    qualification_evidence_sha256: str
    created_session: str
    last_eligible_session: str
    first_submission_session: str = ""
    last_submission_session: str = ""
    healthy_retry_sessions: int = 0
    submitted_order_ids: list[str] = field(default_factory=list)
    acknowledged_order_ids: list[str] = field(default_factory=list)
    filled_shares: int = 0
    target_weight: float = 0.0
    status: str = StrategicGrantStatus.QUALIFIED.value
    expiry_reason: str = ""
    previous_grant_id: str = ""
    account_identity: str = ""
    production_source_identity: str = ""

    @property
    def terminal(self) -> bool:
        """Return whether this grant can no longer create capital targets."""

        return self.status in TERMINAL_STRATEGIC_GRANT_STATUSES


def _require_session(value: str, *, field_name: str, allow_empty: bool = False) -> None:
    if allow_empty and not value:
        return
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _require_sha256(value: str, *, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be SHA-256")


def derive_strategic_grant_id(
    *,
    account_identity: str,
    candidate_symbol: str,
    qualification_signature: str,
    qualification_route: str,
    qualification_evidence_sha256: str,
    created_session: str,
    previous_grant_id: str,
    production_source_identity: str,
) -> str:
    """Derive an immutable grant identity from qualification provenance."""

    for name, value in (
        ("account_identity", account_identity),
        ("candidate_symbol", candidate_symbol),
        ("qualification_signature", qualification_signature),
        ("qualification_route", qualification_route),
        ("production_source_identity", production_source_identity),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"strategic grant {name} must be non-empty text")
    _require_sha256(
        qualification_evidence_sha256,
        field_name="strategic grant qualification_evidence_sha256",
    )
    _require_session(created_session, field_name="strategic grant created_session")
    if not isinstance(previous_grant_id, str):
        raise ValueError("strategic grant previous_grant_id must be text")
    payload = {
        "account_identity": account_identity,
        "candidate_symbol": candidate_symbol,
        "created_session": created_session,
        "previous_grant_id": previous_grant_id,
        "production_source_identity": production_source_identity,
        "qualification_evidence_sha256": qualification_evidence_sha256,
        "qualification_route": qualification_route,
        "qualification_signature": qualification_signature,
        "schema": "uquant.strategic-grant-intent",
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "grant_" + hashlib.sha256(encoded).hexdigest()


def validate_strategic_qualification(value: StrategicQualificationObservation) -> None:
    """Reject malformed or internally contradictory qualification evidence."""

    if not value.candidate_symbol:
        populated = (
            value.qualification_signature,
            value.qualification_route,
            value.qualification_evidence_sha256,
            value.qualification_streak,
            value.qualification_last_observed_session,
            value.qualification_ready,
        )
        if any(populated):
            raise ValueError("strategic qualification candidate is missing")
    else:
        if not value.qualification_signature or not value.qualification_route:
            raise ValueError("strategic qualification identity is incomplete")
        _require_sha256(
            value.qualification_evidence_sha256,
            field_name="strategic qualification evidence",
        )
        _require_session(
            value.qualification_last_observed_session,
            field_name="strategic qualification observed session",
        )
    if isinstance(value.qualification_streak, bool) or value.qualification_streak < 0:
        raise ValueError("strategic qualification streak must be non-negative")
    if value.deployment_blocked and not value.deployment_block_reason:
        raise ValueError("blocked strategic deployment requires a reason")
    if not value.deployment_blocked and value.deployment_block_reason:
        raise ValueError("unblocked strategic deployment cannot retain a block reason")


def validate_strategic_grant(value: StrategicGrantIntent) -> None:
    """Validate a durable grant and its immutable deterministic identity."""

    StrategicGrantStatus(value.status)
    _require_session(value.created_session, field_name="strategic grant created_session")
    _require_session(value.last_eligible_session, field_name="strategic grant last eligible session")
    _require_session(
        value.first_submission_session,
        field_name="strategic grant first submission session",
        allow_empty=True,
    )
    _require_session(
        value.last_submission_session,
        field_name="strategic grant last submission session",
        allow_empty=True,
    )
    expected = derive_strategic_grant_id(
        account_identity=value.account_identity,
        candidate_symbol=value.candidate_symbol,
        qualification_signature=value.qualification_signature,
        qualification_route=value.qualification_route,
        qualification_evidence_sha256=value.qualification_evidence_sha256,
        created_session=value.created_session,
        previous_grant_id=value.previous_grant_id,
        production_source_identity=value.production_source_identity,
    )
    if value.grant_id != expected:
        raise ValueError("strategic grant identity does not match immutable qualification evidence")
    if isinstance(value.healthy_retry_sessions, bool) or not 0 <= value.healthy_retry_sessions <= 20:
        raise ValueError("strategic grant healthy retry sessions must be between zero and twenty")
    if isinstance(value.filled_shares, bool) or value.filled_shares < 0:
        raise ValueError("strategic grant filled shares must be non-negative")
    if (
        isinstance(value.target_weight, bool)
        or not isinstance(value.target_weight, (int, float))
        or not math.isfinite(float(value.target_weight))
        or not 0.0 <= float(value.target_weight) <= 1.0
    ):
        raise ValueError("strategic grant target weight must be between zero and one")
    if len(value.submitted_order_ids) != len(set(value.submitted_order_ids)):
        raise ValueError("strategic grant submitted order ids must be unique")
    if len(value.acknowledged_order_ids) != len(set(value.acknowledged_order_ids)):
        raise ValueError("strategic grant acknowledged order ids must be unique")
    if not set(value.acknowledged_order_ids).issubset(value.submitted_order_ids):
        raise ValueError("strategic grant acknowledged orders must have been submitted")
    if value.terminal and not value.expiry_reason:
        raise ValueError("terminal strategic grant requires an expiry reason")
    if not value.terminal and value.expiry_reason:
        raise ValueError("active strategic grant cannot have an expiry reason")


def strategic_qualification_from_payload(
    value: Mapping[str, Any] | None,
) -> StrategicQualificationObservation:
    """Decode one qualification observation from account JSON."""

    observation = StrategicQualificationObservation(**dict(value or {}))
    validate_strategic_qualification(observation)
    return observation


def strategic_grant_from_payload(value: Mapping[str, Any] | None) -> StrategicGrantIntent | None:
    """Decode one optional grant from account JSON."""

    if value is None:
        return None
    grant = StrategicGrantIntent(**dict(value))
    validate_strategic_grant(grant)
    return grant


def record_strategic_grant_submissions(
    grant: StrategicGrantIntent | None,
    *,
    order_ids: list[tuple[str, str]],
) -> None:
    """Record physical orders submitted for the same economic grant."""

    if grant is None or grant.terminal:
        return
    for order_id, session in order_ids:
        if not order_id or order_id in grant.submitted_order_ids:
            continue
        grant.submitted_order_ids.append(order_id)
        grant.first_submission_session = grant.first_submission_session or session
        grant.last_submission_session = session
    if order_ids and grant.status == StrategicGrantStatus.QUALIFIED.value:
        grant.status = StrategicGrantStatus.PENDING_EXECUTION.value


def acknowledge_strategic_grant_order(
    grant: StrategicGrantIntent | None,
    *,
    grant_id: str,
    order_id: str,
) -> None:
    """Record broker or replay acknowledgement without duplicating quantity."""

    if grant is None or grant.terminal or grant.grant_id != grant_id:
        return
    if order_id not in grant.submitted_order_ids:
        raise RuntimeError("strategic grant acknowledged an order that was not submitted")
    if order_id not in grant.acknowledged_order_ids:
        grant.acknowledged_order_ids.append(order_id)


def record_strategic_grant_fill(
    grant: StrategicGrantIntent | None,
    *,
    grant_id: str,
    shares: int,
    completed: bool,
) -> None:
    """Advance one grant using a newly accepted BUY fill."""

    if grant is None or grant.terminal or grant.grant_id != grant_id:
        return
    grant.filled_shares += shares
    grant.status = (
        StrategicGrantStatus.ACTIVE.value
        if completed
        else StrategicGrantStatus.PARTIALLY_FILLED.value
    )


__all__ = (
    "TERMINAL_STRATEGIC_GRANT_STATUSES",
    "StrategicGrantIntent",
    "StrategicGrantStatus",
    "StrategicQualificationObservation",
    "acknowledge_strategic_grant_order",
    "derive_strategic_grant_id",
    "record_strategic_grant_fill",
    "record_strategic_grant_submissions",
    "strategic_grant_from_payload",
    "strategic_qualification_from_payload",
    "validate_strategic_grant",
    "validate_strategic_qualification",
)
