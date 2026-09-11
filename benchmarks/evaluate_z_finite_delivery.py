"""Read-only evaluation of the expressly authorized Z delivery scope.

Run from repository root: PYTHONPATH=. python benchmarks/evaluate_z_finite_delivery.py RUN_ROOT
Original comparator outputs are inputs and are never rewritten.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from research.cross_ai_robustness import _read_sealed
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.engine import code_fingerprint

root = Path(sys.argv[1]).resolve()
contract_path = Path('benchmarks/z_finite_delivery_contract_20260911.json')
contract = json.loads(contract_path.read_text())
checks = []
inputs = {}


def require(name, passed):
    checks.append({'check': name, 'passed': bool(passed)})


def read(name):
    path = root / name
    inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text())


def sealed_report(path):
    wrapper = _read_sealed(path)
    report = wrapper['report']
    # Symbol PnL is added by readback and protected by the outer wrapper seal.
    unsigned = {k: v for k, v in report.items() if k not in ('canonical_sha256', 'verified_symbol_pnl')}
    require('report seal: ' + path.name,
            hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() == report['canonical_sha256'])
    require('native integrity: ' + path.name,
            report['status'] == 'COMPLETE' and report['accounting']['reconciled']
            and report['sessions'] == report['expected_sessions']
            and not report['future_holdout_used']
            and report['identity']['source_sha256'] == contract['source_sha256'])
    inputs[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return report


require('Z production fingerprint', code_fingerprint() == contract['source_sha256'])
require('executable/input tree unchanged since verified delivery', subprocess.run(
    ['git', 'diff', '--exit-code', '9bad5dfb010aecec44fc11eb6263d76f8dfb1781', '--',
     'uquant', 'tests', 'research', 'scripts', 'pyproject.toml', 'uv.lock'], capture_output=True).returncode == 0)
nominal = read('nominal-final.json')
require('14 nominal accounts and cross-window', len(nominal['rows']) == 14
        and nominal['status'] == 'PASS' and not nominal['cross_window_failures'])
for row in nominal['rows']:
    if row['case'] in ('champion', 'full'):
        require('principal wealth15: ' + row['case'], row['metrics']['final_wealth'] >= 15)
for path in sorted(root.glob('*-readback.json')):
    sealed_report(path)
plan = _read_sealed(root / 'robustness-plan.json')
read('robustness-plan.json')
robust = _read_sealed(root / 'robustness-final.json')
read('robustness-final.json')
new = []
for spec, row in zip(plan['specs'], robust['rows'], strict=True):
    if spec['source_role'] != 'new' or row['status'] == 'CONTROL_DELETED':
        continue
    report = sealed_report(root / 'robustness' / (spec['id'] + '-readback.json'))
    new.append({'id': spec['id'], **{k: report['metrics'][k] for k in
                                   ('final_wealth', 'max_drawdown', 'account_orders')}})
    if spec['group'] != 'paired_initial_conditions':
        require('retained economic gate: ' + spec['id'], row['status'] in ('PASS', 'ACCEPTED_EXCEPTION'))
require('complete frozen new population', len(new) == 44)
metrics = {
    'positive_fraction': sum(x['final_wealth'] > 1 for x in new) / len(new),
    'p10_wealth': float(np.percentile([x['final_wealth'] for x in new], 10)),
    'p90_drawdown': float(np.percentile([x['max_drawdown'] for x in new], 90)),
    'p90_orders': float(np.percentile([x['account_orders'] for x in new], 90)),
}
limits = contract['hard_requirements']
for key in ('positive_fraction', 'p10_wealth'):
    require(key, metrics[key] >= limits['minimum_' + key])
for key in ('p90_drawdown', 'p90_orders'):
    require(key, metrics[key] <= limits['maximum_' + key])
performance = read('performance/full-performance.json')
all_metrics = [x['metrics'] for x in nominal['rows']] + new
all_metrics += list(performance['cells'].values()) + list(performance['protected'].values())
require('every observed account drawdown <=30%', all(x['max_drawdown'] <= .30 for x in all_metrics))
require('every observed account orders <=40', all(x['account_orders'] <= 40 for x in all_metrics))
cache_count = 0
for path in sorted((root / 'performance/native-cache').glob('*.json')):
    cache = json.loads(path.read_text())
    encoded = json.dumps(cache['payload'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    require('Performance cache seal: ' + cache['payload']['name'], hashlib.sha256(encoded).hexdigest() == cache['sha256'])
    cache_count += 1
require('45 native Performance cache units', cache_count == 45)
previous = read('z_remaining_acceptance_result.json')
require('Performance identity/schema validation', not previous['performance']['additional_identity_or_schema_failures'])
require('account copy migration preserved economics', previous['operator_migration']['status'] == 'PASS'
        and previous['operator_migration']['economic_fields_preserved'])
require('original failure retained', previous['overall_status'] == 'NOT_MET'
        and previous['performance']['unit_status_counts']['FAIL'] == 32
        and robust['status'] == 'FAIL')
result = {
    'contract_id': contract['contract_id'], 'contract_sha256': hashlib.sha256(contract_path.read_bytes()).hexdigest(),
    'source_sha256': code_fingerprint(),
    'status': 'PASS_WITH_DISCLOSED_LIMITATIONS' if all(x['passed'] for x in checks) else 'FAIL',
    'original_full_contract_status': 'NOT_MET', 'historical_only': True,
    'metrics': metrics, 'maximum_observed_drawdown': max(x['max_drawdown'] for x in all_metrics),
    'maximum_observed_orders': max(x['account_orders'] for x in all_metrics),
    'new_population': new, 'checks': checks, 'input_sha256': inputs,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(0 if all(x['passed'] for x in checks) else 1)
