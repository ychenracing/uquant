"""The authorized judgment is distinct from unchanged historical evidence."""
import json

import pytest

from research.cross_ai_acceptance import CONTRACT_PATH, check_metrics, number
from research.cross_ai_robustness import metric_failures
from uquant.validation.acceptance_tolerance import acceptance_revision, order_ceiling, wealth_floor
from uquant.validation.promotion import AI_ERA_POLICY, _hard_violations


def test_wealth_not_profit_and_bounded_order_revision() -> None:
    assert wealth_floor(1.5) == 1.35
    assert wealth_floor(1.5, authorized=False) == 1.5
    assert [order_ceiling(n) for n in (12, 15, 20, 25)] == [12, 20, 32, 25]
    assert order_ceiling(15, authorized=False) == 15
    assert acceptance_revision()['authorized_after_observing_candidate'] is True


def test_authorized_32_orders_does_not_relax_wealth_risk_or_short_windows() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': 2.4485949990159668, 'max_drawdown': .15838911160983737,
               'account_orders': 32, 'annual_turnover': 1., 'fees': 1., 'slippage_cost': 1.}
    args = dict(case='remove_all_three', window='continuous_ai_era', metrics=metrics,
                baseline={'final_wealth': .9109325976972311},
                benchmark={'final_wealth': 1.}, thresholds=t)
    assert check_metrics(**args) == []
    assert 'removal order ceiling' in check_metrics(**args, authorized=False)
    metrics['account_orders'] = 33
    assert check_metrics(**args) == ['removal order ceiling']
    assert order_ceiling(12) == 12 and order_ceiling(25) == 25


def test_nominal_original_effective_and_unchanged_risk() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': wealth_floor(t['champion_minimum_final_wealth']),
               'max_drawdown': .30, 'account_orders': 20}
    options = dict(case='champion', window='continuous_ai_era', metrics=metrics,
                   baseline={}, benchmark={}, thresholds=t)
    assert check_metrics(**options) == []
    assert set(check_metrics(**options, authorized=False)) == {
        'champion/full wealth floor', 'champion/full order ceiling'}
    metrics['max_drawdown'] = .300001
    assert check_metrics(**options) == ['champion/full drawdown ceiling']
    metrics['max_drawdown'] = .30
    metrics['final_wealth'] -= 1e-8
    assert check_metrics(**options) == ['champion/full wealth floor']


def test_authorized_h1_drawdown_is_exact_and_case_scoped() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': 1.4476947005385299,
               'max_drawdown': .2442425185317515, 'account_orders': 6}
    args = dict(case='no_optical', window='h1_2023', metrics=metrics,
                baseline={'final_wealth': 1.01039464268425, 'max_drawdown': .18205429957130803},
                benchmark={'final_wealth': 1.}, thresholds=t)
    assert check_metrics(**args) == []
    assert check_metrics(**args, authorized=False) == ['half-year drawdown retention']
    assert check_metrics(**{**args, 'case': 'remove_all_three'}) == ['half-year drawdown retention']
    assert check_metrics(**{**args, 'window': 'h1_2024'}) == ['half-year drawdown retention']
    metrics['max_drawdown'] += 1e-12
    assert check_metrics(**args) == ['half-year drawdown retention']


def test_short_orders_and_improvement_delta_are_unchanged() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': 1.4, 'max_drawdown': .1, 'account_orders': 13,
               'annual_turnover': 0., 'fees': 0., 'slippage_cost': 0.}
    args = dict(case='remove_all_three', metrics=metrics,
                baseline={'final_wealth': 1.3, 'max_drawdown': .1},
                benchmark={'final_wealth': 1.}, thresholds=t)
    assert check_metrics(**args, window='h1_2024') == ['half-year order ceiling']
    assert 'removal wealth delta' in check_metrics(**args, window='continuous_ai_era')
    metrics['account_orders'] = 26
    assert 'later-window order ceiling' in check_metrics(**args, window='bull_crash_2025_2026')


def test_robustness_dual_boundaries_cost_and_risk() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': 20. * t['sensitivity_minimum_wealth_ratio'] * .9,
               'max_drawdown': .3, 'account_orders': 20}
    args = ({'group': 'parameter_neighbors', 'case': 'champion'}, metrics,
            {'final_wealth': 20.}, None, t, 2_000_000.)
    assert metric_failures(*args) == []
    assert set(metric_failures(*args, authorized=False)) == {
        'neighbor wealth retention', 'absolute order ceiling'}
    metrics['max_drawdown'] = .300001
    assert metric_failures(*args) == ['absolute drawdown ceiling']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), None, True, '1.5'])
def test_nonfinite_or_malformed_metrics_remain_invalid(value: object) -> None:
    with pytest.raises(ValueError):
        number({'final_wealth': value}, 'final_wealth')


@pytest.mark.parametrize('pool', list('abcde'))
def test_all_performance_continuous_pools_share_exact_revision(pool: str) -> None:
    gate = AI_ERA_POLICY['official']['continuous_ai_era']
    metrics = {'final_wealth': gate['min_final_wealth'] * .9,
               'max_drawdown': gate['max_drawdown'], 'account_orders': 20, 'acute_return': 0.}
    options = dict(name=f'{pool}/continuous_ai_era', metrics=metrics, gate=gate)
    assert _hard_violations(**options) == []
    assert len(_hard_violations(**options, authorized=False)) == 2
    metrics['max_drawdown'] += 1e-8
    assert 'max_drawdown' in _hard_violations(**options)[0]


def test_robustness_reads_once_reports_both_and_keeps_p10_floor(tmp_path, monkeypatch) -> None:
    import research.cross_ai_robustness as module

    contract = json.loads(CONTRACT_PATH.read_text())
    spec = {'id': 'new-nominal-champion', 'source_role': 'new',
            'group': 'nominal', 'case': 'champion'}
    plan = {'specs': [spec], 'contract_sha256': 'fixture', 'tail_population': ['fixture']}
    metrics = {'final_wealth': 2., 'max_drawdown': .1, 'account_orders': 20}
    reads = []
    monkeypatch.setattr(module, 'validate_plan', lambda _: None)
    monkeypatch.setattr(module, '_contract', lambda: (contract, {}))
    monkeypatch.setattr(module, 'effective_config', lambda *_: {'initial_cash': 2_000_000})

    def read(*_):
        reads.append(True)
        return {'metrics': metrics, 'canonical_sha256': 'fixture'}

    monkeypatch.setattr(module, 'read_shard', read)
    result = module.evaluate_robustness(tmp_path, plan)
    assert len(reads) == 1
    assert result['status'] == 'PASS' and result['original_status'] == 'FAIL'
    assert result['rows'][0]['original_failures'] == ['absolute order ceiling']
    metrics['final_wealth'] = .99999
    result = module.evaluate_robustness(tmp_path, plan)
    assert 'robustness tail: p10_wealth' in result['aggregate_failures']
    assert 'robustness tail: positive_fraction' in result['aggregate_failures']
