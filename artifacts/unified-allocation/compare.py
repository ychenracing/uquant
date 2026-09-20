"""Apply the preregistered routes and reconcile whole-account contribution deltas."""
import argparse
import gzip
import importlib.util
import json
import math
import statistics
from pathlib import Path

spec = importlib.util.spec_from_file_location('prior_compare', Path(__file__).parents[1] / 'trend-continuity/compare.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
p = argparse.ArgumentParser()
p.add_argument('--baseline', type=Path, required=True)
p.add_argument('--candidate', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
contract = json.loads(Path(__file__).with_name('ACCEPTANCE.json').read_text())
rows = []
for cp in sorted(a.candidate.glob('*.json.gz')):
    bp = a.baseline / cp.name
    if not bp.exists():
        continue
    b, c = prior.read(bp), prior.read(cp)
    for k in ('symbols', 'start', 'end', 'config', 'runtime', 'runner_sha256', 'contract_sha256'):
        assert b[k] == c[k], k
    assert {k:v for k,v in b['inputs'].items() if k.startswith('data/')} == {k:v for k,v in c['inputs'].items() if k.startswith('data/')}
    bm, cm = prior.metrics(b), prior.metrics(c)
    gates = dict(drawdown=cm['drawdown'] <= .30 and cm['drawdown']-bm['drawdown'] <= .01,
                 orders=cm['orders'] <= max(bm['orders']+2, bm['orders']*1.15),
                 operation_days=cm['operation_days'] <= max(bm['operation_days']+3,bm['operation_days']*1.15),
                 turnover=cm['turnover'] <= bm['turnover']*1.15,
                 cost=cm['cost_burden'] <= bm['cost_burden']*1.15)
    rows.append(dict(case=b['case'], baseline=bm, candidate=cm, wealth_ratio=cm['wealth']/bm['wealth'],
                     drawdown_delta=cm['drawdown']-bm['drawdown'], common_failures=[k for k,v in gates.items() if not v],
                     route_A_wealth=cm['wealth']/bm['wealth'] >= .95,
                     route_B_wealth=cm['wealth']/bm['wealth'] >= .90,
                     baseline_source=b['source_head'],candidate_source=c['source_head']))
regular = [r for r in rows if 'cost2' not in r['case']]
geometric = math.exp(statistics.mean(math.log(r['wealth_ratio']) for r in regular)) if regular else None
complete = {r['case'] for r in rows} == set(contract['cases'])
report = dict(rows=rows, paired_cases=len(rows), complete=complete, geometric_wealth_ratio=geometric,
              worst_wealth_ratio=min((r['wealth_ratio'] for r in rows), default=None),
              economic_route_A=complete and all(not r['common_failures'] and r['route_A_wealth'] for r in rows) and geometric >= .98,
              economic_route_B=complete and all(not r['common_failures'] and r['route_B_wealth'] for r in rows) and geometric >= 1.02 and sum(r['wealth_ratio'] >= 1.01 for r in regular) >= 2,
              note='Complexity and correctness are separate mandatory conditions. Partial results cannot pass.')
a.output.write_text(json.dumps(report, indent=2)+'\n')
for r in rows:
    print(r['case'], 'wealth ratio',r['wealth_ratio'],'DD delta',r['drawdown_delta'],'common failures',r['common_failures'])
print('pairs',len(rows),'geometric',geometric,'complete',complete)
