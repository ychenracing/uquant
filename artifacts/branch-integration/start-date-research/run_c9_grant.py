import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

repo = Path('/workspace/scratch/607cd381710d/uquant-c9')
out = repo.parent / 'c9-grant-acceptance'
out.mkdir(exist_ok=True)
cases = ['baseline', 'native-sz300308', 'native-sz300502', 'native-sz300394']

def run(case):
    with (out / (case + '.log')).open('x') as log:
        result = subprocess.run([sys.executable, 'scripts/run_strategic_grant_acceptance.py',
            '--case', case, '--cache-dir', str(out / 'units'),
            '--output', str(out / (case + '-result.json'))], cwd=repo, stdout=log, stderr=subprocess.STDOUT)
    print(json.dumps({'case': case, 'exit_code': result.returncode}), flush=True)
    return result.returncode

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    codes = list(pool.map(run, cases))
if any(codes):
    raise SystemExit(1)
result = subprocess.run([sys.executable, 'scripts/run_strategic_grant_acceptance.py',
    '--cache-dir', str(out / 'units'), '--output', str(out / 'acceptance.json')], cwd=repo)
raise SystemExit(result.returncode)
