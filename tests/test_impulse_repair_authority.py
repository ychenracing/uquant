"""An impulse can deploy strategic capital only after genuine account repair."""

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_cash_rearm import _risk, _roles

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.rearm import observe_flat_book_capital_repair_state
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


@pytest.mark.parametrize(('level', 'required'), ((1, 20), (2, 40), (3, 60)))
def test_impulse_grant_consumes_authentic_flat_book_repair(level, required):
    dates = pd.bdate_range('2023-01-02', periods=247)
    close = np.full(len(dates), 1.20)
    close[125:205] = np.linspace(1.20, .90, 80)
    close[205:246] = np.linspace(.90, 1.20, 41)
    close[-1] = close[-2]
    frame = _strategic_frame(dates)
    frame['close'], frame['ma20'], frame['ma60'] = close, close * .95, close * .90
    frame['open'], frame['high'], frame['low'] = close, close * 1.01, close * .99
    frame['volume'] = 100_000_000.0
    symbols = ('sz300308', 'sz300502', 'sz300394')
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {symbol: _leader(symbol, .95 - .01 * index, mature=False, industry='optical')
               for index, symbol in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = 'account:impulse-repair'
    account.code_hash = 'source:impulse-repair'
    account.capital_budget_level = level
    account.opportunity = Opportunity.TREND.value
    # Observe genuine account health; no candidate qualification or authorization is injected.
    for date in dates[-required-1:-3]:
        state = observe_flat_book_capital_repair_state(
            account=account, risk=_risk(), universe=_roles(str(date.date())),
            observed_session=str(date.date()), cfg=DEFAULT_CONFIG,
        )
    assert state.healthy_session_count == required - 2
    assert not account.strategic_cash_rearm.authorization_id
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for index, date in enumerate(dates[-3:-1]):
        targets = allocator.allocate(
            date=date, opportunity=Opportunity.TREND, risk=_risk(), user_panel=panel,
            leaders=leaders, account=account,
            prices={s: float(panel[s].loc[date, 'close']) for s in symbols},
            strategic_universe=_roles(str(date.date())),
        )
        if index == 0:
            assert account.flat_book_capital_repair.healthy_session_count == required - 1
            assert account.strategic_grant is None
            assert not account.strategic_cash_rearm.authorization_id
    grant = account.strategic_grant
    assert grant is not None
    assert account.strategic_qualification.qualification_route == 'transition_impulse'
    assert grant.authorization_id
    assert account.flat_book_capital_repair.healthy_session_count == required
    assert account.flat_book_capital_repair.status == 'CONSUMED'
    assert account.strategic_cash_rearm.status == 'CONSUMED'
    assert account.strategic_cash_rearm.consumed_grant_id == grant.grant_id
    assert any(t.weight > 0 and t.grant_id == grant.grant_id for t in targets)
    attributed = attach_target_attribution(
        'optical', REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=[],
    )
    orders = plan_orders(
        signal_date=str(date.date()), targets=attributed, account=account,
        prices={s: float(panel[s].loc[date, 'close']) for s in symbols}, cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=str(date.date()),
    ))
    before = account.cash
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=account, panel=panel)
    assert fills and all(f.side == 'BUY' and f.shares > 0 for f in fills)
    assert all(f.grant_id == grant.grant_id and f.epoch_id for f in fills)
    assert account.cash < before
    assert len(account.strategic_epochs) == 1
    assert all(f.epoch_id == account.strategic_epochs[0].epoch_id for f in fills)
