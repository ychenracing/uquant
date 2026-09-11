"""The selected main-policy candidate retains the existing maturity guard."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.market import ReplayUniverse
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Position


def test_real_mature_holding_retains_maturity_guard_on_march_damage():
    engine = ProductionEngine('data/frozen')
    engine.workspace.prepare(ReplayUniverse.from_symbols(
        tradable_symbols=('sh688041',), reference_symbols=(), index_symbols=()))
    frame = engine.workspace.feature_frame('sh688041')
    account = AccountState.empty(2_000_000.)
    account.positions['sh688041'] = Position(
        'sh688041', 4500, 85.82903074746, '2024-02-23', 87.481)
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    leaders = {'sh688041': replace(_leader('sh688041', .95), mature=True)}
    outcomes = [policy._leader_lifecycle_exit_confirmed(
        symbol='sh688041', date=date, user_panel={'sh688041': frame},
        leaders=leaders, account=account)
        for date in pd.to_datetime(['2024-03-28', '2024-03-29', '2024-04-01'])]
    assert outcomes == [False, False, False]
    assert leaders['sh688041'].mature


def test_maturity_loss_alone_never_sells_an_intact_winner():
    dates = pd.bdate_range('2024-01-02', periods=15)
    frame = pd.DataFrame({'close': 13., 'ma20': 14., 'ma60': 12., 'ret20': -.09}, index=dates)
    account = AccountState.empty(1000.)
    account.positions['sh688041'] = Position('sh688041', 100, 10., '2024-01-02', 15.)
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-3:]:
        assert not policy._leader_lifecycle_exit_confirmed(
            symbol='sh688041', date=date, user_panel={'sh688041': frame},
            leaders={'sh688041': replace(_leader('sh688041', .2), mature=False)}, account=account)
