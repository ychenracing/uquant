"""Apply this task's registered economic gates to native paired originals."""
import argparse
import hashlib
import importlib.util
import json
import math
import statistics
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'existing_metrics', Path(__file__).parents[1] / 'trend-continuity/compare.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', type=Path, required=True)
parser.add_argument('--candidate', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
rows = []
for case in ('2024', 'full', 'loo308', 'no_three', '2025', 'loo502', '2024_cost2', 'full_cost2'):
    paths = [root / f'{case}.json.gz' for root in (args.baseline, args.candidate)]
    if not all(path.exists() for path in paths):
        continue
    b, c = (metrics.read(path) for path in paths)
    for key in ('symbols', 'start', 'end', 'config', 'runtime', 'runner_sha256', 'contract_sha256'):
        assert b[key] == c[key], (case, key)
    assert {k:v for k,v in b['inputs'].items() if k.startswith('data/')} == {
        k:v for k,v in c['inputs'].items() if k.startswith('data/')}
    bm, cm = metrics.metrics(b), metrics.metrics(c)
    wealth_ratio = cm['wealth'] / bm['wealth']
    gates = dict(wealth=wealth_ratio >= .95,
        risk_absolute=cm['drawdown'] <= .30,
        risk_increment=cm['drawdown'] - bm['drawdown'] <= .01,
        operation_days=cm['operation_days'] <= math.ceil(max(bm['operation_days']+3, 1.15*bm['operation_days'])),
        turnover=cm['turnover'] <= 1.15*bm['turnover'],
        cost=cm['cost_burden'] <= 1.15*bm['cost_burden'])
    rows.append(dict(case=case, baseline=bm, candidate=cm, wealth_ratio=wealth_ratio,
        gates=gates, failures=[k for k,v in gates.items() if not v],
        baseline_source=b['source_head'], candidate_source=c['source_head'],
        originals=[dict(path=str(p), bytes=p.stat().st_size,
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]))
regular = [r for r in rows if 'cost2' not in r['case']]
weak = [r for r in rows if r['case'] in ('loo308', 'loo502', 'no_three')]
def gm(items):
    return math.exp(statistics.mean(math.log(r['wealth_ratio']) for r in items)) if items else None


report = dict(rows=rows, standard_count=len(regular), standard_geomean=gm(regular),
    weak_count=len(weak), weak_geomean=gm(weak),
    weak_positive=sum(r['wealth_ratio'] > 1 for r in weak),
    six_standard_pass=len(regular) == 6 and gm(regular) >= 1.02
        and len(weak) == 3 and gm(weak) >= 1.05
        and sum(r['wealth_ratio'] > 1 for r in weak) >= 2
        and all(not r['failures'] for r in regular),
    note='Economic promotion also requires both cost stresses and the fixed real old account. '
         'Partial comparisons never imply full acceptance.')
args.output.write_text(json.dumps(report, indent=2))
print(json.dumps({k:v for k,v in report.items() if k != 'rows'}))
for row in rows:
    print(row['case'], row['wealth_ratio'], row['failures'])
