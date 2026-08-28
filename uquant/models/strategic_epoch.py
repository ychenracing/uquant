"""Durable strategic ownership epochs backed by realized execution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date as date_type
from enum import Enum
from typing import Any


class StrategicEpochStatus(str, Enum):
    """Realized lifecycle of one immutable strategic ownership identity."""

    QUALIFIED = "QUALIFIED"
    PROBE = "PROBE"
    CORE = "CORE"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


TERMINAL_STRATEGIC_EPOCH_STATUSES = frozenset(
    {StrategicEpochStatus.CLOSED.value, StrategicEpochStatus.EXPIRED.value}
)


def _require_text(value: str, *, field_name: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"strategic epoch {field_name} must be non-empty text")


def _require_session(value: str, *, field_name: str, allow_empty: bool = False) -> None:
    if allow_empty and not value:
        return
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"strategic epoch {field_name} must be an ISO date") from exc


def _require_sha256(value: str, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"strategic epoch {field_name} must be SHA-256")


def derive_strategic_epoch_id(
    *,
    account_identity: str,
    owner_symbol: str,
    qualification_signature: str,
    qualification_route: str,
    grant_id: str,
    opened_session: str,
    previous_epoch_id: str,
    source_identity: str,
    config_identity: str,
    evidence_sha256: str,
) -> str:
    """Derive an immutable epoch identity from its ownership provenance."""

    for field_name, value in (
        ("account_identity", account_identity),
        ("owner_symbol", owner_symbol),
        ("qualification_signature", qualification_signature),
        ("qualification_route", qualification_route),
        ("grant_id", grant_id),
        ("source_identity", source_identity),
        ("config_identity", config_identity),
    ):
        _require_text(value, field_name=field_name)
    _require_text(previous_epoch_id, field_name="previous_epoch_id", allow_empty=True)
    _require_session(opened_session, field_name="opened_session")
    _require_sha256(evidence_sha256, field_name="evidence_sha256")
    payload = {
        "account_identity": account_identity,
        "config_identity": config_identity,
        "evidence_sha256": evidence_sha256,
        "grant_id": grant_id,
        "opened_session": opened_session,
        "owner_symbol": owner_symbol,
        "previous_epoch_id": previous_epoch_id,
        "qualification_route": qualification_route,
        "qualification_signature": qualification_signature,
        "schema": "uquant.strategic-epoch",
        "source_identity": source_identity,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "epoch_" + hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class StrategicEpoch:
    """One immutable strategic owner and its realized capital lifecycle."""

    epoch_id: str
    owner_symbol: str
    qualification_signature: str
    qualification_route: str
    qualification_quorum: str
    grant_id: str
    opened_session: str
    first_fill_session: str = ""
    active_session: str = ""
    closed_session: str = ""
    close_reason: str = ""
    previous_epoch_id: str = ""
    source_identity: str = ""
    config_identity: str = ""
    evidence_sha256: str = ""
    realized_status: str = StrategicEpochStatus.QUALIFIED.value
    target_weight: float = 0.0
    full_weight: float = 0.0
    account_identity: str = ""

    @property
    def terminal(self) -> bool:
        """Return whether this ownership identity can no longer deploy capital."""

        return self.realized_status in TERMINAL_STRATEGIC_EPOCH_STATUSES

    @property
    def active(self) -> bool:
        """Return whether this epoch currently owns strategic capital authority."""

        return self.realized_status == StrategicEpochStatus.ACTIVE.value

    def validate(self) -> None:
        """Validate immutable identity, causal dates, and realized state."""

        status = StrategicEpochStatus(self.realized_status)
        expected = derive_strategic_epoch_id(
            account_identity=self.account_identity,
            owner_symbol=self.owner_symbol,
            qualification_signature=self.qualification_signature,
            qualification_route=self.qualification_route,
            grant_id=self.grant_id,
            opened_session=self.opened_session,
            previous_epoch_id=self.previous_epoch_id,
            source_identity=self.source_identity,
            config_identity=self.config_identity,
            evidence_sha256=self.evidence_sha256,
        )
        if self.epoch_id != expected:
            raise ValueError("strategic epoch identity does not match immutable ownership evidence")
        _require_text(self.qualification_quorum, field_name="qualification_quorum")
        for field_name, value in (
            ("first_fill_session", self.first_fill_session),
            ("active_session", self.active_session),
            ("closed_session", self.closed_session),
        ):
            _require_session(value, field_name=field_name, allow_empty=True)
        for field_name, value in (
            ("target_weight", self.target_weight),
            ("full_weight", self.full_weight),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"strategic epoch {field_name} must be between zero and one")
        if self.target_weight > self.full_weight:
            raise ValueError("strategic epoch target weight exceeds full weight")
        if self.first_fill_session and self.first_fill_session < self.opened_session:
            raise ValueError("strategic epoch fill precedes opening")
        if self.active_session and (
            not self.first_fill_session or self.active_session < self.first_fill_session
        ):
            raise ValueError("strategic epoch activation precedes its first fill")
        if self.closed_session and self.closed_session < self.opened_session:
            raise ValueError("strategic epoch close precedes opening")
        if status in {StrategicEpochStatus.CORE, StrategicEpochStatus.ACTIVE} and not self.first_fill_session:
            raise ValueError("realized strategic epoch requires a first fill")
        if status is StrategicEpochStatus.ACTIVE and not self.active_session:
            raise ValueError("active strategic epoch requires an active session")
        if status in {StrategicEpochStatus.CLOSED, StrategicEpochStatus.EXPIRED}:
            if not self.closed_session or not self.close_reason:
                raise ValueError("terminal strategic epoch requires close evidence")
        elif self.closed_session or self.close_reason:
            raise ValueError("nonterminal strategic epoch cannot retain close evidence")


def strategic_epoch_from_payload(value: dict[str, object]) -> StrategicEpoch:
    """Decode and validate one durable epoch payload."""

    epoch = StrategicEpoch(**value)  # type: ignore[arg-type]
    epoch.validate()
    return epoch


def activate_strategic_epoch(
    epoch: StrategicEpoch,
    *,
    grant_id: str,
    symbol: str,
    fill_session: str,
    filled_shares: int,
) -> None:
    """Activate one epoch only from a positive matching accepted BUY fill."""

    if (
        filled_shares <= 0
        or grant_id != epoch.grant_id
        or symbol != epoch.owner_symbol
    ):
        raise ValueError("strategic epoch activation requires a positive matching fill")
    _require_session(fill_session, field_name="fill_session")
    if epoch.terminal:
        raise ValueError("terminal strategic epoch cannot be activated")
    epoch.first_fill_session = epoch.first_fill_session or fill_session
    epoch.active_session = epoch.active_session or fill_session
    epoch.realized_status = StrategicEpochStatus.ACTIVE.value
    epoch.validate()


def close_strategic_epoch(
    epoch: StrategicEpoch,
    *,
    closed_session: str,
    close_reason: str,
    expired: bool = False,
) -> None:
    """Close one epoch without mutating its immutable owner identity."""

    if epoch.terminal:
        return
    _require_session(closed_session, field_name="closed_session")
    _require_text(close_reason, field_name="close_reason")
    epoch.closed_session = closed_session
    epoch.close_reason = close_reason
    epoch.realized_status = (
        StrategicEpochStatus.EXPIRED.value if expired else StrategicEpochStatus.CLOSED.value
    )
    epoch.validate()


def record_account_strategic_epoch_fill(
    account: Any,
    *,
    epoch_id: str,
    grant_id: str,
    symbol: str,
    fill_session: str,
    filled_shares: int,
) -> None:
    """Advance one account epoch from a newly accepted matching BUY fill."""

    if not epoch_id:
        return
    matches = [epoch for epoch in account.strategic_epochs if epoch.epoch_id == epoch_id]
    if len(matches) != 1:
        raise RuntimeError("strategic fill references an unknown or duplicate epoch")
    epoch = matches[0]
    if epoch.grant_id != grant_id or epoch.owner_symbol != symbol:
        raise RuntimeError("strategic fill identity differs from its epoch owner")
    if filled_shares <= 0:
        raise RuntimeError("strategic epoch fill must have positive shares")
    if account.active_strategic_epoch_id not in {"", epoch_id}:
        raise RuntimeError("strategic fill would activate a second capital owner")
    was_active = epoch.active
    if epoch.realized_status in {
        StrategicEpochStatus.QUALIFIED.value,
        StrategicEpochStatus.PROBE.value,
    }:
        epoch.first_fill_session = epoch.first_fill_session or fill_session
        if epoch.qualification_quorum == "FULL_COHORT":
            activate_strategic_epoch(
                epoch,
                grant_id=grant_id,
                symbol=symbol,
                fill_session=fill_session,
                filled_shares=filled_shares,
            )
        else:
            epoch.realized_status = StrategicEpochStatus.CORE.value
            epoch.validate()
    elif (
        epoch.realized_status == StrategicEpochStatus.CORE.value
        and fill_session > epoch.first_fill_session
    ):
        activate_strategic_epoch(
            epoch,
            grant_id=grant_id,
            symbol=symbol,
            fill_session=fill_session,
            filled_shares=filled_shares,
        )
    elif epoch.terminal:
        raise RuntimeError("terminal strategic epoch accepted a new BUY fill")
    if epoch.active:
        account.active_strategic_epoch_id = epoch_id
        if not was_active:
            account.strategic_epoch += 1
        account.candidate_tenure["strategic_cohort_active"] = 1
        grant = account.strategic_grant
        if grant is not None and grant.grant_id == grant_id:
            grant.status = "COMPLETED"
            grant.expiry_reason = ""
    elif epoch.realized_status == StrategicEpochStatus.CORE.value:
        grant = account.strategic_grant
        if grant is not None and grant.grant_id == grant_id:
            grant.status = "PARTIALLY_FILLED"


__all__ = (
    "TERMINAL_STRATEGIC_EPOCH_STATUSES",
    "StrategicEpoch",
    "StrategicEpochStatus",
    "activate_strategic_epoch",
    "close_strategic_epoch",
    "derive_strategic_epoch_id",
    "strategic_epoch_from_payload",
    "record_account_strategic_epoch_fill",
)
