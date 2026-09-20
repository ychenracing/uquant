"""Execute the two preregistered confirmation groups without outcome selection."""
import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'artifacts/branch-integration'))
from run_pairs import CONTRACT, run  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    contract = json.loads((ROOT / 'artifacts/branch-integration/CROSS_VINTAGE_CONTRACT.json').read_text())
    universe = json.loads(CONTRACT.read_text())['universe']
    baseline = ROOT.parent / 'uquant-baseline'
    assert subprocess.check_output(['git', '-C', str(baseline), 'rev-parse', 'HEAD'], text=True).strip() == contract['baseline']
    output = ROOT.parent / 'cross-vintage/confirmation' / args.label
    output.mkdir(parents=True, exist_ok=True)
    jobs = []
    for group in contract['confirmation']:
        symbols = universe if group['pool'] == 'full' else [s for s in universe if s != 'sz300308']
        for role in ('old', 'new'):
            name = group['id'] + '-' + role
            jobs.append(('native', ROOT, name, symbols, group[role + '_start']))
            jobs.append(('resume', baseline, name + '-B', name, False))
        jobs.append(('resume', ROOT, group['id'] + '-legacy-C', group['id'] + '-old', False))

    def execute(job):
        kind, checkout, name, a, b = job
        if kind == 'native':
            return run((args.label, checkout, (name, a, b, 1)),
                       runs=ROOT.parent / 'cross-vintage/confirmation-native',
                       data_dir=ROOT / 'data/frozen', contract=CONTRACT, end=contract['end'])
        destination = output / (name + '.json.gz')
        prefix = ROOT.parent / 'cross-vintage/confirmation-prefixes' / (a + '-prefix.json')
        assert prefix.exists()
        if destination.exists():
            # B continuation is independent of the candidate revision. Reuse
            # only its already-validated exact baseline/prefix, never a C run.
            import gzip
            from cross_vintage_metrics import read_metrics
            assert checkout == baseline
            payload = json.loads(gzip.decompress(destination.read_bytes()))
            expected_prefix = json.loads(prefix.read_text())
            assert payload['source_head'] == contract['baseline']
            assert payload['prefix'] == expected_prefix
            group = next(g for g in contract['confirmation'] if name.startswith(g['id'] + '-'))
            read_metrics(destination, group['common_close'], contract['end'])
            return {'case': name, 'exit_code': 0, 'source_root': str(checkout), 'reused_baseline': True}
        command = [sys.executable, str(ROOT / 'artifacts/branch-integration/start-date-research/resume_cross_vintage.py'),
                   '--prefix', str(prefix), '--output', str(destination), '--end', contract['end']]
        with (output / (name + '.log')).open('x') as log:
            result = subprocess.run(command, cwd=checkout, stdout=log, stderr=subprocess.STDOUT)
        return {'case': name, 'exit_code': result.returncode, 'source_root': str(checkout)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = []
        for result in pool.map(execute, jobs):
            print(json.dumps(result), flush=True)
            results.append(result)
    return int(any(row['exit_code'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
