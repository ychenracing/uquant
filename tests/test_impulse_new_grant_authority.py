"""Future impulse admissions use ordinary capital without erasing their evidence."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _normal_risk, _strategic_frame

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import discovery
from uquant.portfolio.strategic.qualification_candidates import StrategicRoute
from uquant.portfolio.strategic.quorum import (
    StrategicQuorumResult,
    StrategicQuorumRoute,
)
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def test_confirmed_impulse_only_executes_ordinary_core_without_new_grant():
    dates = pd.bdate_range('2023-01-02', periods=247)
    close = np.full(len(dates), 1.20)
    close[125:205] = np.linspace(1.20, .90, 80)
    close[205:246] = np.linspace(.90, 1.08, 41)
    close[-1] = close[-2]
    frame = _strategic_frame(dates)
    frame['close'], frame['ma20'], frame['ma60'] = close, close * .95, close * .90
    frame['open'], frame['high'], frame['low'] = close, close * 1.01, close * .99
    frame['volume'] = 100_000_000.0
    symbols = ('sz300308', 'sz300502', 'sz300394')
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {symbol: _leader(symbol, .61 - .01 * index, mature=False, industry='optical')
               for index, symbol in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = 'account:impulse-test', 'source:impulse-test'
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-3:-1]:
        risk = _normal_risk()
        targets = policy.allocate(
            date=date, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
            leaders=leaders, account=account, prices={s: float(close[-2]) for s in symbols},
        )
    certificate = risk.evidence['core_allocation']['symbols'][symbols[0]]['entry']
    assert certificate['block'] == 'READY'
    assert certificate['qualification_route'] == 'transition_impulse'
    assert certificate['confirmations']['transition_impulse'] >= certificate['required_confirmation']
    assert account.strategic_grant is None and account.strategic_epochs == []
    assert targets and all(t.origin_subsystem == 'LEADER' and not t.grant_id and not t.epoch_id
                           for t in targets)
    assert all(t.weight <= DEFAULT_CONFIG.core_admission_weight for t in targets)
    attributed = attach_target_attribution(
        'optical', REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=[],
    )
    orders = plan_orders(signal_date=str(date.date()), targets=attributed, account=account,
                         prices={s: float(close[-2]) for s in symbols}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=str(date.date()),
    ))
    before = account.cash
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=account, panel=panel)
    assert fills and all(f.side == 'BUY' and f.shares > 0 and not f.grant_id and not f.epoch_id
                         for f in fills)
    assert account.cash < before and account.strategic_epochs == []


@pytest.mark.parametrize('durable_confirmed', (True, False))
@pytest.mark.parametrize('repair_ready', (False, True))
def test_new_grant_selector_does_not_let_impulse_mask_confirmed_durable_or_repair_fallback(
    monkeypatch, durable_confirmed, repair_ready,
):
    # This isolates ordering of already-evaluated certificates, not market proof.
    impulse = StrategicRoute(['a'], 'transition_impulse', None, False, [], False, False, 'a')
    durable = replace(impulse, symbols=['b'], owner_symbol='b', route='persistent_industry')
    quorum = StrategicQuorumResult(
        StrategicQuorumRoute.FULL_COHORT, True, True, True, True, True,
        (), (), (), 2, None,
    )
    monkeypatch.setattr(discovery, 'strategic_candidate_certificates', lambda *args, **kwargs: [
        (impulse, quorum, 2), (durable, quorum, 2 if durable_confirmed else 1),
    ])
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    # Ordering seam only; native repair observation/consumption is tested separately.
    if repair_ready:
        account.flat_book_capital_repair.status = "READY"
    selected = discovery._select_qualified_strategic_route(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots={"b": {"persistent_ret240": DEFAULT_CONFIG.strategic_cohort_min_ret240}}, leaders={}, risk=_normal_risk(),
        account=account, reference_snapshots={},
        strategic_universe=None, admission_open=True,
    )
    assert selected is (durable if durable_confirmed and not repair_ready else impulse)
