from __future__ import annotations

from test_strategic_epoch import _epoch, _grant

from uquant.models.strategic_epoch import StrategicEpochStatus
from uquant.portfolio.strategic.authority import (
    assess_strategic_capital_authority,
    normalize_orphan_strategic_capital_residue,
)
from uquant.types import AccountState, PendingOrder, Position


def test_flat_unbacked_strategy_containers_are_orphan_residue_not_live_authority() -> None:
    account = AccountState.empty(2_000_000.0)
    account.strategic_cohort_symbols = ["sz300394"]
    account.strategic_cohort_targets = {"sz300394": 0.20}
    account.anchor_weights = {"sz300394": 0.10}
    account.protected_weights = {"sz300394": 0.10}
    account.strategic_restore_weights = {"sz300394": 0.10}
    account.recovery_conviction_symbol = "sz300394"
    account.tactical_anchor_symbol = "sz300394"

    authority = assess_strategic_capital_authority(account)

    assert authority.all_cash is True
    assert authority.has_live_authority is False
    assert authority.live_authority_fields == ()
    assert authority.orphan_residue_fields == (
        "anchor_weights",
        "protected_weights",
        "recovery_conviction_symbol",
        "strategic_cohort_symbols",
        "strategic_cohort_targets",
        "strategic_restore_weights",
        "tactical_anchor_symbol",
    )


def test_position_or_pending_execution_makes_bound_state_live_authority() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    epoch.first_fill_session = "2026-01-06"
    epoch.active_session = "2026-01-06"
    epoch.realized_status = StrategicEpochStatus.ACTIVE.value
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.active_strategic_epoch_id = epoch.epoch_id
    account.positions[grant.candidate_symbol] = Position(
        symbol=grant.candidate_symbol,
        shares=100,
        avg_cost=10.0,
        entry_date="2026-01-06",
        highest_close=10.0,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    account.pending_orders.append(
        PendingOrder(
            signal_date="2026-01-06",
            symbol=grant.candidate_symbol,
            side="BUY",
            target_weight=0.20,
            reason="pending probe",
            lifecycle="CORE",
            grant_id=grant.grant_id,
            epoch_id=epoch.epoch_id,
        )
    )
    account.protected_weights = {grant.candidate_symbol: 0.20}
    account.protected_weight_epoch_ids = {grant.candidate_symbol: epoch.epoch_id}

    authority = assess_strategic_capital_authority(account)

    assert authority.has_live_authority is True
    assert authority.all_cash is False
    assert "active_strategic_epoch_id" in authority.live_authority_fields
    assert "pending_orders" in authority.live_authority_fields
    assert "protected_weights" in authority.live_authority_fields


def test_nonterminal_epoch_and_grant_remain_authority_even_before_fill() -> None:
    grant = _grant()
    epoch = _epoch(grant)
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]

    authority = assess_strategic_capital_authority(account)

    assert authority.all_cash is True
    assert authority.has_live_authority is True
    assert authority.nonterminal_epoch_ids == (epoch.epoch_id,)
    assert authority.nonterminal_grant_id == grant.grant_id
    assert authority.orphan_residue_fields == ()


def test_unbound_flat_residue_is_preserved_and_fails_closed() -> None:
    account = AccountState.empty(2_000_000.0)
    account.strategic_cohort_symbols = ["sz300394"]
    account.strategic_cohort_targets = {"sz300394": 0.20}
    account.anchor_weights = {"sz300394": 0.10}
    account.protected_weights = {"sz300394": 0.10}
    account.strategic_restore_weights = {"sz300394": 0.10}
    account.recovery_conviction_symbol = "sz300394"
    account.tactical_anchor_symbol = "sz300394"

    normalized = normalize_orphan_strategic_capital_residue(account)

    assert normalized == ()
    assert assess_strategic_capital_authority(account).orphan_residue_fields == (
        "anchor_weights",
        "protected_weights",
        "recovery_conviction_symbol",
        "strategic_cohort_symbols",
        "strategic_cohort_targets",
        "strategic_restore_weights",
        "tactical_anchor_symbol",
    )


def test_terminal_epoch_residue_is_normalized_by_recorded_owner() -> None:
    grant = _grant()
    grant.status = "COMPLETED"
    epoch = _epoch(grant)
    epoch.realized_status = StrategicEpochStatus.CLOSED.value
    epoch.closed_session = "2026-01-07"
    epoch.close_reason = "owner_exit"
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.protected_weights = {grant.candidate_symbol: 0.10}
    account.protected_weight_epoch_ids = {grant.candidate_symbol: epoch.epoch_id}
    account.strategic_restore_weights = {grant.candidate_symbol: 0.10}
    account.strategic_restore_epoch_ids = {grant.candidate_symbol: epoch.epoch_id}
    account.anchor_weights = {grant.candidate_symbol: 0.10}
    account.recovery_owner_epoch_id = epoch.epoch_id
    account.recovery_conviction_symbol = grant.candidate_symbol
    account.tactical_anchor_symbol = grant.candidate_symbol

    normalized = normalize_orphan_strategic_capital_residue(account)

    assert normalized == (
        "anchor_weights",
        "protected_weight_epoch_ids",
        "protected_weights",
        "recovery_conviction_symbol",
        "recovery_owner_epoch_id",
        "strategic_restore_epoch_ids",
        "strategic_restore_weights",
        "tactical_anchor_symbol",
    )
    assert not account.protected_weights
    assert not account.strategic_restore_weights
    assert not account.anchor_weights


def test_live_backed_state_cannot_be_normalized_as_orphan_residue() -> None:
    account = AccountState.empty(2_000_000.0)
    account.positions["sz300394"] = Position(
        symbol="sz300394",
        shares=100,
        avg_cost=10.0,
        entry_date="2026-01-06",
        highest_close=10.0,
    )
    account.protected_weights = {"sz300394": 0.10}

    normalized = normalize_orphan_strategic_capital_residue(account)

    assert normalized == ()
    assert account.protected_weights == {"sz300394": 0.10}
