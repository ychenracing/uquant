from dataclasses import fields

import pytest

from uquant.config import DEFAULT_CONFIG, SystemConfig


@pytest.mark.parametrize('changes', [
    {'max_gross': .8}, {'max_gross': .5}, {'max_gross': .001},
    {'max_symbol_weight': .5}, {'max_symbol_weight': .3}, {'max_symbol_weight': .001},
    {'max_positions': 2}, {'max_positions': 1},
    {'max_gross': .5, 'max_symbol_weight': .3, 'max_positions': 1},
])
def test_lower_public_limits_keep_fixed_qualification_and_exit_rules(changes):
    cfg = SystemConfig(**changes)
    public = {field.name for field in fields(cfg)}
    assert {k: v for k, v in cfg.to_dict().items() if k not in public} == {
        k: v for k, v in DEFAULT_CONFIG.to_dict().items() if k not in public
    }
    assert cfg.strategic_cohort_size == 3
    assert cfg.strategic_cohort_confirm_days == DEFAULT_CONFIG.strategic_cohort_confirm_days


def test_strategic_cap_respects_total_limit_without_losing_default_exception():
    from uquant.portfolio_core import symbol_weight_cap
    from uquant.types import AccountState
    account = AccountState.empty(2_000_000)
    assert symbol_weight_cap(SystemConfig(max_gross=.2, max_symbol_weight=.3), account, 'sz300308') == .2


@pytest.mark.parametrize('changes', [
    {'max_gross': .8}, {'max_gross': .5}, {'max_gross': .001},
    {'max_symbol_weight': .5}, {'max_symbol_weight': .3}, {'max_symbol_weight': .001},
    {'max_positions': 2}, {'max_positions': 1},
    {'max_gross': .5, 'max_symbol_weight': .3, 'max_positions': 1},
])
def test_native_allocation_honors_limits_without_shortening_qualification(changes):
    from test_unified_core_book import _inputs

    from uquant.portfolio import PortfolioAllocator
    from uquant.types import AccountState, Opportunity
    cfg = SystemConfig(**changes)
    _, panel, leaders, risk = _inputs()
    dates = next(iter(panel.values())).index
    account = AccountState.empty(cfg.initial_cash)
    policy = PortfolioAllocator(cfg)
    for date in dates[-8:]:
        targets = policy.allocate(date=date, opportunity=Opportunity.TREND, risk=risk,
            user_panel=panel, leaders=leaders, account=account,
            prices={s: float(f.loc[date, 'close']) for s, f in panel.items()})
        assert sum(t.weight for t in targets) <= cfg.max_gross + 1e-12
        assert sum(t.weight > 0 for t in targets) <= cfg.max_positions
        assert all(t.weight <= cfg.max_symbol_weight + 1e-12 for t in targets)
    assert cfg.strategic_cohort_size == DEFAULT_CONFIG.strategic_cohort_size


def test_capacity_blocks_full_deployment_without_changing_observed_qualification():
    from copy import deepcopy

    from test_strategic_common_core_admission import _confirmed_entry, _qualified, _risk

    from uquant.portfolio import PortfolioAllocator
    from uquant.portfolio.strategic.ownership import activate_strategic_cohort
    _, account, date, panel, leaders, _certificates, _ = _confirmed_entry()
    before = deepcopy(account.strategic_qualification)
    qualified = _qualified(account, panel)
    activate_strategic_cohort(PortfolioAllocator(SystemConfig(max_positions=1)), qualified=qualified,
        entry_eligibility={s: {'block': 'READY'} for s in panel}, snapshots={}, leaders=leaders, account=account,
        date=date, risk=_risk(frozen=False), user_panel=panel)
    assert account.strategic_grant is None
    assert account.strategic_qualification.qualification_streak == before.qualification_streak
    assert account.strategic_qualification.qualification_ready == before.qualification_ready
    assert account.strategic_qualification.deployment_block_reason == 'POSITION_COUNT_LIMIT'


@pytest.mark.parametrize('document', ['[]', '{"max_gross": .5}', '{"max_gross": 0.5, "max_gross": 0.4}',
    '{"max_positions": true}', '{"max_positions": 1.0}', '{"max_gross": NaN}',
    '{"max_gross": 1e999}', '{"strategic_cohort_size": 1}', '{"unknown": 1}',
    '{"risk_sentinel_mode": "SHADOW"}'])
def test_strict_public_json_rejects_bad_inputs(tmp_path, document):
    from uquant.config.input import load_public_config
    path = tmp_path / 'config.json'
    path.write_text(document)
    with pytest.raises((ValueError, TypeError)):
        load_public_config(path)


def test_public_json_uses_defaults_for_omitted_fields(tmp_path):
    from uquant.config.input import load_public_config
    path = tmp_path / 'config.json'
    path.write_text('{"max_gross": 0.5, "max_symbol_weight": 0.3, "max_positions": 1}')
    assert load_public_config(path) == SystemConfig(max_gross=.5, max_symbol_weight=.3, max_positions=1)
    assert load_public_config(None) is DEFAULT_CONFIG


@pytest.mark.parametrize('capacity', [1, 2])
def test_recovery_capacity_does_not_replace_member_confirmation(capacity):
    from test_unified_core_book import _inputs

    from uquant.portfolio import PortfolioAllocator
    from uquant.portfolio.recovery.cohort_admission import _await_recovery_confirmation
    from uquant.types import AccountState
    _, _, leaders, risk = _inputs()
    cfg = SystemConfig(max_positions=capacity)
    account = AccountState.empty(cfg.initial_cash)
    members = set(list(leaders)[:capacity])
    for day in range(1, cfg.recovery_member_confirm_days + 1):
        result = _await_recovery_confirmation(PortfolioAllocator(cfg), risk=risk, leaders=leaders,
            account=account, anchored_held={}, previous_members=set(), candidate_members=members,
            crash_depth={}, deep_count=0, admission_depth=-.35, freeze_active=False)
        assert (result is None) == (day == cfg.recovery_member_confirm_days)
    assert account.replacement_tenure['recovery_admission:' + ','.join(sorted(members))] == cfg.recovery_member_confirm_days


def test_explicit_cash_cannot_hide_invalid_json_cash(tmp_path):
    from uquant.config.input import load_public_config
    path = tmp_path / 'config.json'
    path.write_text('{"initial_cash": true}')
    with pytest.raises((ValueError, TypeError)):
        load_public_config(path, initial_cash=1)
    path.write_text('{"initial_cash": 100000}')
    with pytest.raises(ValueError, match='conflicts'):
        load_public_config(path, initial_cash=200000)


@pytest.mark.parametrize('cap', [.8, .5, .001])
def test_open_gap_and_fees_cannot_exceed_lower_gross(cap):
    import pandas as pd
    from test_execution import _canonical_pending, _frame

    from uquant.execution import ExecutionPlanner
    from uquant.types import AccountState
    symbol = 'sh603986'
    panel = {symbol: _frame([
        {'date': '2026-01-05', 'open': 10, 'high': 10.5, 'low': 9.5, 'close': 10, 'volume': 1e8, 'amount': 1e9},
        {'date': '2026-01-06', 'open': 15, 'high': 15.5, 'low': 10.5, 'close': 14.5, 'volume': 1e8, 'amount': 1.45e9},
    ])}
    account = AccountState.empty(2e6)
    account.pending_orders = [_canonical_pending('2026-01-05', symbol, 'BUY', .6, 'entry')]
    fills = ExecutionPlanner(SystemConfig(max_gross=cap)).execute_open(
        date=pd.Timestamp('2026-01-06'), account=account, panel=panel)
    value = sum(p.shares * fills[0].price for p in account.positions.values()) if fills else 0
    assert value / (account.cash + value) <= cap + 1e-12
    assert account.cash >= 0


def test_lower_capacity_keeps_real_inventory_until_execution_settles():
    from copy import deepcopy

    from uquant.portfolio_core import PortfolioCore
    from uquant.types import AccountState, AttributionMechanism, Lifecycle, OriginSubsystem, Position
    account = AccountState.empty(2e6)
    account.positions = {s: Position(s, shares=1000, avg_cost=20.) for s in ['sz300308', 'sz300502']}
    before = deepcopy(account)
    targets = PortfolioCore(SystemConfig(max_positions=1))._targets(
        proposed={s: .2 for s in account.positions}, leaders={}, account=account,
        lifecycle=Lifecycle.CORE, reason='existing core', origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_SELECTION)
    assert {t.symbol for t in targets if t.weight > 0} == set(account.positions)
    assert account == before


def test_tiny_lower_cap_does_not_relax_order_planning_minimum():
    from uquant.execution.order_planning import plan_orders
    from uquant.types import AccountState, Target
    cfg = SystemConfig(max_gross=.001, max_symbol_weight=.001)
    account = AccountState.empty(cfg.initial_cash)
    diagnostics = {}
    orders = plan_orders(signal_date='2026-01-05', targets=(Target('sz300308', .001, 'CORE', .9, 1., 'entry'),),
        account=account, prices={'sz300308': 10.}, cfg=cfg, diagnostics=diagnostics)
    assert not orders
    assert cfg.min_trade_value == DEFAULT_CONFIG.min_trade_value
    assert diagnostics['sz300308']['block'] == 'NO_TRADE_BAND'
    assert diagnostics['sz300308']['difference_value'] < diagnostics['sz300308']['standard_trade_threshold']
    assert diagnostics['sz300308']['standard_trade_threshold'] >= cfg.min_trade_value
