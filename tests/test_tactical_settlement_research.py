"""A real closed tactical order retires; pending or protected rights do not."""
from dataclasses import replace

import pandas as pd
import pytest
from test_core_bounded_risk_restoration import _restorable, _submit

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.recovery.current_cohort import allocate_confirmed_recovery
from uquant.types import AccountState, Opportunity, PendingOrder, Target


def _closed_tactical():
    policy, _, dates, panel, leaders, risk = _restorable()
    symbol = next(iter(panel))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = 'fixture-code', 'fixture-data'
    target = Target(symbol, .6, 'RECOVERY', .9, .9, 'tactical entry',
                    origin_subsystem='RECOVERY', mechanism='TACTICAL_REBOUND', origin_lifecycle='RECOVERY')
    _submit(account, dates[0], (target,))
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    buy = executor.execute_open(date=dates[1], account=account, panel=panel)
    assert len(buy) == 1 and buy[0].side == 'BUY'
    account.candidate_tenure.update(tactical_active=1, tactical_promotable=1)
    account.tactical_anchor_symbol = symbol
    exit_target = replace(target, weight=0., origin_subsystem='RISK', mechanism='CRISIS',
                          reason_code='crisis', exit_kind='crisis')
    _submit(account, dates[2], (exit_target,))
    sell = executor.execute_open(date=dates[3], account=account, panel=panel)
    assert len(sell) == 1 and sell[0].shares == buy[0].shares and not account.positions
    book = AllocationBook(policy, pd.Timestamp(dates[4]), risk, panel, leaders, account,
                          {symbol: 10.}, {}, set(), {}, {}, {}, 1.)
    return book, symbol


def test_actual_closed_tactical_retires_and_starts_original_cooldown():
    book, _ = _closed_tactical()
    fills, cash = list(book.account.fills), book.account.cash
    allocate_confirmed_recovery(book, opportunity=Opportunity.WEAK, frozen=True)
    account = book.account
    assert account.candidate_tenure['tactical_active'] == 0
    assert account.tactical_anchor_symbol == ''
    assert account.candidate_tenure['tactical_cooldown'] == DEFAULT_CONFIG.tactical_rebound_cooldown_days
    assert account.fills == fills and account.cash == cash and not account.pending_orders


@pytest.mark.parametrize('owner', ['protected', 'pending', 'unsettled', 'no_actual_buy'])
def test_settlement_cannot_erase_existing_rights_or_invent_execution(owner):
    book, symbol = _closed_tactical()
    account = book.account
    if owner == 'protected':
        account.protected_weights[symbol] = .6
    elif owner == 'pending':
        account.pending_orders = [PendingOrder(str(book.date.date()), symbol, 'BUY', .6, 'pending', 'RECOVERY')]
    elif owner == 'unsettled':
        account.order_ledger[-1].status = 'PARTIALLY_FILLED'
    else:
        account.fills.clear()
    allocate_confirmed_recovery(book, opportunity=Opportunity.WEAK, frozen=True)
    assert account.candidate_tenure['tactical_active'] == 1
    assert account.tactical_anchor_symbol == symbol


@pytest.mark.parametrize('case,exits', [
    ('profit', True), ('not_due', False), ('promotable', False),
    ('crisis', False), ('protected_recovery', False),
])
def test_frozen_tactical_exit_uses_existing_due_and_risk_permissions(case, exits):
    policy, _, dates, panel, leaders, risk = _restorable()
    symbol = next(iter(panel))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = 'fixture-code', 'fixture-data'
    entry = Target(symbol, .6, 'RECOVERY', .9, .9, 'tactical entry',
                   origin_subsystem='RECOVERY', mechanism='TACTICAL_REBOUND',
                   origin_lifecycle='RECOVERY')
    _submit(account, dates[0], (entry,))
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    buy = executor.execute_open(date=dates[1], account=account, panel=panel)
    assert len(buy) == 1 and buy[0].side == 'BUY'
    account.candidate_tenure.update(tactical_active=1, tactical_promotable=int(case == 'promotable'))
    account.tactical_anchor_symbol = symbol
    if case == 'crisis':
        risk = replace(risk, state=risk.state.CRISIS)
    if case == 'protected_recovery':
        account.protected_weights[symbol] = .6
    price = 10.2 if case == 'not_due' else 10.8
    book = AllocationBook(policy, pd.Timestamp(dates[2]), risk, panel, leaders, account,
                          {symbol: price}, {symbol: .6}, set(), {}, {}, {}, 1.)
    allocate_confirmed_recovery(book, opportunity=Opportunity.WEAK, frozen=True)
    assert book.proposed[symbol] == (0. if exits else .6)
    if exits:
        order = _submit(account, dates[2], tuple(book.recovery_targets.values()))
        assert len(order) == 1 and order[0].side == 'SELL' and order[0].order_id
        sell = executor.execute_open(date=dates[3], account=account, panel=panel)
        assert len(sell) == 1 and sell[0].side == 'SELL'
        assert sell[0].order_id == order[0].order_id
        assert sell[0].commission > 0 and sell[0].stamp_duty > 0
        assert account.cash == pytest.approx(
            DEFAULT_CONFIG.initial_cash - buy[0].gross_value - buy[0].commission
            - buy[0].transfer_fee + sell[0].gross_value - sell[0].commission
            - sell[0].stamp_duty - sell[0].transfer_fee
        )
        assert not account.positions and not account.pending_orders
    else:
        assert account.positions[symbol].shares == buy[0].shares
