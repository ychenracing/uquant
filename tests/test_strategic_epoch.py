from __future__ import annotations

import copy

import pandas as pd
import pytest

from uquant.account.codec import account_from_dict
from uquant.account.validation_strategy import validate_strategy_risk_state
from uquant.application.target_attribution import attach_target_attribution
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    activate_strategic_epoch,
    close_strategic_epoch,
    derive_strategic_epoch_id,
    record_account_strategic_epoch_fill,
    validate_strategic_epoch,
)
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.portfolio_core import strategic_dominant_symbol, symbol_weight_cap
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Fill,
    Lifecycle,
    OriginSubsystem,
    PendingOrder,
    Target,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _grant(*, symbol: str = "sz300308", created: str = "2026-01-05") -> StrategicGrantIntent:
    account_identity = "account:primary"
    evidence = "a" * 64
    grant_id = derive_strategic_grant_id(
        account_identity=account_identity,
        candidate_symbol=symbol,
        qualification_signature=f"qualification:{symbol}",
        qualification_route="FULL_COHORT",
        qualification_evidence_sha256=evidence,
        created_session=created,
        previous_grant_id="",
        production_source_identity="code:production",
    )
    return StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=symbol,
        qualification_signature=f"qualification:{symbol}",
        qualification_route="FULL_COHORT",
        qualification_evidence_sha256=evidence,
        created_session=created,
        last_eligible_session=created,
        target_weight=0.20,
        status=StrategicGrantStatus.PENDING_EXECUTION.value,
        account_identity=account_identity,
        production_source_identity="code:production",
        qualification_quorum="FULL_COHORT",
    )


def _epoch(
    grant: StrategicGrantIntent,
    *,
    previous_epoch_id: str = "",
) -> StrategicEpoch:
    epoch_id = derive_strategic_epoch_id(
        account_identity=grant.account_identity,
        owner_symbol=grant.candidate_symbol,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        grant_id=grant.grant_id,
        opened_session=grant.created_session,
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity="config:frozen",
        evidence_sha256=grant.qualification_evidence_sha256,
    )
    return StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=grant.candidate_symbol,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        qualification_quorum=grant.qualification_route,
        grant_id=grant.grant_id,
        opened_session=grant.created_session,
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity="config:frozen",
        evidence_sha256=grant.qualification_evidence_sha256,
        target_weight=grant.target_weight,
        full_weight=0.95,
        realized_status=StrategicEpochStatus.PROBE.value,
        account_identity=grant.account_identity,
    )


def test_epoch_identity_is_distinct_from_grant_and_ignores_realized_progress() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    progressed = copy.deepcopy(epoch)
    progressed.first_fill_session = "2026-01-06"
    progressed.active_session = "2026-01-06"
    progressed.realized_status = StrategicEpochStatus.ACTIVE.value

    assert epoch.epoch_id.startswith("epoch_")
    assert epoch.epoch_id != grant.grant_id
    assert progressed.epoch_id == epoch.epoch_id


def test_epoch_owner_cannot_be_mutated_to_simulate_handoff() -> None:
    epoch = _epoch(_grant())
    epoch.owner_symbol = "sz300502"

    with pytest.raises(ValueError, match="epoch identity"):
        validate_strategic_epoch(epoch)


def test_epoch_activates_only_after_a_matching_real_fill() -> None:
    grant = _grant()
    epoch = _epoch(grant)

    with pytest.raises(ValueError, match="positive matching fill"):
        activate_strategic_epoch(
            epoch,
            grant_id=grant.grant_id,
            symbol=grant.candidate_symbol,
            fill_session="2026-01-06",
            filled_shares=0,
        )

    activate_strategic_epoch(
        epoch,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2026-01-06",
        filled_shares=100,
    )

    assert epoch.first_fill_session == "2026-01-06"
    assert epoch.active_session == "2026-01-06"


def test_full_cohort_probe_keeps_frozen_dominant_target_before_first_fill() -> None:
    """A pending ledger is not a realized epoch, but its formal target keeps the old cap."""

    grant = _grant()
    grant.target_weight = DEFAULT_CONFIG.strategic_dominant_max_weight
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    account = AccountState.empty(2_000_000.0)
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.strategic_cohort_symbols = [grant.candidate_symbol]
    account.strategic_cohort_targets = {
        grant.candidate_symbol: DEFAULT_CONFIG.strategic_dominant_max_weight,
    }
    account.candidate_tenure["strategic_cohort_active"] = 1
    account.candidate_tenure["strategic_dominant_epoch"] = 1

    assert account.strategic_epoch == 0
    assert account.active_strategic_epoch_id == ""
    assert epoch.realized_status == StrategicEpochStatus.PROBE.value
    assert strategic_dominant_symbol(account) == grant.candidate_symbol
    assert symbol_weight_cap(DEFAULT_CONFIG, account, grant.candidate_symbol) == pytest.approx(
        DEFAULT_CONFIG.strategic_dominant_max_weight
    )


def test_full_cohort_witness_fill_cannot_activate_the_owner_epoch() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.strategic_cohort_symbols = [grant.candidate_symbol, "sz300502"]

    record_account_strategic_epoch_fill(
        account,
        epoch_id=epoch.epoch_id,
        grant_id=grant.grant_id,
        symbol="sz300502",
        fill_session="2026-01-06",
        filled_shares=100,
    )

    assert epoch.realized_status == StrategicEpochStatus.PROBE.value
    assert account.active_strategic_epoch_id == ""
    assert account.strategic_epoch == 0

    record_account_strategic_epoch_fill(
        account,
        epoch_id=epoch.epoch_id,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2026-01-06",
        filled_shares=100,
    )

    assert epoch.realized_status == StrategicEpochStatus.ACTIVE.value
    assert account.active_strategic_epoch_id == epoch.epoch_id
    assert account.strategic_epoch == 1
    assert epoch.realized_status == StrategicEpochStatus.ACTIVE.value

    activate_strategic_epoch(
        epoch,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2026-01-07",
        filled_shares=100,
    )
    assert epoch.first_fill_session == "2026-01-06"
    assert epoch.active_session == "2026-01-06"


def test_account_round_trip_preserves_epoch_ledger_and_active_pointer() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    activate_strategic_epoch(
        epoch,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2026-01-06",
        filled_shares=100,
    )
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.data_hash = "data"
    account.code_hash = grant.production_source_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.active_strategic_epoch_id = epoch.epoch_id

    restored = account_from_dict(account.to_dict())

    assert restored == account
    assert restored.strategic_epochs[0].epoch_id == epoch.epoch_id


def test_account_rejects_two_active_epochs() -> None:
    first_grant = _grant()
    first = _epoch(first_grant)
    activate_strategic_epoch(
        first,
        grant_id=first_grant.grant_id,
        symbol=first_grant.candidate_symbol,
        fill_session="2026-01-06",
        filled_shares=100,
    )
    second_grant = _grant(symbol="sz300502", created="2026-02-02")
    second = _epoch(second_grant, previous_epoch_id=first.epoch_id)
    activate_strategic_epoch(
        second,
        grant_id=second_grant.grant_id,
        symbol=second_grant.candidate_symbol,
        fill_session="2026-02-03",
        filled_shares=100,
    )
    account = AccountState.empty(2_000_000.0)
    account.account_identity = first_grant.account_identity
    account.strategic_epochs = [first, second]
    account.active_strategic_epoch_id = second.epoch_id

    with pytest.raises(RuntimeError, match="at most one ACTIVE"):
        validate_strategy_risk_state(account)


def _filled_strategic_account() -> tuple[
    AccountState,
    tuple[Target, ...],
    tuple[PendingOrder, ...],
    list[Fill],
]:
    grant = _grant()
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.data_hash = "data"
    account.code_hash = grant.production_source_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    target = Target(
        symbol=grant.candidate_symbol,
        weight=grant.target_weight,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=0.9,
        confidence=0.95,
        reason="bounded strategic probe",
        reason_code="strategic_cohort",
        origin_subsystem=OriginSubsystem.STRATEGIC.value,
        mechanism=AttributionMechanism.STRATEGIC_COHORT.value,
        origin_lifecycle=Lifecycle.CORE.value,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    targets = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-05",
        targets=(target,),
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={grant.candidate_symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=[],
            current=planned,
            submitted_date="2026-01-05",
        )
    )
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.0],
            "high": [10.2, 10.2],
            "low": [9.8, 9.8],
            "close": [10.0, 10.0],
            "volume": [10_000_000.0, 10_000_000.0],
            "amount": [100_000_000.0, 100_000_000.0],
        },
        index=dates,
    )

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1],
        account=account,
        panel={grant.candidate_symbol: frame},
    )
    return account, targets, planned, fills


def test_epoch_identity_flows_through_target_order_fill_and_position() -> None:
    account, targets, planned, fills = _filled_strategic_account()

    assert len(fills) == 1
    grant = account.strategic_grant
    assert grant is not None
    epoch = account.strategic_epochs[0]
    position = account.positions[grant.candidate_symbol]
    assert targets[0].epoch_id == epoch.epoch_id
    assert planned[0].epoch_id == epoch.epoch_id
    assert account.order_ledger[0].epoch_id == epoch.epoch_id
    assert fills[0].epoch_id == epoch.epoch_id
    assert position.epoch_id == epoch.epoch_id
    assert position.tranches[0].epoch_id == epoch.epoch_id
    assert account.active_strategic_epoch_id == epoch.epoch_id
    assert account.strategic_epochs[0].realized_status == StrategicEpochStatus.ACTIVE.value
    assert account.strategic_epoch == 1
    assert account.strategic_grant is not None
    assert account.strategic_grant.status == StrategicGrantStatus.COMPLETED.value


@pytest.mark.parametrize("identity_owner", ("order", "position"))
def test_account_rejects_unknown_epoch_on_strategic_trading_identity(
    identity_owner: str,
) -> None:
    account, _, _, _ = _filled_strategic_account()
    payload = account.to_dict()
    unknown_epoch_id = "epoch_" + "f" * 64
    unknown_grant_id = "grant_" + "e" * 64
    if identity_owner == "order":
        payload["order_ledger"][0]["epoch_id"] = unknown_epoch_id
        payload["order_ledger"][0]["grant_id"] = unknown_grant_id
        payload["fills"][0]["epoch_id"] = unknown_epoch_id
        payload["fills"][0]["grant_id"] = unknown_grant_id
    else:
        symbol = account.strategic_epochs[0].owner_symbol
        payload["positions"][symbol]["epoch_id"] = unknown_epoch_id
        payload["positions"][symbol]["grant_id"] = unknown_grant_id
        payload["positions"][symbol]["tranches"][0]["epoch_id"] = unknown_epoch_id
        payload["positions"][symbol]["tranches"][0]["grant_id"] = unknown_grant_id

    with pytest.raises(RuntimeError, match="unknown strategic epoch"):
        account_from_dict(payload)


def test_account_rejects_grant_that_differs_from_trading_identity_epoch() -> None:
    account, _, _, _ = _filled_strategic_account()
    payload = account.to_dict()
    symbol = account.strategic_epochs[0].owner_symbol
    mismatched_grant_id = "grant_" + "e" * 64
    payload["positions"][symbol]["grant_id"] = mismatched_grant_id
    payload["positions"][symbol]["tranches"][0]["grant_id"] = mismatched_grant_id

    with pytest.raises(RuntimeError, match="grant identity differs from strategic epoch"):
        account_from_dict(payload)


def test_account_rejects_blank_grant_on_epoch_owner() -> None:
    account, _, _, _ = _filled_strategic_account()
    payload = account.to_dict()
    symbol = account.strategic_epochs[0].owner_symbol
    payload["positions"][symbol]["grant_id"] = ""
    payload["positions"][symbol]["tranches"][0]["grant_id"] = ""

    with pytest.raises(RuntimeError, match="grant identity differs from strategic epoch"):
        account_from_dict(payload)


def test_account_rejects_blank_aggregate_position_strategic_identity() -> None:
    account, _, _, _ = _filled_strategic_account()
    payload = account.to_dict()
    symbol = account.strategic_epochs[0].owner_symbol
    payload["positions"][symbol]["grant_id"] = ""
    payload["positions"][symbol]["epoch_id"] = ""

    with pytest.raises(RuntimeError, match="position strategic identity differs from tranches"):
        account_from_dict(payload)


def test_account_rejects_nonterminal_epoch_without_current_grant_binding() -> None:
    account, _, _, _ = _filled_strategic_account()
    payload = account.to_dict()
    payload["strategic_grant"] = None

    with pytest.raises(RuntimeError, match="nonterminal strategic epoch requires current grant"):
        account_from_dict(payload)


def test_terminal_epoch_is_only_historical_trading_provenance() -> None:
    account, _, _, _ = _filled_strategic_account()
    epoch = account.strategic_epochs[0]
    close_strategic_epoch(
        epoch,
        closed_session="2026-01-07",
        close_reason="owner exited",
    )
    account.active_strategic_epoch_id = ""
    account.positions.clear()

    restored = account_from_dict(account.to_dict())

    assert restored.strategic_epochs[0].terminal is True
    assert restored.order_ledger[0].epoch_id == epoch.epoch_id
    assert restored.fills[0].epoch_id == epoch.epoch_id


def test_account_rejects_terminal_epoch_as_live_position_owner() -> None:
    account, _, _, _ = _filled_strategic_account()
    epoch = account.strategic_epochs[0]
    close_strategic_epoch(
        epoch,
        closed_session="2026-01-07",
        close_reason="corrupt retained position",
    )
    account.active_strategic_epoch_id = ""

    with pytest.raises(RuntimeError, match="position cannot reference terminal strategic epoch"):
        account_from_dict(account.to_dict())


def test_broker_fill_activates_the_matching_epoch_once() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.data_hash = "data"
    account.code_hash = grant.production_source_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    target = Target(
        symbol=grant.candidate_symbol,
        weight=grant.target_weight,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=0.9,
        confidence=0.95,
        reason="bounded strategic probe",
        reason_code="strategic_cohort",
        origin_subsystem=OriginSubsystem.STRATEGIC.value,
        mechanism=AttributionMechanism.STRATEGIC_COHORT.value,
        origin_lifecycle=Lifecycle.CORE.value,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    attributed = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-05",
        targets=(target,),
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=attributed,
        account=account,
        prices={grant.candidate_symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=[],
            current=planned,
            submitted_date="2026-01-05",
        )
    )
    order_id = account.pending_orders[0].order_id

    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-06",
            "cash": 1_999_000.0,
            "fills": [
                {
                    "fill_id": "broker-strategic-epoch",
                    "order_id": order_id,
                    "fill_date": "2026-01-06",
                    "symbol": "300308",
                    "side": "BUY",
                    "shares": 100,
                    "price": 10.0,
                    "final": True,
                    "remaining_shares": 0,
                }
            ],
            "positions": [
                {
                    "symbol": "300308",
                    "shares": 100,
                    "sellable_shares": 0,
                    "avg_cost": 10.0,
                }
            ],
        },
    )

    assert account.active_strategic_epoch_id == epoch.epoch_id
    assert account.strategic_epochs[0].realized_status == StrategicEpochStatus.ACTIVE.value
    assert account.strategic_epoch == 1
