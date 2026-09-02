from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_cash_rearm import _risk, _roles, _strict_inputs

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.models import strategic_rearm as rearm_model
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import rearm as rearm_policy
from uquant.types import (
    AccountState,
    Opportunity,
    PendingOrder,
    StrategicQualificationObservation,
)


def _account(*, budget_level: int = 3) -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:flat-book-repair"
    account.capital_budget_level = budget_level
    account.opportunity = Opportunity.TREND.value
    return account


def _observation(
    candidate: str,
    *,
    evidence: str,
    ready: bool = True,
) -> StrategicQualificationObservation:
    return StrategicQualificationObservation(
        candidate_symbol=candidate,
        qualification_signature=f"qualification:{candidate}",
        qualification_route="established",
        qualification_evidence_sha256=evidence,
        qualification_ready=ready,
        deployment_blocked=True,
        deployment_block_reason="freeze_new_risk",
        qualification_streak=3 if ready else 0,
        qualification_last_observed_session="2026-01-05",
        qualification_quorum="FULL_COHORT",
        candidate_symbols=list(dict.fromkeys((candidate, "sz300394", "sz300502"))),
    )


def _observe_repair(
    account: AccountState,
    *,
    session: str,
    risk: Any | None = None,
) -> Any:
    observe = rearm_policy.observe_flat_book_capital_repair_state
    return observe(
        account=account,
        risk=_risk() if risk is None else risk,
        universe=_roles(session),
        observed_session=session,
        cfg=DEFAULT_CONFIG,
    )


def test_flat_book_repair_ladder_maps_current_encoding_to_business_target() -> None:
    """Catches applying the 20-session level-zero bound to every persisted level."""

    requirement = rearm_policy.flat_book_capital_repair_requirement

    assert tuple(requirement(level) for level in (1, 2, 3, 4)) == (
        (0, 20),
        (1, 40),
        (2, 60),
        (3, 60),
    )


def test_flat_book_repair_identity_binds_account_damage_but_has_no_candidate_input() -> None:
    """Catches qualification identity leaking back into the account repair episode."""

    derive = rearm_model.derive_flat_book_capital_repair_episode_id
    inputs = {
        "account_identity": "account:flat-book-repair",
        "capital_budget_level": 3,
        "first_observed_session": "2024-01-31",
        "risk_reference_universe_identity": "a" * 64,
        "config_identity": config_fingerprint(DEFAULT_CONFIG),
    }

    first = derive(**inputs)
    assert first == derive(**inputs)
    assert first.startswith("repair_")
    for field, replacement in (
        ("account_identity", "account:other"),
        ("capital_budget_level", 2),
        ("first_observed_session", "2024-02-01"),
        ("risk_reference_universe_identity", "b" * 64),
        ("config_identity", "c" * 64),
    ):
        changed = dict(inputs)
        changed[field] = replacement
        assert derive(**changed) != first


def test_flat_book_repair_accumulates_across_candidate_changes_and_waits_ready() -> None:
    """Catches candidate churn owning or clearing the account damage-repair clock."""

    account = _account(budget_level=3)
    sessions = pd.bdate_range("2025-01-02", periods=60)
    state = None
    for index, session in enumerate(sessions):
        if index < 20:
            account.strategic_qualification = _observation(
                "sz300394",
                evidence=f"{index + 1:064x}",
            )
        elif index < 40:
            account.strategic_qualification = StrategicQualificationObservation()
        else:
            account.strategic_qualification = _observation(
                "sz300502",
                evidence=f"{index + 1:064x}",
            )
        state = _observe_repair(account, session=str(session.date()))

    assert state is not None
    assert state.status == "READY"
    assert state.healthy_session_count == 60
    assert state.required_healthy_sessions == 60
    assert state.last_ready_session == str(sessions[-1].date())
    assert state.capital_budget_level == 3
    assert account.capital_budget_level == 3
    assert account.capital_budget_repair_streak == 0


def test_production_discovery_advances_account_repair_without_a_candidate() -> None:
    """Catches qualification early returns starving the account-owned clock."""

    dates = pd.bdate_range("2023-01-02", periods=50)
    symbols = ("sz300308", "sz300394", "sz300502")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    weak_leaders = {
        symbol: _leader(symbol, 0.10, industry="optical") for symbol in symbols
    }
    account = _account()
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    allocator._initialize_strategic_cohort(
        date=dates[-1],
        user_panel=panel,
        leaders=weak_leaders,
        account=account,
        risk=_risk(),
        strategic_universe=_roles(str(dates[-1].date())),
    )

    assert not account.strategic_qualification.candidate_symbol
    assert account.flat_book_capital_repair.healthy_session_count == 1
    assert account.flat_book_capital_repair.last_counted_session == str(
        dates[-1].date()
    )


def test_flat_book_repair_counts_a_session_once_and_holds_during_market_pause() -> None:
    """Catches duplicate calls or CHOPPY sessions fabricating/resetting repair progress."""

    account = _account()
    first = _observe_repair(account, session="2025-01-02")
    duplicate = _observe_repair(account, session="2025-01-02")
    account.opportunity = Opportunity.CHOPPY.value
    paused = _observe_repair(account, session="2025-01-03")
    account.opportunity = Opportunity.TREND.value
    resumed = _observe_repair(account, session="2025-01-06")

    assert first.healthy_session_count == 1
    assert duplicate.healthy_session_count == 1
    assert paused.healthy_session_count == 1
    assert paused.status == "BLOCKED"
    assert "OPPORTUNITY_NOT_TREND" in paused.rejection_reasons
    assert resumed.healthy_session_count == 2
    assert resumed.repair_episode_id == first.repair_episode_id


def test_flat_book_repair_budget_worsening_starts_a_new_episode() -> None:
    """Catches old healthy evidence surviving a newly worse capital-damage tier."""

    account = _account(budget_level=2)
    first = _observe_repair(account, session="2025-01-02")
    account.capital_budget_level = 3
    reset = _observe_repair(account, session="2025-01-03")

    assert first.healthy_session_count == 1
    assert reset.repair_episode_id != first.repair_episode_id
    assert reset.healthy_session_count == 1
    assert reset.last_reset_session == "2025-01-03"
    assert reset.reset_reason == "CAPITAL_BUDGET_WORSENED"


def test_ready_repair_authorizes_only_the_current_independently_qualified_candidate() -> None:
    """Catches authorization inheriting a prior candidate's identity or repair streak."""

    account = _account()
    snapshots, leaders = _strict_inputs()
    for session in pd.bdate_range("2025-01-02", periods=60):
        _observe_repair(account, session=str(session.date()))
    repair_id = account.flat_book_capital_repair.repair_episode_id
    first_observation = _observation("sz300394", evidence="a" * 64)
    account.strategic_qualification = first_observation
    first = rearm_policy.observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-01"),
        snapshots=snapshots,
        leaders=leaders,
        observation=first_observation,
        observed_session="2025-04-01",
        cfg=DEFAULT_CONFIG,
    )
    second_observation = _observation("sz300502", evidence="b" * 64)
    account.strategic_qualification = second_observation
    second = rearm_policy.observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-02"),
        snapshots=snapshots,
        leaders=leaders,
        observation=second_observation,
        observed_session="2025-04-02",
        cfg=DEFAULT_CONFIG,
    )

    assert first.status == "AUTHORIZED"
    assert first.repair_episode_id == repair_id
    assert first.candidate_symbol == "sz300394"
    assert second.status == "AUTHORIZED"
    assert second.repair_episode_id == repair_id
    assert second.candidate_symbol == "sz300502"
    assert second.authorization_id != first.authorization_id
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == 60


def test_ready_repair_without_candidate_creates_no_authorization() -> None:
    """Catches account repair READY being mistaken for a production capital grant."""

    account = _account()
    for session in pd.bdate_range("2025-01-02", periods=60):
        _observe_repair(account, session=str(session.date()))
    snapshots, leaders = _strict_inputs()
    empty = StrategicQualificationObservation()
    state = rearm_policy.observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-01"),
        snapshots=snapshots,
        leaders=leaders,
        observation=empty,
        observed_session="2025-04-01",
        cfg=DEFAULT_CONFIG,
    )

    assert account.flat_book_capital_repair.status == "READY"
    assert state.status != "AUTHORIZED"
    assert not state.authorization_id
    assert account.strategic_grant is None
    assert not account.strategic_epochs


def test_account_round_trip_preserves_repair_episode_and_candidate_authorization() -> None:
    """Catches restart merging the account and candidate clocks back together."""

    account = _account()
    account.data_hash = "data"
    account.code_hash = "code"
    for session in pd.bdate_range("2025-01-02", periods=60):
        _observe_repair(account, session=str(session.date()))
    snapshots, leaders = _strict_inputs()
    observation = _observation("sz300394", evidence="a" * 64)
    account.strategic_qualification = observation
    rearm_policy.observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-01"),
        snapshots=snapshots,
        leaders=leaders,
        observation=observation,
        observed_session="2025-04-01",
        cfg=DEFAULT_CONFIG,
    )

    restored = account_from_dict(account.to_dict())

    assert restored == account
    state_type = rearm_model.FlatBookCapitalRepairState
    assert isinstance(restored.flat_book_capital_repair, state_type)
    assert restored.strategic_cash_rearm.repair_episode_id == (
        restored.flat_book_capital_repair.repair_episode_id
    )


def test_account_rejects_repair_episode_identity_not_bound_to_damage_evidence() -> None:
    """Catches durable repair progress being moved to another economic episode."""

    account = _account()
    account.data_hash = "data"
    account.code_hash = "code"
    _observe_repair(account, session="2025-01-02")
    payload = account.to_dict()
    payload["flat_book_capital_repair"]["repair_episode_id"] = (
        "repair_" + "0" * 64
    )

    with pytest.raises(RuntimeError, match="repair episode identity"):
        account_from_dict(payload)


def test_account_rejects_candidate_authorization_from_other_repair_episode() -> None:
    """Catches a candidate authorization borrowing another account repair clock."""

    account = _account()
    account.data_hash = "data"
    account.code_hash = "code"
    for session in pd.bdate_range("2025-01-02", periods=60):
        _observe_repair(account, session=str(session.date()))
    snapshots, leaders = _strict_inputs()
    observation = _observation("sz300394", evidence="a" * 64)
    authorization = rearm_policy.observe_strategic_cash_rearm_state(
        account=account,
        risk=_risk(),
        universe=_roles("2025-04-01"),
        snapshots=snapshots,
        leaders=leaders,
        observation=observation,
        observed_session="2025-04-01",
        cfg=DEFAULT_CONFIG,
    )
    other_episode = "repair_" + "0" * 64
    authorization.repair_episode_id = other_episode
    authorization.authorization_id = (
        rearm_model.derive_strategic_cash_rearm_authorization_id(
            account_identity=account.account_identity,
            repair_episode_id=other_episode,
            candidate_symbol=authorization.candidate_symbol,
            qualification_signature=authorization.qualification_signature,
            qualification_route=authorization.qualification_route,
            qualification_quorum=authorization.qualification_quorum,
            qualification_evidence_sha256=(
                authorization.qualification_evidence_sha256
            ),
            capital_budget_level=authorization.capital_budget_level,
            tradable_universe_identity=authorization.tradable_universe_identity,
            qualification_reference_universe_identity=(
                authorization.qualification_reference_universe_identity
            ),
            risk_reference_universe_identity=(
                authorization.risk_reference_universe_identity
            ),
            point_in_time_industry_identity=(
                authorization.point_in_time_industry_identity
            ),
            authorized_session=authorization.authorized_session,
        )
    )

    with pytest.raises(RuntimeError, match="repair episode binding"):
        account_from_dict(account.to_dict())


def test_current_schema_requires_explicit_flat_book_repair_state() -> None:
    """Catches silently reviving a candidate-owned repair clock after restart."""

    account = AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    payload = account.to_dict()
    payload.pop("flat_book_capital_repair")

    with pytest.raises(
        RuntimeError,
        match="current account schema requires flat_book_capital_repair",
    ):
        account_from_dict(payload)


def test_live_execution_resets_progress_without_clearing_the_order() -> None:
    """Catches repair logic hiding real broker authority to preserve a backtest clock."""

    account = _account()
    first = _observe_repair(account, session="2025-01-02")
    account.pending_orders.append(
        PendingOrder(
            signal_date="2025-01-02",
            symbol="sz300394",
            side="BUY",
            target_weight=0.20,
            reason="pending",
            lifecycle="PROBE",
        )
    )
    reset = _observe_repair(account, session="2025-01-03")

    assert first.healthy_session_count == 1
    assert reset.status == "RESET"
    assert reset.healthy_session_count == 0
    assert reset.reset_reason == "LIVE_CAPITAL_AUTHORITY"
    assert account.pending_orders


def test_flat_repair_fails_closed_for_unbound_owner_residue() -> None:
    """Catches daily observation guessing that an unbound restore intent is harmless."""

    account = _account()
    account.strategic_cohort_symbols = ["sz300394"]
    account.strategic_cohort_targets = {"sz300394": 0.20}
    account.protected_weights = {"sz300394": 0.10}

    state = _observe_repair(account, session="2025-01-02")

    predicate = next(
        item
        for item in state.predicate_results
        if item.code == "ORPHAN_RESIDUE_NORMALIZED"
    )
    assert state.healthy_session_count == 0
    assert state.status == "BLOCKED"
    assert predicate.passed is False
    assert predicate.orphan_residue is True
    assert predicate.authoritative_state == {
        "detected_fields": [
            "protected_weights",
            "strategic_cohort_symbols",
            "strategic_cohort_targets",
        ],
        "normalized_fields": [],
    }
    assert account.strategic_cohort_symbols == ["sz300394"]
    assert account.strategic_cohort_targets == {"sz300394": 0.20}
    assert account.protected_weights == {"sz300394": 0.10}
