"""Temporal production acceptance for bounded flat-book capital repair."""

from __future__ import annotations

import pandas as pd
import pytest
from test_strategic_cash_rearm import _qualification, _risk, _roles
from test_strategic_cash_rearm_state import _authorize, _ready_account

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_grant import StrategicQualificationObservation
from uquant.portfolio.strategic.rearm import (
    consume_strategic_cash_rearm_authorization,
    observe_flat_book_capital_repair_state,
)
from uquant.types import AccountState, Opportunity, PendingOrder


def _damaged_account(level: int) -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = f"account:repair-tier-{level}"
    account.capital_budget_level = level
    account.opportunity = Opportunity.TREND.value
    return account


def _observe(account: AccountState, session: str) -> None:
    observe_flat_book_capital_repair_state(
        account=account,
        risk=_risk(),
        universe=_roles(session),
        observed_session=session,
        cfg=DEFAULT_CONFIG,
    )


@pytest.mark.parametrize(
    ("level", "target", "required"),
    ((1, 0, 20), (2, 1, 40), (3, 2, 60), (4, 3, 60)),
)
def test_each_repair_tier_uses_the_exact_temporal_boundary(
    level: int,
    target: int,
    required: int,
) -> None:
    account = _damaged_account(level)
    sessions = [str(item.date()) for item in pd.bdate_range("2025-01-02", periods=required)]
    for session in sessions[:-1]:
        _observe(account, session)

    repair = account.flat_book_capital_repair
    assert repair.status == "ACCUMULATING"
    assert repair.healthy_session_count == required - 1
    assert repair.repair_target_level == target
    _observe(account, sessions[-2])
    assert account.flat_book_capital_repair.healthy_session_count == required - 1
    _observe(account, sessions[-1])
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == required
    assert account.capital_budget_level == level


def test_candidate_switch_does_not_reset_account_repair_and_ready_persists() -> None:
    account = _damaged_account(1)
    sessions = [str(item.date()) for item in pd.bdate_range("2025-01-02", periods=21)]
    account.strategic_qualification = _qualification()
    for session in sessions[:10]:
        _observe(account, session)
    repair_id = account.flat_book_capital_repair.repair_episode_id
    changed = _qualification()
    changed.candidate_symbol = "sz300502"
    changed.qualification_signature = "qualification:changed"
    account.strategic_qualification = changed
    for session in sessions[10:20]:
        _observe(account, session)

    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.repair_episode_id == repair_id
    account.strategic_qualification = StrategicQualificationObservation()
    _observe(account, sessions[20])
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == 20


def test_budget_worsening_and_live_execution_reset_the_repair_clock() -> None:
    account = _damaged_account(1)
    sessions = [str(item.date()) for item in pd.bdate_range("2025-01-02", periods=4)]
    _observe(account, sessions[0])
    first_id = account.flat_book_capital_repair.repair_episode_id
    account.capital_budget_level = 2
    _observe(account, sessions[1])
    assert account.flat_book_capital_repair.repair_episode_id != first_id
    assert account.flat_book_capital_repair.reset_reason == "CAPITAL_BUDGET_WORSENED"
    assert account.flat_book_capital_repair.healthy_session_count == 1

    account.pending_orders = [
        PendingOrder(
            signal_date=sessions[1],
            symbol="sz300308",
            side="BUY",
            target_weight=0.1,
            reason="observed pending execution",
            lifecycle="CORE",
        )
    ]
    _observe(account, sessions[2])
    assert account.flat_book_capital_repair.status == "RESET"
    assert account.flat_book_capital_repair.healthy_session_count == 0
    assert account.flat_book_capital_repair.reset_reason == "LIVE_CAPITAL_AUTHORITY"


def test_candidate_bound_authorization_is_consumed_exactly_once() -> None:
    account = _ready_account()
    authorization = _authorize(account)
    assert authorization.status == "AUTHORIZED"
    assert authorization.candidate_symbol == account.strategic_qualification.candidate_symbol

    consumed = consume_strategic_cash_rearm_authorization(
        account,
        grant_id="grant_" + "1" * 64,
    )
    assert consumed.status == "CONSUMED"
    assert consumed.consumed_grant_id == "grant_" + "1" * 64
    with pytest.raises(RuntimeError, match="already consumed"):
        consume_strategic_cash_rearm_authorization(
            account,
            grant_id="grant_" + "2" * 64,
        )
