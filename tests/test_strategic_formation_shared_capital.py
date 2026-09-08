"""Future formation shares actual capital with existing ordinary inventory."""

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _normal_risk, _strategic_frame

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _submit(account, targets, date, panel):
    targets = attach_target_attribution(
        'optical', REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=[],
    )
    orders = plan_orders(
        signal_date=str(date.date()), targets=targets, account=account,
        prices={s: float(frame.loc[date, 'close']) for s, frame in panel.items()}, cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=str(date.date()),
    ))
    return orders


def _allocate(policy, account, date, panel, leaders):
    risk = _normal_risk()
    risk.evidence.update(broad_ret120=.04, tech_ret120=.04, ai_fast_return=.16,
                         declining_ratio=.05, below_ma20_ratio=.05, tech_speed=.16, broad_speed=.02)
    return policy.allocate(
        date=date, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
        leaders=leaders, account=account,
        prices={s: float(frame.loc[date, 'close']) for s, frame in panel.items()},
    )


@pytest.mark.parametrize('persistent', (True, False), ids=('formation', 'ordinary-established'))
def test_new_candidate_uses_shared_capital_without_relabeling_ordinary_incumbent(persistent):
    dates = pd.bdate_range('2023-01-02', periods=280)
    incumbent = 'sh688008'
    newcomers = ('sz300308', 'sz300502', 'sz300394')
    panel = {s: _strategic_frame(dates) for s in (incumbent, *newcomers)}
    for symbol, frame in panel.items():
        if persistent and symbol in newcomers:
            frame['close'] = np.linspace(1.0, 4.0, len(dates))
        frame['ma20'], frame['ma60'] = frame['close'] * .95, frame['close'] * .85
        frame['open'], frame['high'], frame['low'] = frame['close'], frame['close'] * 1.01, frame['close'] * .99
        frame['volume'] = 100_000_000.0
    leaders = {incumbent: _leader(incumbent, .95, industry='compute')}
    leaders.update({s: _leader(s, .99 - i * .01, industry='optical') for i, s in enumerate(newcomers)})
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = 'account:shared-formation', 'source:shared-formation'
    ordinary = PortfolioAllocator(DEFAULT_CONFIG.override(strategic_dynamic_enabled=False))
    for date in dates[240:245]:
        targets = _allocate(ordinary, account, date, {incumbent: panel[incumbent]},
                            {incumbent: leaders[incumbent]})
    orders = _submit(account, targets, date, panel)
    assert orders and all(o.side == 'BUY' and not o.grant_id for o in orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[245], account=account, panel=panel)
    assert fills and all(f.side == 'BUY' and f.shares > 0 and not f.grant_id for f in fills)
    incumbent_shares = account.positions[incumbent].shares
    assert not account.pending_orders and not account.strategic_epochs
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[246:250]:
        targets = _allocate(policy, account, date, panel, leaders)
    if persistent:
        grant = account.strategic_grant
        assert grant is not None and grant.qualification_route == 'persistent_industry'
        assert grant.candidate_symbol in newcomers and not grant.authorization_id
    else:
        assert account.strategic_grant is None and not account.strategic_epochs
        assert account.strategic_qualification.qualification_route in {'established', 'transition'}
    assert account.positions[incumbent].shares == incumbent_shares
    assert not account.positions[incumbent].grant_id and not account.positions[incumbent].epoch_id
    orders = _submit(account, targets, date, panel)
    assert any(o.side == 'BUY' and o.symbol in newcomers for o in orders)
    assert not any(o.side == 'SELL' and o.symbol == incumbent for o in orders)
    before = account.cash
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[250], account=account, panel=panel)
    new_buys = [f for f in fills if f.side == 'BUY' and f.symbol in newcomers]
    assert new_buys and all(f.shares > 0 for f in new_buys)
    if persistent:
        assert any(f.grant_id == grant.grant_id and f.epoch_id == grant.epoch_id for f in new_buys)
    else:
        assert all(not f.grant_id and not f.epoch_id for f in new_buys)
    assert 0 <= account.cash < before
    assert account.positions[incumbent].shares == incumbent_shares
    assert not account.positions[incumbent].grant_id and not account.positions[incumbent].epoch_id
