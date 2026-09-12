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
    _, account, date, panel, leaders, certificates, _ = _confirmed_entry()
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
