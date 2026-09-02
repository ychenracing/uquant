from __future__ import annotations

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_epoch import _epoch, _grant
from test_strategic_grant_observation import _risk

from uquant.account import save_account
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.strategic_epoch import (
    StrategicEpochStatus,
    bind_account_strategic_ownership,
    close_account_strategic_epoch,
    record_account_strategic_epoch_fill,
)
from uquant.models.strategic_grant import StrategicGrantStatus
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountOrder,
    AccountState,
    Opportunity,
    OrderStatus,
    Position,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _active_account() -> tuple[AccountState, str, str]:
    grant = _grant()
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.code_hash = grant.production_source_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.strategic_cohort_symbols = [grant.candidate_symbol]
    account.strategic_cohort_targets = {grant.candidate_symbol: grant.target_weight}
    record_account_strategic_epoch_fill(
        account,
        epoch_id=epoch.epoch_id,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2026-01-06",
        filled_shares=10_000,
    )
    account.positions[grant.candidate_symbol] = Position(
        symbol=grant.candidate_symbol,
        shares=10_000,
        avg_cost=10.0,
        entry_date="2026-01-06",
        highest_close=10.0,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    return account, grant.grant_id, epoch.epoch_id


def test_epoch_close_waits_for_positions_and_unsettled_execution() -> None:
    account, grant_id, epoch_id = _active_account()

    with pytest.raises(RuntimeError, match="position"):
        close_account_strategic_epoch(
            account,
            epoch_id=epoch_id,
            closed_session="2026-02-02",
            close_reason="owner_exit",
        )

    account.positions.clear()
    account.order_ledger.append(
        AccountOrder(
            order_id="O000000001",
            signal_date="2026-02-01",
            submitted_date="2026-02-01",
            symbol="sz300308",
            side="SELL",
            target_weight=0.0,
            reason="owner exit",
            lifecycle="CORE",
            status=OrderStatus.OPEN.value,
            remaining_shares=10_000,
            grant_id=grant_id,
            epoch_id=epoch_id,
        )
    )

    with pytest.raises(RuntimeError, match="execution"):
        close_account_strategic_epoch(
            account,
            epoch_id=epoch_id,
            closed_session="2026-02-02",
            close_reason="owner_exit",
        )

    account.order_ledger[0].status = OrderStatus.FILLED.value
    account.order_ledger[0].last_event = "BROKER_CANCELLED"
    close_account_strategic_epoch(
        account,
        epoch_id=epoch_id,
        closed_session="2026-02-02",
        close_reason="owner_exit",
    )

    assert account.strategic_epochs[0].realized_status == StrategicEpochStatus.CLOSED.value
    assert account.active_strategic_epoch_id == ""
    assert account.strategic_grant is not None
    assert account.strategic_grant.status == StrategicGrantStatus.COMPLETED.value
    assert account.strategic_grant.terminal is True


def test_epoch_close_waits_for_a_late_fill_eligible_cancelled_remainder() -> None:
    account, grant_id, epoch_id = _active_account()
    account.positions.clear()
    account.order_ledger.append(
        AccountOrder(
            order_id="O000000001",
            signal_date="2026-02-01",
            submitted_date="2026-02-01",
            symbol="sz300308",
            side="BUY",
            target_weight=0.20,
            reason="probe remainder",
            lifecycle="CORE",
            status=OrderStatus.CANCELLED.value,
            requested_shares=10_000,
            filled_shares=2_000,
            remaining_shares=8_000,
            cancel_reason="strategic partial remainder replaced",
            grant_id=grant_id,
            epoch_id=epoch_id,
        )
    )

    with pytest.raises(RuntimeError, match="unsettled execution"):
        close_account_strategic_epoch(
            account,
            epoch_id=epoch_id,
            closed_session="2026-02-02",
            close_reason="owner_exit",
        )

    account.order_ledger[0].remaining_shares = 0
    close_account_strategic_epoch(
        account,
        epoch_id=epoch_id,
        closed_session="2026-02-02",
        close_reason="owner_exit",
    )

    assert account.strategic_epochs[0].realized_status == StrategicEpochStatus.CLOSED.value


def test_epoch_close_clears_only_state_owned_by_the_closed_epoch() -> None:
    account, _grant_id, epoch_id = _active_account()
    account.positions.clear()
    account.protected_weights = {"sz300308": 0.20, "sz300502": 0.10}
    account.protected_weight_epoch_ids = {
        "sz300308": epoch_id,
        "sz300502": "external-owner",
    }
    account.strategic_restore_weights = {"sz300308": 0.20, "sz300502": 0.10}
    account.strategic_restore_epoch_ids = {
        "sz300308": epoch_id,
        "sz300502": "external-owner",
    }
    account.anchor_weights = {"sz300308": 0.20}
    account.recovery_owner_epoch_id = epoch_id

    close_account_strategic_epoch(
        account,
        epoch_id=epoch_id,
        closed_session="2026-02-02",
        close_reason="owner_exit",
    )

    assert account.protected_weights == {"sz300502": 0.10}
    assert account.protected_weight_epoch_ids == {"sz300502": "external-owner"}
    assert account.strategic_restore_weights == {"sz300502": 0.10}
    assert account.strategic_restore_epoch_ids == {"sz300502": "external-owner"}
    assert account.anchor_weights == {}
    assert account.recovery_owner_epoch_id == ""


def test_expired_probe_reselects_a_different_owner_with_a_new_identity_chain() -> None:
    dates = pd.bdate_range("2023-01-02", periods=255)
    initial_symbols = ("sz300308", "sz300502", "sz300394")
    initial_panel = {symbol: _strategic_frame(dates) for symbol in initial_symbols}
    initial_leaders = {
        symbol: _leader(symbol, 0.95 - index * 0.02, industry="optical")
        for index, symbol in enumerate(initial_symbols)
    }
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for session in dates[-7:-5]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=initial_panel,
            leaders=initial_leaders,
            account=account,
            prices={symbol: float(initial_panel[symbol].loc[session, "close"]) for symbol in initial_symbols},
        )
    first_grant = account.strategic_grant
    assert first_grant is not None
    first_epoch = account.strategic_epochs[-1]

    successor_symbols = ("sz300502", "sz300394", "sh688008")
    successor_panel = {
        symbol: initial_panel.get(symbol, _strategic_frame(dates))
        for symbol in successor_symbols
    }
    successor_leaders = {
        symbol: _leader(symbol, 0.96 - index * 0.02, industry="optical")
        for index, symbol in enumerate(successor_symbols)
    }
    allocator.allocate(
        date=dates[-5],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=successor_panel,
        leaders=successor_leaders,
        account=account,
        prices={symbol: float(successor_panel[symbol].loc[dates[-5], "close"]) for symbol in successor_symbols},
    )

    assert first_grant.status == StrategicGrantStatus.EXPIRED.value
    assert first_epoch.realized_status == StrategicEpochStatus.EXPIRED.value

    for session in dates[-4:-2]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=successor_panel,
            leaders=successor_leaders,
            account=account,
            prices={symbol: float(successor_panel[symbol].loc[session, "close"]) for symbol in successor_symbols},
        )

    second_grant = account.strategic_grant
    second_epoch = account.strategic_epochs[-1]
    assert second_grant is not None
    assert second_grant.grant_id != first_grant.grant_id
    assert second_grant.candidate_symbol != first_grant.candidate_symbol
    assert second_grant.previous_grant_id == first_grant.grant_id
    assert second_epoch.epoch_id != first_epoch.epoch_id
    assert second_epoch.previous_epoch_id == first_epoch.epoch_id
    assert len(account.strategic_epochs) == 2


def test_completed_epoch_can_regrant_the_same_owner_only_with_new_ids() -> None:
    account, first_grant_id, first_epoch_id = _active_account()
    account.positions.clear()
    close_account_strategic_epoch(
        account,
        epoch_id=first_epoch_id,
        closed_session="2026-02-02",
        close_reason="owner_exit",
    )
    account.strategic_last_exit_date = "2026-02-02"
    account.strategic_rearm_date = "2026-03-16"
    account.strategic_previous_symbols = ["sz300308"]
    account.strategic_cohort_symbols.clear()
    account.strategic_cohort_targets.clear()
    account.candidate_tenure["strategic_cohort_active"] = 0

    dates = pd.bdate_range("2025-03-03", "2026-03-18")
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.96 - index * 0.02, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for session in dates[-2:]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(panel[symbol].loc[session, "close"]) for symbol in symbols},
        )

    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == "sz300308"
    assert account.strategic_grant.grant_id != first_grant_id
    assert account.strategic_grant.previous_grant_id == first_grant_id
    assert account.strategic_epochs[-1].epoch_id != first_epoch_id
    assert account.strategic_epochs[-1].previous_epoch_id == first_epoch_id


def test_full_cohort_epoch_only_peers_persist_before_and_after_fill(tmp_path) -> None:
    dates = pd.bdate_range("2023-01-02", periods=251)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.96 - index * 0.02, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    targets = ()
    for session in dates[-3:-1]:
        targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(panel[symbol].loc[session, "close"]) for symbol in symbols},
        )

    assert account.strategic_grant is not None
    assert {target.symbol for target in targets if target.weight > 0} == set(symbols)
    grant_by_symbol = {
        target.symbol: target.grant_id for target in targets if target.weight > 0
    }
    assert grant_by_symbol[account.strategic_grant.candidate_symbol] == (
        account.strategic_grant.grant_id
    )
    assert all(
        not grant_id
        for symbol, grant_id in grant_by_symbol.items()
        if symbol != account.strategic_grant.candidate_symbol
    )
    assert {target.epoch_id for target in targets if target.weight > 0} == {
        account.strategic_grant.epoch_id
    }

    signal_date = str(dates[-2].date())
    attributed = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=signal_date,
        targets=targets,
    )
    planned = plan_orders(
        signal_date=signal_date,
        targets=attributed,
        account=account,
        prices={
            symbol: float(panel[symbol].loc[dates[-2], "close"])
            for symbol in symbols
        },
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=[],
            current=planned,
            submitted_date=signal_date,
        )
    )

    save_account(account, tmp_path / "pending.json")

    execution_panel = {
        symbol: pd.DataFrame(
            {
                "open": [3.0, 3.0],
                "high": [3.1, 3.1],
                "low": [2.9, 2.9],
                "close": [3.0, 3.0],
                "volume": [10_000_000.0, 10_000_000.0],
                "amount": [30_000_000.0, 30_000_000.0],
            },
            index=dates[-2:],
        )
        for symbol in symbols
    }
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1],
        account=account,
        panel=execution_panel,
    )
    peer_symbols = set(symbols) - {account.strategic_grant.candidate_symbol}
    assert {fill.symbol for fill in fills if not fill.grant_id} == peer_symbols
    assert {
        symbol for symbol, position in account.positions.items() if not position.grant_id
    } == peer_symbols
    save_account(account, tmp_path / "filled.json")


def test_strategic_protection_restore_and_recovery_bind_to_active_epoch() -> None:
    account, _grant_id, epoch_id = _active_account()
    account.protected_weights = {"sz300308": 0.20}
    account.strategic_restore_weights = {"sz300308": 0.20}
    account.anchor_weights = {"sz300308": 0.20}

    bind_account_strategic_ownership(account)

    assert account.protected_weight_epoch_ids == {"sz300308": epoch_id}
    assert account.strategic_restore_epoch_ids == {"sz300308": epoch_id}
    assert account.recovery_owner_epoch_id == epoch_id
