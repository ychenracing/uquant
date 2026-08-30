"""Shared production-semantic reachability facts for focused validation tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from test_absolute_generalization_reachability import (
    _cash_account,
    _filled_chain,
    _observed_trace,
    _probe_chain,
    _reachability_state,
    _rearm_evidence,
)

from uquant.models.decision import Target
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import StrategicGrantIntent
from uquant.models.strategic_rearm import (
    FlatBookCapitalRepairState,
    StrategicCashRearmState,
)
from uquant.models.trading import AccountOrder, Fill
from uquant.types import AccountState

OutletChain = tuple[
    Target,
    StrategicGrantIntent,
    StrategicEpoch,
    AccountOrder,
    Fill,
]


def failed_successor_chain(*, retry_sessions: int = 20) -> tuple[
    StrategicGrantIntent,
    StrategicEpoch,
    Target,
    StrategicGrantIntent,
    StrategicEpoch,
    AccountOrder,
    Fill,
]:
    """Build two causally linked grant/epoch identities and one realized successor."""

    _first_target, first_grant, first_epoch = _probe_chain()
    first_grant.status = "EXPIRED"
    first_grant.expiry_reason = "unfilled_probe_timeout"
    first_epoch.realized_status = "EXPIRED"
    first_epoch.closed_session = "2026-01-07"
    first_epoch.close_reason = "unfilled_probe_timeout"
    created_session = date(2026, 1, 8) + timedelta(days=retry_sessions)
    fill_session = created_session + timedelta(days=1)
    rearm = _rearm_evidence("sz300502", created_session.isoformat())
    target, second_grant, second_epoch, order, fill = _filled_chain(
        candidate="sz300502",
        previous_grant_id=first_grant.grant_id,
        previous_epoch_id=first_epoch.epoch_id,
        authorization_id=str(rearm["authorization_id"]),
        created_session=created_session.isoformat(),
        fill_session=fill_session.isoformat(),
    )
    return (
        first_grant,
        first_epoch,
        target,
        second_grant,
        second_epoch,
        order,
        fill,
    )


def _account_with_successor_chain(chain: OutletChain, *, filled: bool) -> AccountState:
    _target, grant, epoch, order, fill = deepcopy(chain)
    account = _cash_account(budget_level=1)
    if not filled:
        grant.status = "PENDING_EXECUTION"
        grant.filled_shares = 0
        epoch.realized_status = "PROBE"
        epoch.first_fill_session = ""
        epoch.active_session = ""
        order.status = "SUBMITTED"
        order.filled_shares = 0
        order.remaining_shares = order.requested_shares
        order.last_update_date = order.submitted_date
        order.last_event = "SUBMITTED"
    else:
        account.active_strategic_epoch_id = epoch.epoch_id
        account.fills = [fill]
    _first_target, _first_grant, predecessor = _probe_chain()
    predecessor.realized_status = "EXPIRED"
    predecessor.closed_session = "2026-01-07"
    predecessor.close_reason = "unfilled_probe_timeout"
    account.strategic_grant = grant
    account.strategic_epochs = [predecessor, epoch]
    account.order_ledger = [order]
    account.next_order_sequence = 2
    rearm = _rearm_evidence(grant.candidate_symbol, grant.created_session)
    repair_id = str(rearm["repair_episode_id"])
    risk_identity = str(rearm["risk_reference_universe_identity"])
    account.flat_book_capital_repair = FlatBookCapitalRepairState(
        repair_episode_id=repair_id,
        account_identity=account.account_identity,
        capital_budget_level=1,
        repair_target_level=0,
        first_observed_session="2026-01-01",
        last_observed_session=grant.created_session,
        last_counted_session=grant.created_session,
        healthy_session_count=20,
        required_healthy_sessions=20,
        status="CONSUMED",
        risk_reference_universe_identity=risk_identity,
        config_identity=str(rearm["config_identity"]),
        last_ready_session=grant.created_session,
    )
    account.strategic_cash_rearm = StrategicCashRearmState(
        observed_session=grant.created_session,
        repair_episode_id=repair_id,
        candidate_symbol=grant.candidate_symbol,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        qualification_quorum=grant.qualification_quorum,
        qualification_evidence_sha256=grant.qualification_evidence_sha256,
        capital_budget_level=1,
        tradable_universe_identity=str(rearm["tradable_universe_identity"]),
        qualification_reference_universe_identity=str(
            rearm["qualification_reference_universe_identity"]
        ),
        risk_reference_universe_identity=risk_identity,
        point_in_time_industry_identity=str(rearm["point_in_time_industry_identity"]),
        status="CONSUMED",
        authorization_id=grant.authorization_id,
        authorized_session=grant.created_session,
        consumed_grant_id=grant.grant_id,
        qualification_ready=True,
        route_consistent_absolute_quality=True,
        authorized=False,
    )
    return account


def failed_recovery_trace(retry_sessions: int, chain: OutletChain) -> list[dict[str, object]]:
    """Embed exact successor runtime evidence into consecutive validated states."""

    trace = _observed_trace(retry_sessions + 2, start=date(2026, 1, 8))
    for index, filled in ((-3, False), (-2, True), (-1, True)):
        state = trace[index]["state"]
        assert isinstance(state, dict)
        state.update(
            _reachability_state(
                account=_account_with_successor_chain(chain, filled=filled),
                outlet_chain=chain if filled else None,
            )
        )
    return trace


__all__ = ("OutletChain", "failed_recovery_trace", "failed_successor_chain")
