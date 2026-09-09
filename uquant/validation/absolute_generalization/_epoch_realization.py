"""Read-only native epoch status and physical BUY realization checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ._metric_primitives import metric_iso_session, metric_positive_number, metric_text


def epoch_owner_buy_fills(
    *,
    epoch_id: str,
    epoch: Mapping[str, object],
    fills: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    grant_id = metric_text(epoch.get("grant_id"), label="epoch grant")
    owner = metric_text(epoch.get("owner_symbol"), label="epoch owner")
    matching = tuple(
        fill for fill in fills
        if fill.get("epoch_id") == epoch_id and fill.get("symbol") == owner
        and fill.get("side") == "BUY" and metric_positive_number(fill.get("shares")) > 0.0
    )
    if any(fill.get("grant_id") != grant_id for fill in matching):
        raise ValueError("absolute generalization epoch fill grant identity differs")
    return matching


def _validate_terminal_epoch(*, status: str, closed: str, reason: str) -> None:
    if status in {"CLOSED", "EXPIRED"}:
        if not closed or not reason:
            raise ValueError("absolute generalization terminal epoch lacks close evidence")
    elif closed or reason:
        raise ValueError("absolute generalization nonterminal epoch has close evidence")


def _validate_activation(
    *,
    status: str,
    first: str,
    active: str,
    closed: str,
    matching: Sequence[Mapping[str, object]],
) -> None:
    if (status == "ACTIVE" and not active) or (status == "CORE" and active):
        raise ValueError("absolute generalization epoch activation status differs")
    if active and (
        active < first
        or not any(item.get("fill_date") == active for item in matching)
    ):
        raise ValueError("absolute generalization epoch activation lacks a matching positive BUY")
    if closed and closed < (active or first):
        raise ValueError("absolute generalization epoch closes before its realized fill")


def epoch_realization_sessions(
    *, epoch: Mapping[str, object], matching: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    """Return separate first-fill and activation dates after checking native status."""
    status = metric_text(epoch.get("realized_status"), label="epoch realized status")
    if status not in {"QUALIFIED", "PROBE", "CORE", "ACTIVE", "CLOSED", "EXPIRED"}:
        raise ValueError("absolute generalization epoch realized status differs")
    first = metric_iso_session(epoch.get("first_fill_session", ""), label="epoch first fill", empty=True)
    active = metric_iso_session(epoch.get("active_session", ""), label="epoch active session", empty=True)
    closed = metric_iso_session(epoch.get("closed_session", ""), label="epoch closed", empty=True)
    reason = metric_text(epoch.get("close_reason", ""), label="epoch close reason", empty=True)
    _validate_terminal_epoch(status=status, closed=closed, reason=reason)
    if not first:
        if matching or active or status in {"CORE", "ACTIVE"}:
            raise ValueError("absolute generalization realized epoch has no first fill")
        return "", ""
    if status in {"QUALIFIED", "PROBE"}:
        raise ValueError("absolute generalization filled epoch has non-realized status")
    if not matching:
        raise ValueError("absolute generalization strategic epoch has no matching real fill")
    fill_session = min(metric_iso_session(item.get("fill_date"), label="fill session") for item in matching)
    if fill_session != first:
        raise ValueError("absolute generalization epoch first fill differs from fill ledger")
    _validate_activation(status=status, first=first, active=active, closed=closed, matching=matching)
    return fill_session, active
