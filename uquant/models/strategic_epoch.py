"""Durable strategic ownership epochs backed by realized execution."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_type
from enum import Enum
from typing import Any, cast

from .trading import late_strategic_fill_allowed


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


def _require_epoch_text(value: str, *, field_name: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"strategic epoch {field_name} must be non-empty text")


def _require_epoch_session(
    value: str, *, field_name: str, allow_empty: bool = False
) -> None:
    if allow_empty and not value:
        return
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"strategic epoch {field_name} must be an ISO date") from exc


def _require_epoch_sha256(value: str, *, field_name: str) -> None:
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
        _require_epoch_text(value, field_name=field_name)
    _require_epoch_text(previous_epoch_id, field_name="previous_epoch_id", allow_empty=True)
    _require_epoch_session(opened_session, field_name="opened_session")
    _require_epoch_sha256(evidence_sha256, field_name="evidence_sha256")
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

    def _validate(self) -> None:
        """Validate immutable identity, causal dates, and realized state."""

        status = StrategicEpochStatus(self.realized_status)
        _validate_epoch_identity(self)
        _validate_epoch_fields(self)
        _validate_epoch_causality(self)
        _validate_epoch_status(self, status=status)


def _validate_epoch_identity(epoch: StrategicEpoch) -> None:
    expected = derive_strategic_epoch_id(
        account_identity=epoch.account_identity,
        owner_symbol=epoch.owner_symbol,
        qualification_signature=epoch.qualification_signature,
        qualification_route=epoch.qualification_route,
        grant_id=epoch.grant_id,
        opened_session=epoch.opened_session,
        previous_epoch_id=epoch.previous_epoch_id,
        source_identity=epoch.source_identity,
        config_identity=epoch.config_identity,
        evidence_sha256=epoch.evidence_sha256,
    )
    if epoch.epoch_id != expected:
        raise ValueError(
            "strategic epoch identity does not match immutable ownership evidence"
        )


def _validate_epoch_fields(epoch: StrategicEpoch) -> None:
    _require_epoch_text(epoch.qualification_quorum, field_name="qualification_quorum")
    for field_name, session_value in (
        ("first_fill_session", epoch.first_fill_session),
        ("active_session", epoch.active_session),
        ("closed_session", epoch.closed_session),
    ):
        _require_epoch_session(session_value, field_name=field_name, allow_empty=True)
    for field_name, weight_value in (
        ("target_weight", epoch.target_weight),
        ("full_weight", epoch.full_weight),
    ):
        if (
            isinstance(weight_value, bool)
            or not isinstance(weight_value, (int, float))
            or not math.isfinite(float(weight_value))
            or not 0.0 <= float(weight_value) <= 1.0
        ):
            raise ValueError(
                f"strategic epoch {field_name} must be between zero and one"
            )
    if epoch.target_weight > epoch.full_weight:
        raise ValueError("strategic epoch target weight exceeds full weight")


def _validate_epoch_causality(epoch: StrategicEpoch) -> None:
    if epoch.first_fill_session and epoch.first_fill_session < epoch.opened_session:
        raise ValueError("strategic epoch fill precedes opening")
    if epoch.active_session and (
        not epoch.first_fill_session
        or epoch.active_session < epoch.first_fill_session
    ):
        raise ValueError("strategic epoch activation precedes its first fill")
    if epoch.closed_session and epoch.closed_session < epoch.opened_session:
        raise ValueError("strategic epoch close precedes opening")


def _validate_epoch_status(
    epoch: StrategicEpoch, *, status: StrategicEpochStatus
) -> None:
    if status in {
        StrategicEpochStatus.CORE,
        StrategicEpochStatus.ACTIVE,
    } and not epoch.first_fill_session:
        raise ValueError("realized strategic epoch requires a first fill")
    if status is StrategicEpochStatus.ACTIVE and not epoch.active_session:
        raise ValueError("active strategic epoch requires an active session")
    if status in {StrategicEpochStatus.CLOSED, StrategicEpochStatus.EXPIRED}:
        if not epoch.closed_session or not epoch.close_reason:
            raise ValueError("terminal strategic epoch requires close evidence")
    elif epoch.closed_session or epoch.close_reason:
        raise ValueError("nonterminal strategic epoch cannot retain close evidence")


def validate_strategic_epoch(epoch: StrategicEpoch) -> None:
    """Validate one strategic epoch without expanding the stable type API."""

    epoch._validate()


def strategic_epoch_from_payload(value: Mapping[str, Any]) -> StrategicEpoch:
    """Decode and validate one durable epoch payload."""

    epoch = StrategicEpoch(**value)
    validate_strategic_epoch(epoch)
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
    _require_epoch_session(fill_session, field_name="fill_session")
    if epoch.terminal:
        raise ValueError("terminal strategic epoch cannot be activated")
    epoch.first_fill_session = epoch.first_fill_session or fill_session
    epoch.active_session = epoch.active_session or fill_session
    epoch.realized_status = StrategicEpochStatus.ACTIVE.value
    validate_strategic_epoch(epoch)


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
    _require_epoch_session(closed_session, field_name="closed_session")
    _require_epoch_text(close_reason, field_name="close_reason")
    epoch.closed_session = closed_session
    epoch.close_reason = close_reason
    epoch.realized_status = (
        StrategicEpochStatus.EXPIRED.value if expired else StrategicEpochStatus.CLOSED.value
    )
    validate_strategic_epoch(epoch)


def _account_epoch_close_blockers(account: Any, *, epoch_id: str) -> tuple[str, ...]:
    blockers: list[str] = []
    if any(
        position.shares > 0
        and (
            position.epoch_id == epoch_id
            or any(tranche.epoch_id == epoch_id for tranche in position.tranches)
        )
        for position in account.positions.values()
    ):
        blockers.append("position")
    if any(order.epoch_id == epoch_id for order in account.pending_orders):
        blockers.append("pending execution")
    terminal_order_statuses = {"FILLED", "CANCELLED", "REPLACED"}
    if any(
        order.epoch_id == epoch_id
        and (
            order.status not in terminal_order_statuses
            or late_strategic_fill_allowed(order)
        )
        for order in account.order_ledger
    ):
        blockers.append("unsettled execution")
    return tuple(blockers)


def close_account_strategic_epoch(
    account: Any,
    *,
    epoch_id: str,
    closed_session: str,
    close_reason: str,
    expired: bool = False,
) -> None:
    """Terminally settle one flat epoch and release only its owned state."""

    matches = [epoch for epoch in account.strategic_epochs if epoch.epoch_id == epoch_id]
    if len(matches) != 1:
        raise RuntimeError("strategic epoch close references an unknown or duplicate epoch")
    epoch = matches[0]
    blockers = _account_epoch_close_blockers(account, epoch_id=epoch_id)
    if blockers:
        raise RuntimeError(
            "strategic epoch close blocked by " + ", ".join(blockers)
        )
    close_strategic_epoch(
        epoch,
        closed_session=closed_session,
        close_reason=close_reason,
        expired=expired,
    )
    if account.active_strategic_epoch_id == epoch_id:
        account.active_strategic_epoch_id = ""
    for ownership_field, weights_field in (
        ("protected_weight_epoch_ids", "protected_weights"),
        ("strategic_restore_epoch_ids", "strategic_restore_weights"),
    ):
        ownership = getattr(account, ownership_field)
        weights = getattr(account, weights_field)
        for symbol in tuple(ownership):
            if ownership[symbol] != epoch_id:
                continue
            ownership.pop(symbol, None)
            weights.pop(symbol, None)
    if account.recovery_owner_epoch_id == epoch_id:
        account.recovery_owner_epoch_id = ""
        account.anchor_weights.clear()
        account.recovery_anchor_date = ""
        account.recovery_conviction_symbol = ""
        account.tactical_anchor_symbol = ""
    grant = account.strategic_grant
    if grant is not None and grant.grant_id == epoch.grant_id:
        if expired:
            if grant.status not in {"EXPIRED", "CANCELLED"}:
                grant.status = "EXPIRED"
                grant.expiry_reason = close_reason
        else:
            grant.status = "COMPLETED"
            grant.expiry_reason = ""


def settle_account_strategic_epoch(
    account: Any,
    *,
    epoch_id: str,
    closed_session: str,
    close_reason: str,
    expired: bool = False,
) -> bool:
    """Close an epoch when its capital and execution identities are settled."""

    if _account_epoch_close_blockers(account, epoch_id=epoch_id):
        return False
    close_account_strategic_epoch(
        account,
        epoch_id=epoch_id,
        closed_session=closed_session,
        close_reason=close_reason,
        expired=expired,
    )
    return True


def bind_account_strategic_ownership(account: Any) -> None:
    """Bind recovery and restoration state to its realized strategic epoch."""

    known = {
        epoch.epoch_id: epoch
        for epoch in account.strategic_epochs
        if not epoch.terminal
    }

    def owner_for_symbol(symbol: str) -> str:
        position = account.positions.get(symbol)
        if position is not None and position.shares > 0 and position.epoch_id in known:
            return str(position.epoch_id)
        if (
            account.active_strategic_epoch_id in known
            and symbol in account.strategic_cohort_symbols
        ):
            return str(account.active_strategic_epoch_id)
        return ""

    for ownership_field, weights_field in (
        ("protected_weight_epoch_ids", "protected_weights"),
        ("strategic_restore_epoch_ids", "strategic_restore_weights"),
    ):
        ownership = getattr(account, ownership_field)
        weights = getattr(account, weights_field)
        for symbol in tuple(ownership):
            if symbol not in weights or ownership[symbol] not in known:
                ownership.pop(symbol, None)
        for symbol in weights:
            epoch_id = owner_for_symbol(symbol)
            if epoch_id:
                ownership[symbol] = epoch_id

    anchor_owners = {
        owner_for_symbol(symbol)
        for symbol in account.anchor_weights
        if owner_for_symbol(symbol)
    }
    if account.anchor_weights and len(anchor_owners) == 1:
        account.recovery_owner_epoch_id = next(iter(anchor_owners))
    elif not account.anchor_weights or len(anchor_owners) != 1:
        account.recovery_owner_epoch_id = ""


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
    epoch = _account_epoch_for_fill(account, epoch_id=epoch_id)
    if not _epoch_owner_fill_matches(
        account,
        epoch=epoch,
        grant_id=grant_id,
        symbol=symbol,
    ):
        return
    if filled_shares <= 0:
        raise RuntimeError("strategic epoch fill must have positive shares")
    if account.active_strategic_epoch_id not in {"", epoch_id}:
        raise RuntimeError("strategic fill would activate a second capital owner")
    was_active = epoch.active
    _advance_strategic_epoch_fill(
        epoch,
        grant_id=grant_id,
        symbol=symbol,
        fill_session=fill_session,
        filled_shares=filled_shares,
    )
    _record_account_epoch_fill_status(
        account,
        epoch=epoch,
        epoch_id=epoch_id,
        grant_id=grant_id,
        was_active=was_active,
    )
    bind_account_strategic_ownership(account)


def _account_epoch_for_fill(account: Any, *, epoch_id: str) -> StrategicEpoch:
    matches = [
        epoch for epoch in account.strategic_epochs if epoch.epoch_id == epoch_id
    ]
    if len(matches) != 1:
        raise RuntimeError("strategic fill references an unknown or duplicate epoch")
    return cast(StrategicEpoch, matches[0])


def _epoch_owner_fill_matches(
    account: Any,
    *,
    epoch: StrategicEpoch,
    grant_id: str,
    symbol: str,
) -> bool:
    if epoch.grant_id != grant_id or epoch.owner_symbol != symbol:
        if (
            epoch.grant_id != grant_id
            or symbol not in set(account.strategic_cohort_symbols)
        ):
            raise RuntimeError("strategic fill identity differs from its epoch owner")
        if epoch.terminal:
            raise RuntimeError("terminal strategic epoch accepted a new BUY fill")
        bind_account_strategic_ownership(account)
        return False
    return True


def _advance_strategic_epoch_fill(
    epoch: StrategicEpoch,
    *,
    grant_id: str,
    symbol: str,
    fill_session: str,
    filled_shares: int,
) -> None:
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
            validate_strategic_epoch(epoch)
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


def _record_account_epoch_fill_status(
    account: Any,
    *,
    epoch: StrategicEpoch,
    epoch_id: str,
    grant_id: str,
    was_active: bool,
) -> None:
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
    "bind_account_strategic_ownership",
    "close_account_strategic_epoch",
    "close_strategic_epoch",
    "derive_strategic_epoch_id",
    "record_account_strategic_epoch_fill",
    "settle_account_strategic_epoch",
    "strategic_epoch_from_payload",
    "validate_strategic_epoch",
)
