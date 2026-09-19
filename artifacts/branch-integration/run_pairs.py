"""Run declared native replays with validated reuse and preserved attempts."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import importlib.util
import json
import math
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'artifacts/alpha-recovery/local-enhancement'
CONTRACT = FOLDER / 'FROZEN_SCENARIOS.json'


def validate(path: Path, checkout: Path, expected: dict) -> dict:
    # Keep the original provenance/curve verifier as the evidence authority.
    spec = importlib.util.spec_from_file_location('paired_compare', FOLDER / 'compare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.read(path, checkout)
    for key, value in expected.items():
        if key != 'completed' and data.get(key) != value:
            raise ValueError(f'Evidence identity mismatch: {key}')
    if data['completed'] is not True or data.get('error'):
        raise ValueError('Replay did not complete')
    result = data['result']
    for key in ('equity_curve', 'order_ledger', 'decision_trace', 'daily_replay_evidence'):
        if not isinstance(result.get(key), list):
            raise ValueError(f'Missing replay payload: {key}')
    curve = result['equity_curve']
    if len(curve) < 2 or len(result['daily_replay_evidence']) != len(curve):
        raise ValueError('Incomplete replay payload')
    import pandas as pd
    calendar = pd.read_csv(checkout / 'data/frozen/sh000300.csv')['date'].str[:10]
    dates = sorted(day for day in calendar if expected['start'] <= day <= expected['end'])
    if [row['date'] for row in curve] != dates:
        raise ValueError('Incomplete or reordered calendar')
    if [row['date'] for row in result['daily_replay_evidence']] != dates:
        raise ValueError('Incomplete daily evidence')
    for point, ledger in zip(curve, result['daily_replay_evidence'], strict=True):
        cash = float(ledger['cash'])
        marked = cash + sum(float(shares) * float(ledger['close_marks'][symbol])
                            for symbol, shares in ledger['position_shares'].items())
        if (not math.isfinite(cash) or cash < -1e-6
                or any(float(shares) < 0 for shares in ledger['position_shares'].values())
                or not math.isclose(marked, float(point['equity']), rel_tol=1e-10, abs_tol=1e-6)):
            raise ValueError('Invalid daily cash/positions/equity reconciliation')
    if not isinstance(result.get('final_account', {}).get('fills'), list):
        raise ValueError('Missing final-account fills')
    from uquant.account import account_from_dict
    account = account_from_dict(result['final_account'], require_hashes=True)
    final_day = result['daily_replay_evidence'][-1]
    if (not math.isclose(account.cash, float(final_day['cash']), abs_tol=1e-6)
            or {s: p.shares for s, p in account.positions.items() if p.shares > 0}
            != {s: v for s, v in final_day['position_shares'].items() if v > 0}):
        raise ValueError('Final account differs from daily evidence')
    return data


def run(row, *, runs: Path, data_dir: Path, contract: Path, end: str) -> dict:
    label, checkout, case = row
    name, symbols, begin, cost = case
    folder = runs / label
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / (name + '.json.gz')
    with (folder / (name + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'label': label, 'case': name, 'exit_code': 1, 'error': 'Replay already running'}
        attempt = folder / 'attempts' / name / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
               'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
        base = [sys.executable, str(FOLDER / 'replay.py'), '--root', str(checkout),
                '--data-dir', str(data_dir), '--contract', str(contract), '--case', name,
                '--symbols', ','.join(symbols), '--start', begin, '--end', end,
                '--cost-multiplier', str(cost)]
        status = {'label': label, 'case': name, 'attempt': str(attempt), 'exit_code': 1}
        try:
            identity = attempt / 'identity.json'
            with (attempt / 'identity.log').open('x') as log:
                subprocess.run([*base, '--identity-only', '--output', str(identity)],
                               env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
                               pass_fds=(lock.fileno(),))
            expected = json.loads(identity.read_text())
            if output.exists():
                try:
                    validate(output, checkout, expected)
                except (ValueError, KeyError, TypeError, AssertionError, OSError, EOFError, RuntimeError) as exc:
                    # Preserve the exact rejected bytes; never overwrite them on retry.
                    output.rename(attempt / 'rejected.json.gz')
                    status['rejected_existing'] = repr(exc)
                else:
                    status.update(exit_code=0, existing=True)
                    return status
            pending = attempt / 'result.json.gz'
            with (attempt / 'replay.log').open('x') as log:
                child = subprocess.run([*base, '--output', str(pending)], env=env,
                                       stdout=log, stderr=subprocess.STDOUT, pass_fds=(lock.fileno(),))
            status['exit_code'] = child.returncode
            if child.returncode:
                return status
            validate(pending, checkout, expected)
            # Hard-link publication is atomic and refuses an existing destination.
            os.link(pending, output)
            status.update(exit_code=0, existing=False)
        except Exception as exc:
            status.update(exit_code=1, error=repr(exc))
        finally:
            (attempt / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
        return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-root', type=Path, default=ROOT.parent / 'uquant-candidate')
    parser.add_argument('--candidate-label', default='combined')
    parser.add_argument('--baseline-root', type=Path, default=ROOT.parent / 'uquant-current-main')
    parser.add_argument('--candidate-only', action='store_true')
    parser.add_argument('--only')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--runs', type=Path, default=ROOT.parent / 'branch-integration-runs')
    args = parser.parse_args()
    if Path(args.candidate_label).name != args.candidate_label or args.candidate_label in {'.', '..'}:
        parser.error('Candidate label must be one path component')
    contract = json.loads(CONTRACT.read_text())
    universe = contract['universe']
    start, end = contract['window']['start'], contract['window']['end']
    import pandas as pd
    calendar = pd.read_csv(ROOT / 'data/frozen/sh000300.csv')['date'].str[:10]
    offset = sorted(day for day in calendar if start <= day <= end)[5]
    core = {'sz300308', 'sz300502', 'sz300394'}
    optical = core | {'sh600487', 'sh601869'}
    cases = [
        ('full', universe, start, 1),
        ('no_optical', [s for s in universe if s not in optical], start, 1),
        ('remove_all_three', [s for s in universe if s not in core], start, 1),
        ('d-continuous_ai_era', contract['pools']['d'], start, 1),
        ('full-cost2', universe, start, 2),
        ('full-offset5', universe, offset, 1),
        ('loo-sz300308', [s for s in universe if s != 'sz300308'], start, 1),
        ('loo-sz300502', [s for s in universe if s != 'sz300502'], start, 1),
    ]
    if args.only:
        selected = set(args.only.split(','))
        if selected - {case[0] for case in cases}:
            parser.error('Unknown case')
        cases = [case for case in cases if case[0] in selected]
    checkouts = [(args.candidate_label, args.candidate_root.resolve())]
    if not args.candidate_only:
        checkouts.insert(0, ('main', args.baseline_root.resolve()))
    rows = [(label, checkout, case) for case in cases for label, checkout in checkouts]
    def execute(row):
        return run(row, runs=args.runs.resolve(), data_dir=ROOT / 'data/frozen', contract=CONTRACT, end=end)
    failed = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(execute, rows):
            print(json.dumps(result), flush=True)
            failed |= result['exit_code'] != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
