"""Pure orchestration and rejection checks; fixtures are not economic evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.cross_ai_acceptance import CONTRACT_PATH, config_payload_sha256
from research.cross_ai_robustness import (
    _contract,
    effective_config,
    evaluate_robustness,
    largest_positive_contributor,
    make_plan,
    metric_failures,
    run_shard,
    scenario_specs,
    validate_plan,
)
from research.cross_ai_strategy import case_symbols, run_production_case
from uquant.config import DEFAULT_CONFIG, config_fingerprint


def test_specs_cover_each_frozen_pair_and_never_cross_initial_factors() -> None:
    contract = json.loads(CONTRACT_PATH.read_text())
    specs = scenario_specs(contract, DEFAULT_CONFIG.to_dict())
    assert len(specs) == 64 and len({s['id'] for s in specs}) == 64
    initial = [s for s in specs if s['group'] == 'paired_initial_conditions']
    assert len(initial) == 24
    assert all(not (s['offset'] and 'cash_multiplier' in s) for s in initial)
    assert all(any(pair['id'] == s['pair'] and pair['source_role'] == 'old' for pair in specs)
               for s in specs if s['source_role'] == 'new' and 'pair' in s)
    neighbors = [s for s in specs if s['group'] == 'parameter_neighbors']
    assert len(neighbors) == 30 and all(len(s['overrides']) == 1 for s in neighbors)


def test_role_exclusions_preserve_indexes_and_validate_before_output(tmp_path: Path) -> None:
    for case in ('champion', 'remove_all_three', 'no_optical'):
        roles = case_symbols(case, '2023-01-03', extra_excluded_symbols=('sh688008',))
        assert all('sh688008' not in roles[key] for key in ('tradable', 'qualification', 'risk'))
        assert roles['indexes'] == ('sh000300', 'sh000682')
    for options in ({'extra_excluded_symbols': ('sh000300',)}, {'start_session_offset': -1},
                    {'initial_cash': float('nan')}):
        with pytest.raises(ValueError):
            run_production_case(case_id='champion', start='2023-01-03', end='2023-01-06',
                                output_dir=tmp_path / 'invalid', **options)
        assert not (tmp_path / 'invalid').exists()


def test_plan_missing_shards_and_omitted_specs_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin a fixture old-config identity so this pure test remains valid after deliberate config deletion.
    import research.cross_ai_robustness as module
    contract, old = _contract()
    old = {**old, 'config_sha256': config_fingerprint(DEFAULT_CONFIG)}
    monkeypatch.setattr(module, '_contract', lambda: (contract, old))
    plan = make_plan(candidate_source='a' * 64)
    result = evaluate_robustness(tmp_path, plan)
    assert result['status'] == 'FAIL' and len(result['rows']) == 64
    assert all(row['status'] == 'FAIL' for row in result['rows'])
    assert 'complete_L4' in result['remaining_final_gates'] and not result['authoritative_promotion']
    missing = copy.deepcopy(plan)
    missing['specs'].pop()
    with pytest.raises(ValueError, match='frozen'):
        validate_plan(missing)
    cash_spec = next(s for s in plan['specs'] if s['id'] == 'new-cash0.5-champion')
    assert effective_config(plan, cash_spec)['initial_cash'] == DEFAULT_CONFIG.initial_cash / 2
    assert config_payload_sha256(DEFAULT_CONFIG.to_dict()) == config_fingerprint(DEFAULT_CONFIG)
    first = run_shard(tmp_path, plan, 'new-nominal-champion')
    second = run_shard(tmp_path, plan, 'new-nominal-champion')
    assert first['status'] == second['status'] == 'FAIL'
    assert 'matching source/config checkout' in first['error']
    assert (tmp_path / 'new-nominal-champion.checkpoint.json').exists()
    assert (tmp_path / 'new-nominal-champion.checkpoint-1.json').exists()
    assert not (tmp_path / 'new-nominal-champion').exists()


def test_contributor_is_positive_and_ties_use_symbol_order() -> None:
    assert largest_positive_contributor({'verified_symbol_pnl': {'sz300502': 10.0, 'sz300308': 10.0}}) == 'sz300308'
    with pytest.raises(ValueError, match='no positive'):
        largest_positive_contributor({'verified_symbol_pnl': {'sz300308': -1.0}})
    with pytest.raises(ValueError, match='numeric metric'):
        largest_positive_contributor({'verified_symbol_pnl': {'sz300308': float('nan')}})


def test_cost_neighbor_and_old_pair_require_real_comparison_fields() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    metrics = {'final_wealth': 1.4, 'max_drawdown': 0.1, 'account_orders': 3,
               'annual_turnover': 1.0, 'fees': 100.0, 'slippage_cost': 100.0}
    spec = {'group': 'cost_stress', 'case': 'no_optical'}
    assert 'cost wealth retention' in metric_failures(spec, metrics, {'final_wealth': 2.0}, None, t, 2_000_000)
    spec['group'] = 'paired_initial_conditions'
    with pytest.raises(ValueError, match='old-source'):
        metric_failures(spec, metrics, {}, None, t, 2_000_000)
    assert not metric_failures(spec, metrics, {}, {'final_wealth': 1.4}, t, 2_000_000)
