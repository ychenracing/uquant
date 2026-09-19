"""Release-screen semantics; these fixtures are not economic evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'startup_screen', ROOT / 'artifacts/branch-integration/evaluate_robustness.py')
screen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(screen)


@pytest.fixture
def matrix(tmp_path, monkeypatch):
    contract = json.loads((screen.HERE / 'START_DATE_CONTRACT.json').read_text())
    scenario = json.loads((screen.HERE.parent / 'alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())
    starts = {screen.case_name(year, offset): date
              for year, cluster in contract['clusters'].items() for offset, date in cluster.items()}
    for name in contract['strong_cases'] + contract['weak_cases'] + ['full-offset5-cost2']:
        starts.setdefault(name, starts['full-offset5'] if name == 'full-offset5-cost2' else starts['full'])
    runs, baseline = tmp_path / 'runs', tmp_path / 'baseline'
    runs.mkdir()
    baseline.mkdir()
    data = {}
    for name, start in starts.items():
        symbols = scenario['universe']
        if name.startswith('loo-'):
            symbols = [s for s in symbols if s != name.removeprefix('loo-')]
        elif name == 'remove_all_three':
            symbols = [s for s in symbols if s not in {'sz300308', 'sz300394', 'sz300502'}]
        elif name == 'd-continuous_ai_era':
            symbols = scenario['pools']['d']
        common = max(contract['clusters'][start[:4]].values())
        result = {'final_wealth': 2., 'max_drawdown': .29, 'final_equity': 2.,
                  'equity_curve': [{'date': common, 'equity': 1.}, {'date': contract['end'], 'equity': 2.}],
                  'order_ledger': [], 'daily_replay_evidence': [{'date': common, 'cash': 1.,
                                                             'position_shares': {}, 'close_marks': {}}],
                  'final_account': {'fills': [], 'order_ledger': [], 'cash': 2.}, 'gross_turnover': 0.,
                  'fees': 0., 'slippage_cost': 0., 'peak_to_recovery_days': 0}
        row = {'case': name, 'start': start, 'end': contract['end'], 'symbols': symbols,
               'cost_multiplier': 2 if name.endswith('cost2') else 1,
               'source_head': 'fixture-candidate', 'source_tree': 'fixture-tree',
               'config': {}, 'runtime': {}, 'inputs': {}, 'runner_sha256': 'fixture',
               'contract_sha256': 'fixture', 'sessions': 2, 'result': result}
        (runs / (name + '.json.gz')).touch()
        data[runs / (name + '.json.gz')] = row
        left = {**row, 'source_head': 'fixture-baseline',
                'source_tree': '120fa8a2d2914b9185239a0918a84a8838aeed77',
                'result': {**result, 'final_wealth': 1.5, 'max_drawdown': .10}}
        data[baseline / (name + '.json.gz')] = left
    monkeypatch.setattr(screen.compare, 'read', lambda path, repo: data[path])
    return runs, baseline, data


def test_revised_absolute_budget_replaces_old_increment_limit(matrix):
    runs, baseline, _ = matrix
    result = screen.evaluate(runs, baseline, ROOT)
    assert result['status'] == 'ECONOMIC_MATRIX_MET'
    assert set(result['clusters']) == {'2023', '2024', '2025'}
    assert all(c['R_common'] == 1. for c in result['clusters'].values())
    assert 'no_optical' not in result['cells']


def test_adjacent_start_failure_cannot_hide_behind_offset5_success(matrix):
    runs, baseline, data = matrix
    data[runs / 'full-offset6.json.gz']['result']['final_equity'] = 1.
    result = screen.evaluate(runs, baseline, ROOT)
    assert result['status'] == 'NOT_MET'
    assert '2023: common growth retention' in result['failures']


def test_missing_temporal_confirmation_is_not_pass(matrix):
    runs, baseline, _ = matrix
    (runs / '2025-offset10.json.gz').unlink()
    result = screen.evaluate(runs, baseline, ROOT)
    assert result['status'] == 'INCOMPLETE'
    assert result['NOT_RUN'] == ['2025-offset10']
    assert result['clusters']['2025']['R_common'] is None
