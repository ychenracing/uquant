"""Prospective first strategic deployment boundary; native economic inventory."""

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _normal_risk, _strategic_frame
from test_strategic_probe_holding import _allocate, _filled_probe

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _register(account, targets, date, panel):
    attributed = attach_target_attribution(
        'optical', REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=[],
    )
    orders = plan_orders(
        signal_date=str(date.date()), targets=attributed, account=account,
        prices={s: float(f.loc[date, 'close']) for s, f in panel.items()}, cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=str(date.date()),
    ))
    return orders


@pytest.mark.parametrize('filled', (False, True), ids=('unsettled-buy', 'ordinary-position'))
def test_first_strategic_grant_waits_for_native_ordinary_capital_to_settle(filled):
    dates = pd.bdate_range('2023-01-02', periods=280)
    incumbent = 'sh688008'
    symbols = (incumbent, 'sz300308', 'sz300502', 'sz300394')
    panel = {s: _strategic_frame(dates) for s in symbols}
    for frame in panel.values():
        frame['open'], frame['high'], frame['low'] = frame['close'], frame['close'] * 1.01, frame['close'] * .99
        frame['volume'] = 100_000_000.0
    leaders = {s: _leader(s, .95 - i * .01, industry='optical') for i, s in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = 'account:bootstrap', 'source:bootstrap'
    # Native ordinary strategy produces and registers its actual BUY before the
    # independent strategic-discovery boundary is exercised with current evidence.
    ordinary = PortfolioAllocator(DEFAULT_CONFIG.override(strategic_dynamic_enabled=False))
    for date in dates[240:245]:
        targets = ordinary.allocate(
            date=date, opportunity=Opportunity.TREND, risk=_normal_risk(),
            user_panel={incumbent: panel[incumbent]}, leaders={incumbent: leaders[incumbent]},
            account=account, prices={incumbent: float(panel[incumbent].loc[date, 'close'])},
        )
    orders = _register(account, targets, date, panel)
    assert orders and all(o.side == 'BUY' and not o.grant_id for o in orders)
    if filled:
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[245], account=account, panel=panel)
        assert fills and all(f.shares > 0 and not f.grant_id for f in fills)
        assert account.positions[incumbent].shares > 0
    else:
        assert not account.positions and account.pending_orders
    leaders[incumbent] = _leader(incumbent, .50, industry='optical')
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[246:250]:
        allocator._initialize_strategic_cohort(
            date=date, user_panel=panel, leaders=leaders, account=account, risk=_normal_risk(),
        )
    assert account.strategic_qualification.qualification_ready
    assert account.strategic_grant is None and not account.strategic_epochs
    if filled:
        assert not account.positions[incumbent].grant_id
        targets = allocator.allocate(
            date=dates[250], opportunity=Opportunity.TREND, risk=_normal_risk(),
            user_panel=panel, leaders=leaders, account=account,
            prices={s: float(f.loc[dates[250], 'close']) for s, f in panel.items()},
        )
        newcomers = [t for t in targets if t.symbol != incumbent and t.weight > 0]
        assert newcomers and all(t.origin_subsystem == 'LEADER' and not t.grant_id for t in newcomers)
        _register(account, targets, dates[250], panel)
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[251], account=account, panel=panel)
        assert any(f.side == 'BUY' and f.symbol != incumbent and f.shares > 0 for f in fills)
        assert all(not f.grant_id and not f.epoch_id for f in fills)


def test_existing_genuinely_filled_partial_strategic_deployment_is_preserved():
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=True)
    grant = account.strategic_grant
    epoch = account.strategic_epochs[0]
    assert grant is not None and grant.filled_shares > 0
    assert any(order.status == 'PARTIALLY_FILLED' for order in account.order_ledger)
    targets = _allocate(allocator, account, dates[0], panel, leaders, roles)
    assert account.strategic_grant.grant_id == grant.grant_id
    assert account.strategic_epochs[0].epoch_id == epoch.epoch_id
    assert targets and any(t.weight > 0 and t.grant_id == grant.grant_id for t in targets)
