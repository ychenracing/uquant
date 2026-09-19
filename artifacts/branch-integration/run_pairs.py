"""Run the eight declared cells through the existing immutable native replay."""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'artifacts/alpha-recovery/local-enhancement'
CONTRACT = FOLDER / 'FROZEN_SCENARIOS.json'
contract = json.loads(CONTRACT.read_text())
universe = contract['universe']
start, end = contract['window']['start'], contract['window']['end']
core = {'sz300308', 'sz300502', 'sz300394'}
optical = core | {'sh600487', 'sh601869'}
calendar = pd.read_csv(ROOT / 'data/frozen/sh000300.csv')['date'].str[:10]
offset = sorted(day for day in calendar if start <= day <= end)[5]
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
parser = argparse.ArgumentParser()
parser.add_argument('--candidate-root', type=Path, default=ROOT.parent / 'uquant-candidate')
parser.add_argument('--candidate-label', default='combined')
parser.add_argument('--candidate-only', action='store_true')
parser.add_argument('--only')
parser.add_argument('--workers', type=int, default=6)
args = parser.parse_args()
if args.only:
    selected = set(args.only.split(','))
    if selected - {case[0] for case in cases}:
        raise ValueError('Unknown case')
    cases = [case for case in cases if case[0] in selected]
runs = ROOT.parent / 'branch-integration-runs'
env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
       'PATH': str(ROOT / '.venv/bin') + os.pathsep + os.environ['PATH']}

def run(row):
    label, checkout, case = row
    name, symbols, begin, cost = case
    output = runs / label / (name + '.json.gz')
    log = output.with_suffix('.log')
    if output.exists():
        return {'label': label, 'case': name, 'existing': True}
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(FOLDER / 'replay.py'), '--root', str(checkout),
           '--data-dir', str(ROOT / 'data/frozen'), '--contract', str(CONTRACT),
           '--case', name, '--symbols', ','.join(symbols), '--start', begin,
           '--end', end, '--cost-multiplier', str(cost), '--output', str(output)]
    with log.open('x') as stream:
        result = subprocess.run(cmd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    return {'label': label, 'case': name, 'exit_code': result.returncode,
            'tail': log.read_text()[-1000:]}

checkouts = [(args.candidate_label, args.candidate_root.resolve())]
if not args.candidate_only:
    checkouts.insert(0, ('main', ROOT.parent / 'uquant-current-main'))
rows = [(label, checkout, case) for case in cases for label, checkout in checkouts]
with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
    for result in pool.map(run, rows):
        print(json.dumps(result), flush=True)
