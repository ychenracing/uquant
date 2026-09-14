"""Verify the read-only probe and derive its bounded feasibility findings."""
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent


def read(name):
    return json.loads(gzip.decompress((root / (name + '.json.gz')).read_bytes()))


a = read('S-a-bull-trace')
e = read('S-e-bull-trace')
h2 = read('S-e-h2-acute-prefix')
probe = read('S-e-joint-probe')
assert probe['trace'] == e['trace'], 'Observer changed a production decision or account'
assert probe['metrics'] == e['metrics']
assert probe['observer_sha256'] == hashlib.sha256((root / 'probe_joint_allocation.py').read_bytes()).hexdigest()
for trace in (a, e, h2, probe):
    assert trace['source']['commit'] == 'bbaf58d3c403f79bd96f20bddc1ff849d5c7f960'
    assert trace['source'] == a['source']
    assert trace['source']['patch_sha256'] is None
for name, trace in [('a', a), ('e', e)]:
    prior = json.loads((root / f'prior-S-{name}-bull_crash_2025_2026.json').read_text())
    for key in ('final_wealth', 'max_drawdown', 'account_orders'):
        assert abs(trace['metrics'][key] - prior['metrics'][key]) < 1e-10, key

by_date = defaultdict(list)
edge_rows = []
for row in probe['allocation_observations']:
    if row['edge'] >= row['required_edge'] and row['broken'] and not row['protected']:
        edge_rows.append(row)
        by_date[row['date']].append(row)
streak = {}
longest = 0
for day in e['trace']:
    current = {}
    for row in by_date[day['date']]:
        key = (row['incumbent'], row['challenger'])
        current[key] = streak.get(key, 0) + 1
        longest = max(longest, current[key])
    streak = current

# Deliberately optimistic: every READY fresh name, even before slot/funding checks.
a_pairs = []
for day in a['trace']:
    if day['risk'] != 'NORMAL' or day['opportunity'] not in ('TREND', 'STRONG_TREND', 'RECOVERY'):
        continue
    symbols = day['risk_evidence'].get('core_allocation', {}).get('symbols', {})
    fresh = [s for s, row in symbols.items() if row.get('entry', {}).get('block') == 'READY'
             and row.get('held_weight', 0) == 0]
    held = [s for s, row in symbols.items() if row.get('held_weight', 0) > 0
            and s not in day['strategic_targets']]
    a_pairs.extend((day['date'], incumbent, challenger) for incumbent in held for challenger in fresh)

h2_ready = []
for day in h2['trace']:
    symbols = day['risk_evidence'].get('core_allocation', {}).get('symbols', {})
    for symbol, row in symbols.items():
        if row.get('entry', {}).get('block') == 'READY':
            h2_ready.append(dict(date=day['date'], symbol=symbol, risk=day['risk'],
                opportunity=day['opportunity'], held_weight=row.get('held_weight', 0),
                entry_gate=row.get('entry_gate'), allocation_reason=row.get('allocation_reason')))
assert not [row for row in h2_ready if row['date'] > '2024-08-01']
assert not a_pairs
assert len(probe['allocation_observations']) == 179
assert len(edge_rows) == 4 and longest == 2
assert max(row['feasible'] for row in edge_rows) < 0.20
summary = dict(
    source=a['source'], environment=a['environment'],
    observer_matches_full_original_trace=True,
    a_optimistic_fresh_incumbent_pairs=len(a_pairs),
    e_observed_pairs=len(probe['allocation_observations']),
    e_single_day_edge_and_deterioration_pairs=len(edge_rows),
    e_longest_confirmation_streak=longest,
    e_required_confirmation_streak=3,
    e_pre_damage_max_edge=max(row['edge'] for row in probe['allocation_observations']
                             if row['date'] < '2025-02-28'),
    e_edge_rows=edge_rows,
    h2_ready_observations=h2_ready,
    findings_scope='Original qualification, score, winner protection and transfer confirmation; not all possible designs',
    acceptance='FAIL; no production candidate created; diagnostic evidence is not canonical acceptance',
)
(root / 'verified-findings.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ('e_edge_rows', 'h2_ready_observations')}, indent=2))
