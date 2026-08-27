from __future__ import annotations

import copy

from uquant.account.codec import account_from_dict
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    StrategicQualificationObservation,
    derive_strategic_grant_id,
)
from uquant.types import AccountState


def _grant() -> StrategicGrantIntent:
    return StrategicGrantIntent(
        grant_id=derive_strategic_grant_id(
            account_identity="account:primary",
            candidate_symbol="sz300308",
            qualification_signature="qual:stable",
            qualification_route="long_cycle",
            qualification_evidence_sha256="a" * 64,
            created_session="2024-02-20",
            previous_grant_id="",
            production_source_identity="code:3915a94",
        ),
        candidate_symbol="sz300308",
        qualification_signature="qual:stable",
        qualification_route="long_cycle",
        qualification_evidence_sha256="a" * 64,
        created_session="2024-02-20",
        last_eligible_session="2024-02-20",
        target_weight=0.82,
        status=StrategicGrantStatus.QUALIFIED.value,
        account_identity="account:primary",
        production_source_identity="code:3915a94",
    )


def test_grant_id_is_deterministic_and_ignores_retry_state() -> None:
    first = _grant()
    restarted = copy.deepcopy(first)
    restarted.status = StrategicGrantStatus.PARTIALLY_FILLED.value
    restarted.last_submission_session = "2024-02-22"
    restarted.healthy_retry_sessions = 2
    restarted.submitted_order_ids.extend(["ord-1", "ord-2"])
    restarted.filled_shares = 300

    assert restarted.grant_id == first.grant_id
    assert restarted.grant_id.startswith("grant_")


def test_account_round_trip_preserves_grant_and_observation() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    account.account_identity = "account:primary"
    account.strategic_qualification = StrategicQualificationObservation(
        candidate_symbol="sz300308",
        qualification_signature="qual:stable",
        qualification_route="long_cycle",
        qualification_evidence_sha256="a" * 64,
        qualification_ready=True,
        deployment_blocked=True,
        deployment_block_reason="freeze_new_risk",
        qualification_streak=3,
        qualification_last_observed_session="2024-02-20",
    )
    account.strategic_grant = _grant()

    restored = account_from_dict(account.to_dict())

    assert restored == account
    assert isinstance(restored.strategic_qualification, StrategicQualificationObservation)
    assert isinstance(restored.strategic_grant, StrategicGrantIntent)


def test_current_schema_without_grant_fields_loads_without_economic_change() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    payload = account.to_dict()
    payload.pop("account_identity", None)
    payload.pop("strategic_qualification", None)
    payload.pop("strategic_grant", None)

    restored = account_from_dict(payload)

    assert restored.initial_cash == account.initial_cash
    assert restored.cash == account.cash
    assert restored.positions == account.positions
    assert restored.pending_orders == account.pending_orders
    assert restored.order_ledger == account.order_ledger
    assert restored.fills == account.fills
    assert restored.strategic_grant is None
    assert restored.strategic_qualification == StrategicQualificationObservation()


def test_grant_id_binds_candidate_route_evidence_and_source_identity() -> None:
    grant = _grant()
    base = {
        "account_identity": grant.account_identity,
        "candidate_symbol": grant.candidate_symbol,
        "qualification_signature": grant.qualification_signature,
        "qualification_route": grant.qualification_route,
        "qualification_evidence_sha256": grant.qualification_evidence_sha256,
        "created_session": grant.created_session,
        "previous_grant_id": grant.previous_grant_id,
        "production_source_identity": grant.production_source_identity,
    }

    for field, replacement in (
        ("candidate_symbol", "sz300502"),
        ("qualification_route", "risk_anchor"),
        ("qualification_evidence_sha256", "b" * 64),
        ("production_source_identity", "code:other"),
    ):
        changed = dict(base)
        changed[field] = replacement
        assert derive_strategic_grant_id(**changed) != grant.grant_id
