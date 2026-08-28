from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from uquant.account.codec import account_from_dict
from uquant.account.migrations import migrate_account
from uquant.account.validation_strategy import validate_strategy_risk_state
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    activate_strategic_epoch,
    derive_strategic_epoch_id,
)
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
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
        epoch.validate()


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


def test_epoch_identity_flows_through_target_order_fill_and_position() -> None:
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

    assert len(fills) == 1
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


def test_legacy_active_owner_migrates_to_exactly_one_epoch(tmp_path) -> None:
    grant = _grant()
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.data_hash = "data"
    account.code_hash = grant.production_source_identity
    account.strategic_grant = grant
    target = Target(
        symbol=grant.candidate_symbol,
        weight=grant.target_weight,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=0.9,
        confidence=0.95,
        reason="prequalified strategic leader cohort",
        reason_code="strategic_cohort",
        origin_subsystem=OriginSubsystem.STRATEGIC.value,
        mechanism=AttributionMechanism.STRATEGIC_COHORT.value,
        origin_lifecycle=Lifecycle.CORE.value,
        grant_id=grant.grant_id,
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
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1],
        account=account,
        panel={grant.candidate_symbol: frame},
    )
    account.strategic_cohort_symbols = [grant.candidate_symbol]
    account.strategic_cohort_targets = {grant.candidate_symbol: grant.target_weight}
    account.strategic_epoch = 1
    payload = account.to_dict()
    payload["schema_version"] = 5
    for field_name in (
        "strategic_successor_qualification",
        "strategic_epochs",
        "active_strategic_epoch_id",
        "protected_weight_epoch_ids",
        "strategic_restore_epoch_ids",
        "recovery_owner_epoch_id",
        "strategic_tradable_universe_identity",
        "strategic_qualification_universe_identity",
        "strategic_risk_universe_identity",
    ):
        payload.pop(field_name)
    payload["strategic_grant"].pop("epoch_id")
    for collection in ("pending_orders", "order_ledger", "fills"):
        for item in payload[collection]:
            item.pop("epoch_id")
    for position in payload["positions"].values():
        position.pop("epoch_id")
        for tranche in position["tranches"]:
            tranche.pop("epoch_id")
    source = tmp_path / "legacy-account.json"
    destination = tmp_path / "migrated-account.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        source,
        destination,
        new_code_hash="code:ownership",
        acknowledge_code_change=True,
    )

    assert len(migrated.strategic_epochs) == 1
    epoch = migrated.strategic_epochs[0]
    assert epoch.owner_symbol == "sz300308"
    assert epoch.realized_status == StrategicEpochStatus.ACTIVE.value
    assert migrated.active_strategic_epoch_id == epoch.epoch_id
    assert migrated.strategic_grant is not None
    assert migrated.strategic_grant.epoch_id == epoch.epoch_id
    assert migrated.positions["sz300308"].epoch_id == epoch.epoch_id
    assert {item.epoch_id for item in migrated.positions["sz300308"].tranches} == {
        epoch.epoch_id
    }
