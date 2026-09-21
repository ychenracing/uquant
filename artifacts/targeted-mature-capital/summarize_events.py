"""Retrospective diagnostics; no observations here enter production decisions."""
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
summaries = []
for case in ('loo308', 'no_three'):
    with gzip.open(HERE / 'raw' / f'{case}.json.events.json.gz', 'rt') as stream:
        events = json.load(stream)
    with gzip.open(HERE / 'raw' / f'{case}.json.gz', 'rt') as stream:
        replay = json.load(stream)
    curve = replay['result']['equity_curve']
    equity = {r['date']: r['equity'] for r in curve}
    for row in events['rows']:
        row['baseline_portfolio_remaining_net_pnl'] = curve[-1]['equity'] - equity[row['date']]
        row['baseline_portfolio_remaining_return'] = curve[-1]['equity'] / equity[row['date']] - 1
    blocked = [r for r in events['rows'] if r['open'] and r['mature'] and not r['owned']
               and not r['anchor'] and r['cooldown'] and r['increment'] > 0
               and r['proposed'] >= r['current'] and r['funded'] >= .01 and not r['eligible']]
    summaries.append(dict(case=case, observed_holding_events=len(events['rows']),
        candidate_blocked_events=len(blocked), blocked_sessions=sorted({r['date'] for r in blocked}),
        baseline_wealth=replay['result']['final_wealth'], baseline_drawdown=replay['result']['max_drawdown'],
        normalization_candidates=events['normalized_capture_candidates'],
        blocked=[{k:r[k] for k in ('date','symbol','funded','score','entry','cash','capital')}
                 for r in blocked]))
    (HERE / f'{case}-event-table.json.gz').write_bytes(gzip.compress(json.dumps(events).encode(), mtime=0))
(HERE / 'EVENT_SUMMARY.json').write_text(json.dumps(dict(cases=summaries,
    evidence_scope='All native holding events at the existing add stage, including blocked and accepted requests. '
                   'Remaining portfolio PNL is baseline observation, not incremental causal profit. '
                   'Only full independent candidate replay can measure avoided losses and net benefit.',
    economic_candidate='Current confirmed mature holdings use existing own-stock market checks; no other gate changes.'), indent=2))
