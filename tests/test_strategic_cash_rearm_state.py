from __future__ import annotations

import copy
import json

import pandas as pd
import pytest
from test_strategic_cash_rearm import _risk, _roles, _strict_inputs

from uquant.account.codec import account_from_dict
from uquant.account.migrations import migrate_account
from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_rearm import (
    StrategicCashRearmPredicate,
    StrategicCashRearmRejectionReason,
    StrategicCashRearmState,
    StrategicCashRearmStatus,
    StrategicCashRearmStreakTransition,
    derive_strategic_cash_rearm_authorization_id,
    validate_strategic_cash_rearm_state,
)
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.portfolio.strategic.rearm import (
    consume_strategic_cash_rearm_authorization,
    observe_strategic_cash_rearm_state,
)
from uquant.types import (
    AccountState,
    Opportunity,
    PendingOrder,
    StrategicQualificationObservation,
)


def _identity_fields() -> dict[str, object]:
    return {
        "account_identity": "account:cash-rearm",
        "candidate_symbol": "sz300394",
        "qualification_signature": "qualification:optical",
        "qualification_route": "established",
        "qualification_quorum": "FULL_COHORT",
        "qualification_evidence_sha256": "a" * 64,
        "capital_budget_level": 3,
        "tradable_universe_identity": "b" * 64,
        "qualification_reference_universe_identity": "c" * 64,
        "risk_reference_universe_identity": "d" * 64,
        "point_in_time_industry_identity": "e" * 64,
        "required_healthy_sessions": 20,
    }


def _authorized_state() -> StrategicCashRearmState:
    identity = _identity_fields()
    authorization_id = derive_strategic_cash_rearm_authorization_id(**identity)
    return StrategicCashRearmState(
        observed_session="2024-03-28",
        candidate_symbol="sz300394",
        qualification_signature="qualification:optical",
        qualification_route="established",
        qualification_quorum="FULL_COHORT",
        qualification_evidence_sha256="a" * 64,
        capital_budget_level=3,
        tradable_universe_identity="b" * 64,
        qualification_reference_universe_identity="c" * 64,
        risk_reference_universe_identity="d" * 64,
        point_in_time_industry_identity="e" * 64,
        required_healthy_sessions=20,
        consecutive_healthy_sessions=20,
        status=StrategicCashRearmStatus.AUTHORIZED.value,
        authorization_id=authorization_id,
        authorized_session="2024-03-28",
        predicate_results=[
            StrategicCashRearmPredicate(
                code="NO_LIVE_CAPITAL_AUTHORITY",
                passed=True,
                authoritative_state={"live_authority": False},
                economic_authority=True,
                orphan_residue=False,
            )
        ],
        qualification_ready=True,
        route_consistent_absolute_quality=True,
        healthy=True,
        authorized=True,
        streak_transition=StrategicCashRearmStreakTransition.INCREMENTED.value,
    )


def _observation(
    *,
    candidate: str = "sz300394",
    ready: bool = True,
    evidence: str = "a" * 64,
    block_reason: str = "freeze_new_risk",
    invalidation_reason: str = "",
    signature: str | None = None,
) -> StrategicQualificationObservation:
    return StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=signature or f"qualification:{candidate}",
        qualification_route="established",
        qualification_evidence_sha256=evidence,
        qualification_ready=ready,
        deployment_blocked=bool(block_reason),
        deployment_block_reason=block_reason,
        qualification_streak=3 if ready else 0,
        qualification_last_observed_session="2026-01-05",
        qualification_quorum="FULL_COHORT",
        candidate_symbols=[candidate, "sz300308", "sz300502"],
        evidence_family_status={
            "OWNER_ABSOLUTE_QUALITY": "CONFIRMED",
            "INDUSTRY_CONFIRMATION": "CONFIRMED",
            "MARKET_CONFIRMATION": "CONFIRMED",
            "ROBUSTNESS_CONFIRMATION": "CONFIRMED",
        },
        candidate_invalidation_reason=invalidation_reason,
    )


def _flat_rearm_account() -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:cash-rearm"
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    return account


def test_rearm_authorization_identity_is_deterministic_and_binds_economic_evidence() -> None:
    identity = _identity_fields()
    first = derive_strategic_cash_rearm_authorization_id(**identity)
    second = derive_strategic_cash_rearm_authorization_id(**identity)

    assert first == second
    assert first.startswith("rearm_")

    for field, replacement in (
        ("candidate_symbol", "sz300502"),
        ("qualification_evidence_sha256", "f" * 64),
        ("qualification_quorum", "STRONG_PAIR"),
        ("capital_budget_level", 2),
        ("tradable_universe_identity", "1" * 64),
        ("point_in_time_industry_identity", "2" * 64),
        ("required_healthy_sessions", 40),
    ):
        changed = dict(identity)
        changed[field] = replacement
        assert derive_strategic_cash_rearm_authorization_id(**changed) != first


def test_account_round_trip_preserves_typed_rearm_state() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    account.account_identity = "account:cash-rearm"
    account.strategic_cash_rearm = _authorized_state()

    restored = account_from_dict(account.to_dict())

    assert restored == account
    assert isinstance(restored.strategic_cash_rearm, StrategicCashRearmState)
    assert isinstance(
        restored.strategic_cash_rearm.predicate_results[0],
        StrategicCashRearmPredicate,
    )


def test_account_rejects_rearm_authorization_bound_to_other_identity() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    account.account_identity = "account:cash-rearm"
    account.strategic_cash_rearm = _authorized_state()
    payload = account.to_dict()
    payload["strategic_cash_rearm"]["authorization_id"] = "rearm_" + "0" * 64

    with pytest.raises(RuntimeError, match="authorization identity"):
        account_from_dict(payload)


def test_current_schema_requires_explicit_typed_rearm_state() -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    payload = account.to_dict()
    payload.pop("strategic_cash_rearm")

    with pytest.raises(RuntimeError, match="current account schema requires strategic_cash_rearm"):
        account_from_dict(payload)


def test_schema_six_migration_drops_unbound_rearm_magic_flags_fail_closed(tmp_path) -> None:
    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code:old"
    payload = account.to_dict()
    payload["schema_version"] = 6
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
    destination = tmp_path / "schema-seven.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        source,
        destination,
        new_code_hash="code:new",
        acknowledge_code_change=True,
    )

    assert migrated.schema_version == 7
    assert migrated.strategic_cash_rearm == StrategicCashRearmState()
    assert not any(
        key.startswith("strategic_cash_rearm_") for key in migrated.candidate_tenure
    )


def test_rearm_state_rejects_unsorted_or_contradictory_authorization() -> None:
    state = _authorized_state()
    state.rejection_reasons = [
        StrategicCashRearmRejectionReason.RISK_NOT_NORMAL.value,
    ]

    with pytest.raises(ValueError, match="authorized rearm cannot retain rejection reasons"):
        validate_strategic_cash_rearm_state(state)

    invalidated = copy.deepcopy(state)
    invalidated.status = StrategicCashRearmStatus.INVALIDATED.value
    invalidated.authorized = False
    invalidated.healthy = False
    invalidated.authorized_session = ""
    invalidated.authorization_id = ""
    invalidated.rejection_reasons = [
        StrategicCashRearmRejectionReason.RISK_NOT_NORMAL.value,
        StrategicCashRearmRejectionReason.QUALIFICATION_NOT_READY.value,
    ]

    with pytest.raises(ValueError, match="rearm rejection reasons must be ordered"):
        validate_strategic_cash_rearm_state(invalidated)


def test_rearm_audit_persists_each_failed_authoritative_predicate() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    account.pending_orders.append(
        PendingOrder(
            signal_date="2026-01-02",
            symbol="sz300394",
            side="BUY",
            target_weight=0.20,
            reason="unsettled",
            lifecycle="CORE",
        )
    )

    state = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )

    predicates = {item.code: item for item in state.predicate_results}
    assert state.rejection_reasons == [
        StrategicCashRearmRejectionReason.PENDING_EXECUTION.value,
    ]
    assert predicates["PENDING_EXECUTION_CLEAR"].passed is False
    assert predicates["PENDING_EXECUTION_CLEAR"].economic_authority is True
    assert predicates["PENDING_EXECUTION_CLEAR"].authoritative_state == {
        "symbols": ["sz300394"]
    }
    assert state.consecutive_healthy_sessions == 0
    assert state.streak_transition == StrategicCashRearmStreakTransition.RESET_UNHEALTHY.value


def test_rearm_reference_audit_distinguishes_role_absent_from_expected_unavailable() -> None:
    from uquant.models.strategic_universe import build_strategic_universe_roles

    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    expected_missing = build_strategic_universe_roles(
        as_of="2026-01-05",
        tradable_symbols=("sz300308", "sz300394", "sz300502"),
        qualification_reference_symbols=(
            "sz300308",
            "sz300394",
            "sz300502",
            "sh688008",
        ),
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={
            "sz300308": "optical",
            "sz300394": "optical",
            "sz300502": "optical",
            "sh688008": "optical",
        },
        available_symbols=("sz300308", "sz300394", "sz300502", "sh000300", "sh000682"),
    )
    missing = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=expected_missing,
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    role_absent = build_strategic_universe_roles(
        as_of="2026-01-06",
        tradable_symbols=("sz300308", "sz300394", "sz300502"),
        qualification_reference_symbols=("sz300308", "sz300394", "sz300502"),
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={
            "sz300308": "optical",
            "sz300394": "optical",
            "sz300502": "optical",
        },
        available_symbols=("sz300308", "sz300394", "sz300502", "sh000300", "sh000682"),
    )
    absent = observe_strategic_cash_rearm_state(
        account=_flat_rearm_account(),
        risk=_risk(),
        universe=role_absent,
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-06",
        cfg=DEFAULT_CONFIG,
    )

    assert (
        StrategicCashRearmRejectionReason.QUALIFICATION_REFERENCE_UNAVAILABLE.value
        in missing.rejection_reasons
    )
    assert (
        StrategicCashRearmRejectionReason.QUALIFICATION_REFERENCE_UNAVAILABLE.value
        not in absent.rejection_reasons
    )
    expected_predicate = next(
        item for item in missing.predicate_results if item.code == "QUALIFICATION_REFERENCES_AVAILABLE"
    )
    absent_predicate = next(
        item for item in absent.predicate_results if item.code == "QUALIFICATION_REFERENCES_AVAILABLE"
    )
    assert expected_predicate.authoritative_state["expected_but_unavailable"] == ["sh688008"]
    assert absent_predicate.authoritative_state["expected_but_unavailable"] == []


def test_rearm_counts_each_ready_identity_once_per_session_and_resets_on_identity_change() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    first = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    duplicate = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    changed_observation = _observation(evidence="f" * 64)
    changed_observation.qualification_route = "persistent_industry"
    changed = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(),
        snapshots=snapshots,
        leaders=leaders,
        observation=changed_observation,
        observed_session="2026-01-06",
        cfg=DEFAULT_CONFIG,
    )

    assert first.consecutive_healthy_sessions == 1
    assert duplicate.consecutive_healthy_sessions == 1
    assert duplicate.streak_transition == (
        StrategicCashRearmStreakTransition.HELD_DUPLICATE_SESSION.value
    )
    assert changed.consecutive_healthy_sessions == 1
    assert changed.streak_transition == StrategicCashRearmStreakTransition.RESET_IDENTITY.value


def test_rearm_keeps_one_ready_anchor_until_a_new_formal_identity_or_invalidation() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    sessions = pd.bdate_range("2026-01-05", periods=20)

    state = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles(str(sessions[0].date())),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(evidence="a" * 64),
        observed_session=str(sessions[0].date()),
        cfg=DEFAULT_CONFIG,
    )
    for session in sessions[1:]:
        state = observe_strategic_cash_rearm_state(
            account=account,
            risk=_risk(),
            universe=_roles(str(session.date())),
            snapshots=snapshots,
            leaders=leaders,
            observation=_observation(ready=False, evidence="b" * 64),
            observed_session=str(session.date()),
            cfg=DEFAULT_CONFIG,
        )

    assert state.status == StrategicCashRearmStatus.AUTHORIZED.value
    assert state.qualification_ready is True
    assert state.qualification_evidence_sha256 == "a" * 64
    assert state.consecutive_healthy_sessions == 20


def test_rearm_uses_current_route_quality_instead_of_a_generic_discovery_label() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    first = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-05"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    retained = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-06"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(
            ready=False,
            invalidation_reason="absolute_qualification_failed",
        ),
        observed_session="2026-01-06",
        cfg=DEFAULT_CONFIG,
    )
    weak = copy.deepcopy(snapshots)
    weak["sz300394"]["leader_score"] = 0.01
    invalidated = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-07"),
        snapshots=weak,
        leaders=leaders,
        observation=_observation(
            ready=False,
            invalidation_reason="absolute_qualification_failed",
        ),
        observed_session="2026-01-07",
        cfg=DEFAULT_CONFIG,
    )

    assert first.consecutive_healthy_sessions == 1
    assert retained.consecutive_healthy_sessions == 2
    assert retained.qualification_ready is True
    assert invalidated.consecutive_healthy_sessions == 0
    assert (
        StrategicCashRearmRejectionReason.ROUTE_ABSOLUTE_QUALITY_FAILED.value
        in invalidated.rejection_reasons
    )


def test_hard_failure_resets_streak_without_destroying_the_formal_anchor() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    first = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-05"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    evidence = dict(_risk().evidence)
    evidence["reference_coverage"] = 0.99
    failed = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(evidence=evidence),
        universe=_roles("2026-01-06"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(ready=False),
        observed_session="2026-01-06",
        cfg=DEFAULT_CONFIG,
    )
    recovered = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-07"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(ready=False),
        observed_session="2026-01-07",
        cfg=DEFAULT_CONFIG,
    )

    assert first.consecutive_healthy_sessions == 1
    assert failed.status == StrategicCashRearmStatus.INVALIDATED.value
    assert failed.consecutive_healthy_sessions == 0
    assert recovered.candidate_symbol == first.candidate_symbol
    assert recovered.qualification_evidence_sha256 == first.qualification_evidence_sha256
    assert recovered.status == StrategicCashRearmStatus.OBSERVING.value
    assert recovered.consecutive_healthy_sessions == 1


def test_ready_successor_cannot_take_rearm_streak_until_anchor_quality_fails() -> None:
    snapshots, leaders = _strict_inputs()
    account = _flat_rearm_account()
    signature = "qualification:optical-cohort"
    first = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-05"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(signature=signature),
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )
    successor_read_only = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-06"),
        snapshots=snapshots,
        leaders=leaders,
        observation=_observation(
            candidate="sz300502",
            evidence="b" * 64,
            signature=signature,
        ),
        observed_session="2026-01-06",
        cfg=DEFAULT_CONFIG,
    )
    weak = copy.deepcopy(snapshots)
    weak["sz300394"]["leader_score"] = 0.01
    replacement = observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2026-01-07"),
        snapshots=weak,
        leaders=leaders,
        observation=_observation(
            candidate="sz300502",
            evidence="c" * 64,
            signature=signature,
        ),
        observed_session="2026-01-07",
        cfg=DEFAULT_CONFIG,
    )

    assert first.candidate_symbol == "sz300394"
    assert successor_read_only.candidate_symbol == "sz300394"
    assert successor_read_only.consecutive_healthy_sessions == 2
    assert replacement.candidate_symbol == "sz300502"
    assert replacement.consecutive_healthy_sessions == 1
    assert replacement.streak_transition == StrategicCashRearmStreakTransition.RESET_IDENTITY.value


@pytest.mark.parametrize(
    ("roles", "observation", "reason"),
    (
        (
            build_strategic_universe_roles(
                as_of="2026-01-05",
                tradable_symbols=("sz300308", "sz300502"),
                qualification_reference_symbols=("sz300308", "sz300394", "sz300502"),
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
            _observation(block_reason="freeze_new_risk"),
            StrategicCashRearmRejectionReason.CANDIDATE_NOT_TRADABLE.value,
        ),
        (
            _roles(),
            _observation(block_reason="strategic_cooldown"),
            StrategicCashRearmRejectionReason.DEPLOYMENT_BLOCK_NOT_REARMABLE.value,
        ),
    ),
)
def test_rearm_rejects_nontradable_candidate_and_nonrearmable_block(
    roles,
    observation: StrategicQualificationObservation,
    reason: str,
) -> None:
    snapshots, leaders = _strict_inputs()
    state = observe_strategic_cash_rearm_state(
        account=_flat_rearm_account(),
        risk=_risk(),
        universe=roles,
        snapshots=snapshots,
        leaders=leaders,
        observation=observation,
        observed_session="2026-01-05",
        cfg=DEFAULT_CONFIG,
    )

    assert reason in state.rejection_reasons
    assert state.consecutive_healthy_sessions == 0


def test_typed_rearm_authorization_is_consumed_once_by_its_bound_grant() -> None:
    account = _flat_rearm_account()
    account.strategic_cash_rearm = _authorized_state()

    consumed = consume_strategic_cash_rearm_authorization(
        account,
        grant_id="grant_" + "1" * 64,
    )

    assert consumed.status == StrategicCashRearmStatus.CONSUMED.value
    assert consumed.consumed_grant_id == "grant_" + "1" * 64
    assert consumed.authorized is False
    assert consumed.streak_transition == StrategicCashRearmStreakTransition.CONSUMED.value
    assert not any(
        key.startswith("strategic_cash_rearm_") for key in account.candidate_tenure
    )
    with pytest.raises(RuntimeError, match="already consumed"):
        consume_strategic_cash_rearm_authorization(
            account,
            grant_id="grant_" + "2" * 64,
        )


def test_account_round_trip_requires_nonterminal_rearm_grant_binding() -> None:
    account = _flat_rearm_account()
    account.data_hash = "data"
    account.code_hash = "code:production"
    account.strategic_cash_rearm = _authorized_state()
    authorization_id = account.strategic_cash_rearm.authorization_id
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol="sz300394",
        qualification_signature="qualification:optical",
        qualification_route="established",
        qualification_evidence_sha256="a" * 64,
        created_session="2024-03-28",
        previous_grant_id="",
        production_source_identity=account.code_hash,
        authorization_id=authorization_id,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol="sz300394",
        qualification_signature="qualification:optical",
        qualification_route="established",
        qualification_evidence_sha256="a" * 64,
        created_session="2024-03-28",
        last_eligible_session="2024-03-28",
        target_weight=0.20,
        status=StrategicGrantStatus.QUALIFIED.value,
        account_identity=account.account_identity,
        production_source_identity=account.code_hash,
        qualification_quorum="FULL_COHORT",
        authorization_id=authorization_id,
    )
    consume_strategic_cash_rearm_authorization(account, grant_id=grant_id)

    restored = account_from_dict(account.to_dict())
    assert restored == account

    payload = account.to_dict()
    payload["strategic_cash_rearm"]["consumed_grant_id"] = "grant_" + "f" * 64
    with pytest.raises(RuntimeError, match="rearm grant binding"):
        account_from_dict(payload)
