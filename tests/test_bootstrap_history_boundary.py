"""Native funded/zero-fill history at the first-deployment boundary."""
from dataclasses import replace

import pandas as pd
from test_failed_deployment_settlement_barrier import _native_day, _native_fixture
from test_first_strategic_deployment_boundary import _register
from test_strategic_probe_holding import (
    OWNER,
    _decide_and_submit,
    _entry_deteriorated,
    _filled_probe,
    _submit,
)
from test_strategic_universe_quorum import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio.strategic.discovery import (
    _first_deployment_has_capital_in_use,
    _settle_terminal_strategic_grant,
)
from uquant.types import Target


def _ordinary_target(symbol):
    # An explicit ordinary capital/execution seam, not fabricated historical fills.
    return Target(symbol, .1, 'CORE', .95, .95, 'confirmed core admitted from available account capital',
                  origin_subsystem='LEADER', mechanism='LEADER_SELECTION', origin_lifecycle='CORE')


def test_closed_genuinely_funded_epoch_exempts_later_ordinary_inventory():
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    frozen = replace(_risk(), target_gross_cap=0.0, freeze_new_risk=True)
    orders = _decide_and_submit(allocator, account, dates[0], panel,
                                _entry_deteriorated(leaders), roles, risk=frozen)
    assert orders and all(o.side == 'SELL' for o in orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(f.side == 'SELL' and f.shares > 0 for f in fills)
    _decide_and_submit(allocator, account, dates[2], panel,
                       _entry_deteriorated(leaders), roles, risk=frozen)
    epoch = account.strategic_epochs[0]
    assert epoch.terminal and epoch.first_fill_session
    assert not account.positions
    _submit(account, (_ordinary_target(OWNER),), dates[3], panel)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[4], account=account, panel=panel)
    assert fills and all(f.side == 'BUY' and not f.grant_id and not f.epoch_id for f in fills)
    assert account.positions[OWNER].shares > 0 and not account.positions[OWNER].grant_id
    assert not _first_deployment_has_capital_in_use(account)


def test_native_zero_fill_expired_epoch_does_not_exempt_ordinary_inventory(tmp_path):
    engine, account, panel = _native_fixture(tmp_path)
    _native_day(engine, account, panel, '2023-01-03')
    _native_day(engine, account, panel, '2023-01-04')
    grant = account.strategic_grant
    _, fills = _native_day(engine, account, panel, '2023-01-05', removed=True)
    assert not fills and grant.status == 'EXPIRED' and grant.filled_shares == 0
    assert all(o.status == 'CANCELLED' for o in account.order_ledger)
    _settle_terminal_strategic_grant(account=account, date=pd.Timestamp('2023-01-06'))
    assert account.strategic_epochs[0].terminal
    assert not account.fills
    assert not _first_deployment_has_capital_in_use(account)
    _register(account, (_ordinary_target(OWNER),), pd.Timestamp('2023-01-06'), panel)
    fills = engine.execution.execute_open(date=pd.Timestamp('2023-01-09'), account=account, panel=panel)
    assert fills and all(f.side == 'BUY' and f.shares > 0 and not f.grant_id for f in fills)
    assert _first_deployment_has_capital_in_use(account)
