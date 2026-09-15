"""Verify fixed replay comparisons and retain their complete differences."""
import gzip
import hashlib
import json
from pathlib import Path

root = Path(__file__).parent
report = {'status': 'DIAGNOSTIC_ONLY_NOT_JOINT_ACCEPTANCE', 'pools': {}}
for pool in ('a', 'e'):
    runs = {name: json.loads(gzip.decompress((root / f'{name}-{pool}-bull.json.gz').read_bytes()))
            for name in ('uquant-baseline', 'uquant-main', 'uquant')}
    old = runs['uquant-baseline']
    expected = {'a': 13.166460741078918, 'e': 14.994171560450225}[pool]
    assert old['metrics']['final_wealth'] == expected
    comparisons = {}
    for name, run in runs.items():
        assert run['completed']
        for field in ('symbols', 'interval', 'seed', 'environment', 'adapter_sha256', 'runner_sha256'):
            assert run[field] == old[field], (name, field)
        data = lambda d: {p: h for p, h in d['source_files'].items() if p.startswith('data/')}
        assert data(run) == data(old)
        common = old['config'].keys() & run['config'].keys()
        changed = {k: [old['config'][k], run['config'][k]] for k in common if old['config'][k] != run['config'][k]}
        assert not changed, changed
        pairs = list(zip(old['trace'], run['trace'], strict=True))
        assert all(x['date'] == y['date'] for x, y in pairs)
        events = []
        for x, y in pairs:
            if x['new_fills'] or y['new_fills']:
                events.append({'date': x['date'], 'historical_fills': x['new_fills'], 'compared_fills': y['new_fills'],
                               'historical_equity': x['equity'], 'compared_equity': y['equity']})
        comparisons[name] = {
            'commit': run['commit'], 'metrics': run['metrics'],
            'wealth_retention': run['metrics']['final_wealth'] / old['metrics']['final_wealth'],
            'same_leader_sessions': sum(x['leaders'] == y['leaders'] for x, y in pairs),
            'sessions': len(pairs), 'removed_settings': sorted(old['config'].keys() - run['config'].keys()),
            'added_settings': sorted(run['config'].keys() - old['config'].keys()),
            'changed_shared_settings': changed,
            'trade_events': events,
            'daily_wealth_difference': [{'date': x['date'], 'difference': (y['equity'] - x['equity']) / old['config']['initial_cash']}
                                       for x, y in pairs],
        }
    report['pools'][pool] = comparisons
path = root / 'comparison.json'
path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())
            if p.is_file() and p.name != 'SHA256SUMS.json'}
(root / 'SHA256SUMS.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
print(json.dumps({pool: {name: {'wealth': c['metrics']['final_wealth'], 'retention': c['wealth_retention'],
                              'same_leader_sessions': c['same_leader_sessions']}
                       for name, c in runs.items()} for pool, runs in report['pools'].items()}))
