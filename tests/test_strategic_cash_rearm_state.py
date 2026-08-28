from __future__ import annotations

import copy
import json
from typing import Any

import pandas as pd
import pytest
from test_strategic_cash_rearm import _risk, _roles, _strict_inputs

from uquant.account.codec import account_from_dict
from uquant.account.migrations import migrate_account
from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.models.strategic_rearm import (
    StrategicCashRearmRejectionReason,
    StrategicCashRearmState,
    StrategicCashRearmStatus,
    derive_strategic_cash_rearm_authorization_id,
    validate_strategic_cash_rearm_state,
)
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio.strategic.rearm import (
    consume_strategic_cash_rearm_authorization,
    observe_flat_book_capital_repair_state,
    observe_strategic_cash_rearm_state,
)
from uquant.types import (
    AccountState,
    Opportunity,
    PendingOrder,
    StrategicQualificationObservation,
)


def _observation(
    *,
    candidate: str = "sz300394",
    ready: bool = True,
    evidence: str = "a" * 64,
    block_reason: str = "freeze_new_risk",
) -> StrategicQualificationObservation:
    return StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=f"qualification:{candidate}",
        qualification_route="established",
        qualification_evidence_sha256=evidence,
        qualification_ready=ready,
        deployment_blocked=bool(block_reason),
        deployment_block_reason=block_reason,
        qualification_streak=3 if ready else 0,
        qualification_last_observed_session="2026-01-05",
        qualification_quorum="FULL_COHORT",
        candidate_symbols=list(
            dict.fromkeys((candidate, "sz300308", "sz300394", "sz300502"))
        ),
        evidence_family_status={
            "OWNER_ABSOLUTE_QUALITY": "CONFIRMED",
            "INDUSTRY_CONFIRMATION": "CONFIRMED",
            "MARKET_CONFIRMATION": "CONFIRMED",
            "ROBUSTNESS_CONFIRMATION": "CONFIRMED",
        },
    )


def _flat_account() -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:cash-rearm"
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    return account


def _ready_account() -> AccountState:
    account = _flat_account()
    for session in pd.bdate_range("2025-01-02", periods=60):
        observe_flat_book_capital_repair_state(
            account=account,
            risk=_risk(),
            universe=_roles(str(session.date())),
            observed_session=str(session.date()),
            cfg=DEFAULT_CONFIG,
        )
    assert account.flat_book_capital_repair.status == "READY"
    return account


def _authorize(
    account: AccountState,
    *,
    observation: StrategicQualificationObservation | None = None,
    session: str = "2025-04-01",
    roles: Any | None = None,
    snapshots: dict[str, dict[str, float]] | None = None,
) -> StrategicCashRearmState:
    default_snapshots, leaders = _strict_inputs()
    current = observation or _observation()
    account.strategic_qualification = current
    return observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(session) if roles is None else roles,
        snapshots=default_snapshots if snapshots is None else snapshots,
        leaders=leaders,
        observation=current,
        observed_session=session,
        cfg=DEFAULT_CONFIG,
    )


def _identity_fields(account: AccountState) -> dict[str, object]:
    repair = account.flat_book_capital_repair
    roles = _roles("2025-04-01")
    return {
        "account_identity": account.account_identity,
        "repair_episode_id": repair.repair_episode_id,
        "candidate_symbol": "sz300394",
        "qualification_signature": "qualification:sz300394",
        "qualification_route": "established",
        "qualification_quorum": "FULL_COHORT",
        "qualification_evidence_sha256": "a" * 64,
        "capital_budget_level": 3,
        "tradable_universe_identity": roles.tradable_identity,
        "qualification_reference_universe_identity": (
            roles.qualification_reference_identity
        ),
        "risk_reference_universe_identity": roles.risk_reference_identity,
        "point_in_time_industry_identity": roles.point_in_time_industry_identity,
        "authorized_session": "2025-04-01",
    }


def test_rearm_authorization_identity_is_deterministic_and_candidate_bound() -> None:
    account = _ready_account()
    identity = _identity_fields(account)
    first = derive_strategic_cash_rearm_authorization_id(**identity)  # type: ignore[arg-type]

    assert first == derive_strategic_cash_rearm_authorization_id(  # type: ignore[arg-type]
        **identity
    )
    assert first.startswith("rearm_")
    for field, replacement in (
        ("repair_episode_id", "repair_" + "0" * 64),
        ("candidate_symbol", "sz300502"),
        ("qualification_evidence_sha256", "f" * 64),
        ("qualification_quorum", "STRONG_PAIR"),
        ("authorized_session", "2025-04-02"),
    ):
        changed = dict(identity)
        changed[field] = replacement
        assert (
            derive_strategic_cash_rearm_authorization_id(  # type: ignore[arg-type]
                **changed
            )
            != first
        )


def test_account_round_trip_preserves_ready_repair_and_authorization() -> None:
    account = _ready_account()
    account.data_hash = "data"
    account.code_hash = "code"
    _authorize(account)

    restored = account_from_dict(account.to_dict())

    assert restored == account
    assert restored.flat_book_capital_repair.status == "READY"
    assert isinstance(restored.strategic_cash_rearm, StrategicCashRearmState)


def test_account_rejects_rearm_authorization_bound_to_other_identity() -> None:
    account = _ready_account()
    account.data_hash = "data"
    account.code_hash = "code"
    _authorize(account)
    payload = account.to_dict()
    payload["strategic_cash_rearm"]["authorization_id"] = "rearm_" + "0" * 64

    with pytest.raises(RuntimeError, match="authorization identity"):
        account_from_dict(payload)


def test_current_schema_requires_explicit_candidate_authorization_state() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    payload = account.to_dict()
    payload.pop("strategic_cash_rearm")

    with pytest.raises(RuntimeError, match="current account schema requires strategic_cash_rearm"):
        account_from_dict(payload)


def test_schema_six_migration_discards_unbound_rearm_magic_flags_fail_closed(
    tmp_path: Any,
) -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code:old"
    payload = account.to_dict()
    payload["schema_version"] = 6
    payload.pop("flat_book_capital_repair")
    payload.pop("strategic_cash_rearm")
    payload["candidate_tenure"].update(
        {
            "strategic_cash_rearm_budget_level": 3,
            "strategic_cash_rearm_healthy_sessions": 19,
            "strategic_cash_rearm_authorized": 1,
            "strategic_cash_rearm_grant": 1,
            "strategic_cash_rearm_candidate_strict": 1,
        }
    )
    source = tmp_path / "schema-six.json"
    destination = tmp_path / "current.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        source,
        destination,
        new_code_hash="code:new",
        acknowledge_code_change=True,
    )

    assert migrated.schema_version == 8
    assert migrated.strategic_cash_rearm == StrategicCashRearmState()
    assert not any(
        key.startswith("strategic_cash_rearm_") for key in migrated.candidate_tenure
    )


def test_rearm_state_rejects_contradictory_authorization() -> None:
    account = _ready_account()
    state = _authorize(account)
    state.rejection_reasons = [
        StrategicCashRearmRejectionReason.RISK_NOT_NORMAL.value,
    ]

    with pytest.raises(ValueError, match="authorized rearm cannot retain rejection reasons"):
        validate_strategic_cash_rearm_state(state)


def test_repair_not_ready_cannot_authorize_a_qualified_candidate() -> None:
    account = _flat_account()
    state = _authorize(account)

    assert state.status == StrategicCashRearmStatus.OBSERVING.value
    assert state.rejection_reasons == [
        StrategicCashRearmRejectionReason.FLAT_BOOK_REPAIR_NOT_READY.value,
    ]
    assert not state.authorization_id


def test_undamaged_account_does_not_create_candidate_rearm_state() -> None:
    """Catches ordinary grants acquiring an invalid empty repair binding."""

    account = AccountState.empty(2_000_000.0)
    account.opportunity = Opportunity.TREND.value
    state = _authorize(account)

    assert state == StrategicCashRearmState()


def test_candidate_identity_change_gets_new_authorization_from_same_ready_repair() -> None:
    account = _ready_account()
    repair_id = account.flat_book_capital_repair.repair_episode_id
    first = _authorize(account)
    second = _authorize(
        account,
        observation=_observation(candidate="sz300502", evidence="b" * 64),
        session="2025-04-02",
    )

    assert first.authorization_id != second.authorization_id
    assert first.repair_episode_id == second.repair_episode_id == repair_id
    assert account.flat_book_capital_repair.status == "READY"


def test_candidate_invalidation_revokes_authorization_without_resetting_repair() -> None:
    account = _ready_account()
    first = _authorize(account)
    empty = StrategicQualificationObservation()
    snapshots, leaders = _strict_inputs()
    invalidated = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-02"),
        snapshots=snapshots,
        leaders=leaders,
        observation=empty,
        observed_session="2025-04-02",
        cfg=DEFAULT_CONFIG,
    )
    replacement = _authorize(
        account,
        observation=_observation(candidate="sz300502", evidence="b" * 64),
        session="2025-04-03",
    )

    assert first.status == StrategicCashRearmStatus.AUTHORIZED.value
    assert invalidated.status == StrategicCashRearmStatus.INVALIDATED.value
    assert not invalidated.authorization_id
    assert account.flat_book_capital_repair.status == "READY"
    assert replacement.status == StrategicCashRearmStatus.AUTHORIZED.value
    assert replacement.candidate_symbol == "sz300502"


@pytest.mark.parametrize(
    ("roles", "observation", "reason"),
    (
        (
            build_strategic_universe_roles(
                as_of="2025-04-01",
                tradable_symbols=("sz300308", "sz300502"),
                qualification_reference_symbols=(
                    "sz300308",
                    "sz300394",
                    "sz300502",
                ),
                risk_reference_symbols=("sh000300", "sh000682"),
                industries={
                    "sz300308": "optical",
                    "sz300394": "optical",
                    "sz300502": "optical",
                },
                available_symbols=(
                    "sz300308",
                    "sz300394",
                    "sz300502",
                    "sh000300",
                    "sh000682",
                ),
            ),
            _observation(),
            StrategicCashRearmRejectionReason.CANDIDATE_NOT_TRADABLE.value,
        ),
        (
            _roles("2025-04-01"),
            _observation(block_reason="strategic_cooldown"),
            StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE.value,
        ),
    ),
)
def test_candidate_authorization_rejects_nontradable_or_nonrearmable_state(
    roles: Any,
    observation: StrategicQualificationObservation,
    reason: str,
) -> None:
    account = _ready_account()
    state = _authorize(account, roles=roles, observation=observation)

    assert reason in state.rejection_reasons
    assert not state.authorization_id
    assert account.flat_book_capital_repair.status == "READY"


def test_route_quality_failure_cannot_consume_ready_account_repair() -> None:
    account = _ready_account()
    snapshots, _ = _strict_inputs()
    weak = copy.deepcopy(snapshots)
    weak["sz300394"]["leader_score"] = 0.01
    state = _authorize(account, snapshots=weak)

    assert (
        StrategicCashRearmRejectionReason.ROUTE_ABSOLUTE_QUALITY_FAILED.value
        in state.rejection_reasons
    )
    assert not state.authorization_id
    assert account.flat_book_capital_repair.status == "READY"


def test_typed_rearm_authorization_is_consumed_once_by_its_bound_grant() -> None:
    account = _ready_account()
    _authorize(account)

    consumed = consume_strategic_cash_rearm_authorization(
        account,
        grant_id="grant_" + "1" * 64,
    )

    assert consumed.status == StrategicCashRearmStatus.CONSUMED.value
    assert consumed.consumed_grant_id == "grant_" + "1" * 64
    assert consumed.authorized is False
    assert account.flat_book_capital_repair.status == "CONSUMED"
    with pytest.raises(RuntimeError, match="already consumed"):
        consume_strategic_cash_rearm_authorization(
            account,
            grant_id="grant_" + "2" * 64,
        )


def test_unfilled_grant_attempt_holds_then_releases_the_ready_repair_episode() -> None:
    """Catches grant retry incorrectly forcing another sixty-session repair."""

    account = _ready_account()
    authorization = _authorize(account)
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol=authorization.candidate_symbol,
        qualification_signature=authorization.qualification_signature,
        qualification_route=authorization.qualification_route,
        qualification_evidence_sha256=authorization.qualification_evidence_sha256,
        created_session=authorization.authorized_session,
        previous_grant_id="",
        production_source_identity="code:production",
        authorization_id=authorization.authorization_id,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=authorization.candidate_symbol,
        qualification_signature=authorization.qualification_signature,
        qualification_route=authorization.qualification_route,
        qualification_evidence_sha256=authorization.qualification_evidence_sha256,
        created_session=authorization.authorized_session,
        last_eligible_session=authorization.authorized_session,
        target_weight=0.20,
        account_identity=account.account_identity,
        production_source_identity="code:production",
        qualification_quorum=authorization.qualification_quorum,
        authorization_id=authorization.authorization_id,
    )
    consume_strategic_cash_rearm_authorization(account, grant_id=grant_id)
    repair_id = account.flat_book_capital_repair.repair_episode_id
    account.pending_orders = [
        PendingOrder(
            signal_date="2025-04-01",
            symbol=authorization.candidate_symbol,
            side="BUY",
            target_weight=0.20,
            reason="bounded rearm probe",
            lifecycle="PROBE",
            grant_id=grant_id,
        )
    ]

    held = observe_flat_book_capital_repair_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-02"),
        observed_session="2025-04-02",
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders.clear()
    account.strategic_grant.status = StrategicGrantStatus.EXPIRED.value
    account.strategic_grant.expiry_reason = "candidate_or_route_no_longer_qualified"
    released = observe_flat_book_capital_repair_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-03"),
        observed_session="2025-04-03",
        cfg=DEFAULT_CONFIG,
    )
    replacement = _authorize(
        account,
        observation=_observation(candidate="sz300502", evidence="b" * 64),
        session="2025-04-03",
    )

    assert held.status == "CONSUMED"
    assert held.healthy_session_count == 60
    assert held.repair_episode_id == repair_id
    assert released.status == "READY"
    assert released.repair_episode_id == repair_id
    assert replacement.status == StrategicCashRearmStatus.AUTHORIZED.value
    assert replacement.authorization_id != authorization.authorization_id


def test_account_round_trip_requires_nonterminal_rearm_grant_binding() -> None:
    account = _ready_account()
    account.data_hash = "data"
    account.code_hash = "code:production"
    authorization = _authorize(account)
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol=authorization.candidate_symbol,
        qualification_signature=authorization.qualification_signature,
        qualification_route=authorization.qualification_route,
        qualification_evidence_sha256=authorization.qualification_evidence_sha256,
        created_session=authorization.authorized_session,
        previous_grant_id="",
        production_source_identity=account.code_hash,
        authorization_id=authorization.authorization_id,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=authorization.candidate_symbol,
        qualification_signature=authorization.qualification_signature,
        qualification_route=authorization.qualification_route,
        qualification_evidence_sha256=authorization.qualification_evidence_sha256,
        created_session=authorization.authorized_session,
        last_eligible_session=authorization.authorized_session,
        target_weight=0.20,
        status=StrategicGrantStatus.QUALIFIED.value,
        account_identity=account.account_identity,
        production_source_identity=account.code_hash,
        qualification_quorum=authorization.qualification_quorum,
        authorization_id=authorization.authorization_id,
    )
    consume_strategic_cash_rearm_authorization(account, grant_id=grant_id)

    restored = account_from_dict(account.to_dict())
    assert restored == account

    payload = account.to_dict()
    payload["strategic_cash_rearm"]["consumed_grant_id"] = "grant_" + "f" * 64
    with pytest.raises(RuntimeError, match="rearm grant binding"):
        account_from_dict(payload)
