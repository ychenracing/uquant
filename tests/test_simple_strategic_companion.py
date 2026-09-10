"""Simple ordinary signals cannot replace existing strategic companion proof."""
from dataclasses import replace

import pandas as pd
import pytest
from test_simple_ordinary_policy import _frame
from test_strategic_probe_holding import _filled_probe

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.strategic_grant import StrategicQualificationObservation
from uquant.portfolio.ordinary import ordinary_core_entry
from uquant.types import AccountState


def test_actual_strategic_owner_requires_existing_independent_companion_qualification():
    policy, account, dates, _, leaders, _ = _filled_probe()
    assert account.fills and all(p.shares > 0 and p.epoch_id for p in account.positions.values())
    symbol = 'sh688110'
    score = replace(next(iter(leaders.values())), symbol=symbol, industry='foundry', mature=True)
    frame = _frame()
    frame.index = pd.bdate_range(end=dates[0], periods=len(frame))
    args = dict(symbol=symbol, score=score, date=dates[0], user_panel={symbol: frame},
                confirmation_days=policy.cfg.leader_tenure_days,
                market={'as_of': str(dates[0].date()), 'simple_backdrop_confirmed': True})
    assert ordinary_core_entry(policy, account=AccountState.empty(2_000_000.), **args)['block'] == 'READY'
    result = ordinary_core_entry(policy, account=account, **args)
    assert result['block'] == 'CONFIRMATION_INCOMPLETE'
    assert result['confirmations']['independent_core'] == 0


def test_valid_current_group_certificate_still_funds_and_fills_an_ordinary_companion():
    from test_shared_core_qualification import CHALLENGER, OWNER, _decide, _held_book

    policy, account, dates, panel, leaders, roles = _held_book()
    shares = account.positions[OWNER].shares
    identity = (account.strategic_grant.grant_id, account.strategic_epochs[0].epoch_id)
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles)
    orders = [o for o in account.pending_orders if o.symbol == CHALLENGER and o.side == 'BUY']
    assert len(orders) == 1 and 0 < orders[0].target_weight <= .30
    assert not orders[0].grant_id and not orders[0].epoch_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_cohort_confirm_days], account=account, panel=panel)
    assert any(f.symbol == CHALLENGER and f.side == 'BUY' for f in fills)
    assert account.positions[OWNER].shares == shares
    assert (account.strategic_grant.grant_id, account.strategic_epochs[0].epoch_id) == identity
    assert not account.positions[CHALLENGER].grant_id and not account.positions[CHALLENGER].epoch_id


@pytest.mark.parametrize('change,expected', [
    ({}, 'CONFIRMATION_INCOMPLETE'),
    ({'qualification_last_observed_session': '2020-01-01'}, 'READY'),
    ({'deployment_blocked': True}, 'READY'),
    ({'qualification_quorum': 'ABSOLUTE_SINGLE'}, 'READY'),
    ({'qualification_ready': True}, 'READY'),
    ({'qualification_streak': 0}, 'READY'),
])
def test_only_current_pending_full_formation_requires_strict_ordinary_proof(change, expected):
    from test_lifecycle_and_risk import _leader

    frame = _frame()
    date = frame.index[-1]
    account = AccountState.empty(2_000_000.)
    account.strategic_qualification = replace(StrategicQualificationObservation(
        qualification_last_observed_session=str(date.date()),
        qualification_quorum='FULL_COHORT', qualification_streak=1,
    ), **change)
    from uquant.portfolio import PortfolioAllocator

    result = ordinary_core_entry(PortfolioAllocator(DEFAULT_CONFIG), symbol='sh688110',
                                 score=_leader('sh688110', .9, industry='foundry'),
                                 date=date, user_panel={'sh688110': frame}, account=account,
                                 confirmation_days=DEFAULT_CONFIG.leader_tenure_days,
                                 market={'as_of': str(date.date()), 'simple_backdrop_confirmed': True})
    assert result['block'] == expected
