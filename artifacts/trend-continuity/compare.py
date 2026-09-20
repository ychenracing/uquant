"""Small task-local paired report; original replay runner owns raw validation."""
import argparse
import gzip
import hashlib
import json
import math
import statistics
from pathlib import Path


def read(path):
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert data['completed'] is True and not data.get('error')
    return data


def metrics(data):
    r = data['result']
    equities = {p['date']: p['equity'] for p in r['equity_curve']}
    fills = r['final_account']['fills']
    turnover = sum(f['gross_value'] / equities[f['fill_date']] for f in fills)
    costs = sum(sum(f[k] for k in ('commission', 'stamp_duty', 'transfer_fee', 'slippage_cost'))
                / equities[f['fill_date']] for f in fills)
    spans, opened, gross = [], {}, []
    for i, row in enumerate(r['daily_replay_evidence']):
        held = {s for s, shares in row['position_shares'].items() if shares > 0}
        for s in held - opened.keys():
            opened[s] = i
        spans.extend(i - opened.pop(s) for s in opened.keys() - held)
        gross.append(1 - row['cash'] / equities[row['date']])
    spans.extend(len(r['daily_replay_evidence']) - start for start in opened.values())
    return dict(wealth=r['final_wealth'], drawdown=r['max_drawdown'],
                orders=len({o['order_id'] for o in r['order_ledger']}),
                operation_days=len({f['fill_date'] for f in fills}),
                turnover=turnover, cost_burden=costs,
                fees=r['fees'], slippage=r['slippage_cost'],
                mean_gross=statistics.mean(gross),
                median_continuous_sessions=statistics.median(spans) if spans else 0,
                holding_episodes=len(spans), open_episodes=len(opened))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for cp in sorted(args.candidate.glob('*.json.gz')):
        bp = args.baseline / cp.name
        if not bp.exists():
            continue
        b, c = read(bp), read(cp)
        for key in ('symbols', 'start', 'end', 'config', 'runtime', 'runner_sha256', 'contract_sha256'):
            assert b[key] == c[key], key
        assert {k:v for k,v in b['inputs'].items() if k.startswith('data/')} == {
            k:v for k,v in c['inputs'].items() if k.startswith('data/')}
        bm, cm = metrics(b), metrics(c)
        failures = []
        gates = dict(wealth=cm['wealth']/bm['wealth'] >= .90,
                     drawdown=cm['drawdown'] <= .30 and cm['drawdown']-bm['drawdown'] <= .01,
                     orders=cm['orders'] <= max(bm['orders']+2, bm['orders']*1.10),
                     operation_days=cm['operation_days'] <= bm['operation_days']+2,
                     turnover=cm['turnover'] <= bm['turnover']*1.10,
                     cost_burden=cm['cost_burden'] <= bm['cost_burden']*1.10)
        failures = [k for k,v in gates.items() if not v]
        rows.append(dict(case=b['case'], baseline=bm, candidate=cm,
                         wealth_ratio=cm['wealth']/bm['wealth'],
                         drawdown_delta=cm['drawdown']-bm['drawdown'], failures=failures,
                         baseline_source=b['source_head'], candidate_source=c['source_head'],
                         raw_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (bp,cp)}))
    regular = [r for r in rows if r['case'] != 'full-cost2']
    report = dict(rows=rows, paired_cases=len(rows),
                  geometric_wealth_ratio=math.exp(statistics.mean(math.log(r['wealth_ratio']) for r in regular))
                  if regular else None,
                  individually_improved=sum(r['wealth_ratio'] >= 1.01 for r in regular),
                  note='Partial comparison is not final acceptance; seven cases and mechanism/correctness gates required.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}))
    for r in rows:
        print(r['case'], 'wealth', r['wealth_ratio'], 'DD delta', r['drawdown_delta'],
              'failures', r['failures'], 'holding', r['baseline']['median_continuous_sessions'],
              r['candidate']['median_continuous_sessions'])


if __name__ == '__main__':
    main()
