"""Private mechanics for public failed-grant reachability validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from uquant.models.decision import Target
from uquant.models.strategic_epoch import StrategicEpoch, validate_strategic_epoch
from uquant.models.strategic_grant import StrategicGrantIntent, validate_strategic_grant
from uquant.models.trading import AccountOrder, Fill
from uquant.types import AccountState


@dataclass(frozen=True, slots=True)
class RecoveryChainFacts:
    """Strictly validated identities needed to finish retry reconciliation."""

    target: Target
    second_grant: StrategicGrantIntent
    second_epoch: StrategicEpoch
    orders: tuple[AccountOrder, ...]
    fills: tuple[Fill, ...]
    fill_session: str
    duplicate_submissions: int


@dataclass(frozen=True, slots=True)
class RecoveryResultValues:
    """Private scalar values for the public immutable analysis result."""

    passed: bool
    healthy_count: int
    first_candidate: str
    second_candidate: str
    first_grant_id: str
    second_grant_id: str
    first_epoch_id: str
    second_epoch_id: str
    duplicate_submissions: int


_RECOVERABLE_NO_FILL_REASONS = frozenset(
    {
        "broker_rejection",
        "candidate_not_tradable",
        "candidate_or_route_no_longer_qualified",
        "candidate_removed_from_allowed_universe",
        "limit_blocked",
        "order_timeout",
        "qualification_observation_window_elapsed",
        "unfilled_probe_timeout",
    }
)


def validate_recovery_chain(
    *,
    first_grant: StrategicGrantIntent,
    first_epoch: StrategicEpoch,
    target: Target,
    second_grant: StrategicGrantIntent,
    second_epoch: StrategicEpoch,
    orders: tuple[AccountOrder, ...],
    fills: tuple[Fill, ...],
) -> RecoveryChainFacts:
    """Validate immutable predecessor/successor execution identities."""

    _validate_predecessor(first_grant, first_epoch, second_grant, second_epoch)
    _validate_successor_identity(first_grant, second_grant, second_epoch)
    fill_session, duplicates = _reconcile_successor_execution(
        second_grant=second_grant,
        second_epoch=second_epoch,
        orders=orders,
        fills=fills,
    )
    return RecoveryChainFacts(
        target=target,
        second_grant=second_grant,
        second_epoch=second_epoch,
        orders=orders,
        fills=fills,
        fill_session=fill_session,
        duplicate_submissions=duplicates,
    )


def _validate_predecessor(
    first_grant: StrategicGrantIntent,
    first_epoch: StrategicEpoch,
    second_grant: StrategicGrantIntent,
    second_epoch: StrategicEpoch,
) -> None:
    if second_grant.previous_grant_id != first_grant.grant_id:
        raise ValueError("absolute reachability failed-grant previous grant differs")
    if second_epoch.previous_epoch_id != first_epoch.epoch_id:
        raise ValueError("absolute reachability failed-grant previous epoch differs")
    if (
        not first_grant.authorization_id
        or not second_grant.authorization_id
        or first_grant.authorization_id == second_grant.authorization_id
    ):
        raise ValueError("absolute reachability failed-grant authorization did not rotate")
    if first_grant.expiry_reason not in _RECOVERABLE_NO_FILL_REASONS:
        raise ValueError("absolute reachability failed-grant reason is not recoverable")
    for grant in (first_grant, second_grant):
        validate_strategic_grant(grant)
    for epoch in (first_epoch, second_epoch):
        validate_strategic_epoch(epoch)
    if (
        first_grant.status not in {"EXPIRED", "CANCELLED"}
        or first_grant.filled_shares != 0
        or first_epoch.grant_id != first_grant.grant_id
        or first_epoch.realized_status != "EXPIRED"
        or first_epoch.first_fill_session
        or first_epoch.active_session
        or first_epoch.close_reason != first_grant.expiry_reason
    ):
        raise ValueError("absolute reachability first failed grant is not terminally unfilled")


def _validate_successor_identity(
    first_grant: StrategicGrantIntent,
    second_grant: StrategicGrantIntent,
    second_epoch: StrategicEpoch,
) -> None:
    if (
        second_grant.grant_id == first_grant.grant_id
        or second_grant.candidate_symbol == first_grant.candidate_symbol
        or second_epoch.grant_id != second_grant.grant_id
        or second_epoch.owner_symbol != second_grant.candidate_symbol
    ):
        raise ValueError("absolute reachability failed-grant successor identity differs")


def _reconcile_successor_execution(
    *,
    second_grant: StrategicGrantIntent,
    second_epoch: StrategicEpoch,
    orders: tuple[AccountOrder, ...],
    fills: tuple[Fill, ...],
) -> tuple[str, int]:
    positive_fills = tuple(
        fill
        for fill in fills
        if fill.grant_id == second_grant.grant_id and fill.shares > 0
    )
    if not positive_fills:
        raise ValueError("absolute reachability failed-grant successor has no Fill")
    fill_session = min(fill.fill_date for fill in positive_fills)
    if second_epoch.first_fill_session != fill_session:
        raise ValueError("absolute reachability failed-grant successor Fill differs")
    successor_orders = tuple(
        order for order in orders if order.grant_id == second_grant.grant_id
    )
    if {order.order_id for order in successor_orders} != set(
        second_grant.submitted_order_ids
    ):
        raise ValueError("absolute reachability failed-grant orphan order authority")
    filled_order_ids = {fill.order_id for fill in positive_fills}
    duplicates = sum(
        order.submitted_date >= fill_session
        for order in successor_orders
        if order.order_id not in filled_order_ids
    )
    if duplicates:
        raise ValueError("absolute reachability failed-grant duplicate submission")
    return fill_session, duplicates


def finish_recovery_analysis(
    *,
    first_grant: StrategicGrantIntent,
    first_epoch: StrategicEpoch,
    chain: RecoveryChainFacts,
    sessions: Sequence[str],
    healthy: Sequence[bool],
    accounts: Sequence[AccountState],
    final_session: object,
    final_outlet: bool,
    maximum_healthy_sessions: int,
) -> RecoveryResultValues:
    """Reconcile the chain with validated consecutive production observations."""

    if not final_outlet or final_session != chain.fill_session:
        raise ValueError("absolute reachability failed-grant successor outlet differs")
    _validate_observed_predecessor(
        accounts=accounts,
        sessions=sessions,
        first_grant=first_grant,
        first_epoch=first_epoch,
    )
    retry_by_session: dict[str, list[bool]] = {}
    for session, is_healthy in zip(sessions, healthy, strict=True):
        if first_epoch.closed_session < session < chain.fill_session:
            retry_by_session.setdefault(session, []).append(is_healthy)
    healthy_count = sum(all(values) for values in retry_by_session.values())
    if any(_has_failed_grant_orphan(account, first_grant, first_epoch) for account in accounts):
        raise ValueError("absolute reachability failed-grant left orphan authority")
    return RecoveryResultValues(
        passed=healthy_count <= maximum_healthy_sessions,
        healthy_count=healthy_count,
        first_candidate=first_grant.candidate_symbol,
        second_candidate=chain.second_grant.candidate_symbol,
        first_grant_id=first_grant.grant_id,
        second_grant_id=chain.second_grant.grant_id,
        first_epoch_id=first_epoch.epoch_id,
        second_epoch_id=chain.second_epoch.epoch_id,
        duplicate_submissions=chain.duplicate_submissions,
    )


def _validate_observed_predecessor(
    *,
    accounts: Sequence[AccountState],
    sessions: Sequence[str],
    first_grant: StrategicGrantIntent,
    first_epoch: StrategicEpoch,
) -> None:
    observed_epochs = tuple(
        (session, epoch)
        for session, account in zip(sessions, accounts, strict=True)
        for epoch in account.strategic_epochs
        if epoch.epoch_id == first_epoch.epoch_id
    )
    if (
        not observed_epochs
        or observed_epochs[-1][1] != first_epoch
        or any(
            epoch != first_epoch
            and (
                session > first_epoch.closed_session
                or epoch.realized_status in {"CLOSED", "EXPIRED"}
            )
            for session, epoch in observed_epochs
        )
    ):
        raise ValueError("absolute reachability observed predecessor epoch differs")
    observed_epoch = observed_epochs[-1][1]
    grant_epoch_binding = (
        first_grant.grant_id,
        first_grant.epoch_id,
        first_grant.candidate_symbol,
        first_grant.qualification_signature,
        first_grant.qualification_route,
        first_grant.qualification_quorum,
        first_grant.qualification_evidence_sha256,
        first_grant.created_session,
        first_grant.target_weight,
        first_grant.production_source_identity,
        first_grant.account_identity,
        first_grant.expiry_reason,
    )
    observed_binding = (
        observed_epoch.grant_id,
        observed_epoch.epoch_id,
        observed_epoch.owner_symbol,
        observed_epoch.qualification_signature,
        observed_epoch.qualification_route,
        observed_epoch.qualification_quorum,
        observed_epoch.evidence_sha256,
        observed_epoch.opened_session,
        observed_epoch.target_weight,
        observed_epoch.source_identity,
        observed_epoch.account_identity,
        observed_epoch.close_reason,
    )
    observed_terminal_grants = tuple(
        (session, account.strategic_grant)
        for session, account in zip(sessions, accounts, strict=True)
        if account.strategic_grant is not None
        and account.strategic_grant.grant_id == first_grant.grant_id
    )
    if grant_epoch_binding != observed_binding or (
        any(
            grant != first_grant
            and (session > first_epoch.closed_session or grant.terminal)
            for session, grant in observed_terminal_grants
        )
    ):
        raise ValueError("absolute reachability observed predecessor grant differs")


def _has_failed_grant_orphan(
    account: AccountState,
    grant: StrategicGrantIntent,
    epoch: StrategicEpoch,
) -> bool:
    return bool(
        account.active_strategic_epoch_id == epoch.epoch_id
        or any(order.grant_id == grant.grant_id for order in account.pending_orders)
        or any(
            position.shares > 0 and position.grant_id == grant.grant_id
            for position in account.positions.values()
        )
    )


__all__: tuple[str, ...] = ()
