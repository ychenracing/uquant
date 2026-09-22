"""Resume only incomplete governance cases and preserve exact CI status/originals."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path.cwd()
BRANCH = 'codex/code-audit-fixes-20260922'
REPO = 'ychenracing/uquant'
PRIOR = '023877453c2176a2f9399c93ef595b86e7c0d6cf'
OUT = ROOT / 'artifacts/code-audit-fix/full-validation' / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPO}/{endpoint}'], timeout=25))

def identity(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}

head = git('rev-parse', 'HEAD')
assert head == os.environ['GITHUB_SHA'] and os.environ['GITHUB_REF_NAME'] == BRANCH
assert not git('status', '--porcelain', '--untracked-files=no')
assert not git('diff', '--name-only', PRIOR, head, '--', 'uquant', 'tests', 'benchmarks', 'data', 'uv.lock', 'pyproject.toml')
prior_log = ROOT / 'artifacts/code-audit-fix/full-validation/35683876795/remaining-governance.txt.gz'
raw_prior = gzip.decompress(prior_log.read_bytes())
passed = re.findall(r'^(tests/.+?) PASSED ', raw_prior.decode(), flags=re.MULTILINE)
assert len(passed) == 38 and len(set(passed)) == 38
assert ' FAILED ' not in raw_prior.decode()
summary = {'input_head': head, 'tested_source': head, 'reused_from': PRIOR,
           'reused_governance_cases': passed, 'previous_complete_boundary_cases': 33,
           'checks': [], 'logs': [], 'formal_runs': [], 'full_acceptance_passed': None}

command = ['uv', 'run', '--no-sync', 'pytest', '-vv', '--tb=short', '--maxfail=8',
           'tests/architecture/test_execution_application_boundaries.py',
           'tests/architecture/test_architecture_governance.py',
           *['--deselect=' + node for node in passed], f'--junitxml={OUT}/remaining.xml']
log = OUT / 'remaining.txt'
with log.open('wb') as f:
    try:
        status = subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, timeout=430).returncode
    except subprocess.TimeoutExpired:
        status = 124
raw = log.read_bytes(); log.unlink()
packed = OUT / 'remaining.txt.gz'; packed.write_bytes(gzip.compress(raw, mtime=0))
summary['checks'].append({'name': 'remaining-governance', 'command': command, 'exit_code': status,
                          'tail': raw.decode(errors='replace')[-7000:]})
summary['logs'].append({'path': str(packed.relative_to(ROOT)), 'raw_bytes': len(raw),
                        'raw_sha256': hashlib.sha256(raw).hexdigest(), **identity(packed.read_bytes())})
# Read, never relabel, the actual formal executions. They continue independently.
for run_id in (35679019431, 35679019524):
    try:
        run = api(f'actions/runs/{run_id}')
        record = {key: run.get(key) for key in ('id', 'head_sha', 'status', 'conclusion', 'run_attempt', 'created_at')}
        record['jobs'] = [{key: row.get(key) for key in ('id', 'name', 'status', 'conclusion', 'started_at', 'completed_at')}
                          for row in api(f'actions/runs/{run_id}/jobs?per_page=100')['jobs']]
        record['artifacts'] = [{key: row.get(key) for key in ('id', 'name', 'size_in_bytes', 'digest', 'expired', 'expires_at')}
                               for row in api(f'actions/runs/{run_id}/artifacts?per_page=100')['artifacts']]
        summary['formal_runs'].append(record)
    except Exception as exc:
        summary['formal_runs'].append({'id': run_id, 'read_error': repr(exc)})
summary['local_checks_passed'] = status == 0
(OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', str(OUT))
git('commit', '-m', 'test: retain resumed governance outcomes and exact long-running CI status')
commit = git('rev-parse', 'HEAD')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == commit
names = set(git('diff', '--name-only', head, commit).splitlines())
names.add('artifacts/code-audit-fix/ci-maintenance.py')
receipt = {'commit': commit, 'files': []}
with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(names):
        path = ROOT / name; data = path.read_bytes()
        assert data == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
        row = {'path': name, **identity(data)}
        assert row['git_blob'] == git('rev-parse', f'FETCH_HEAD:{name}')
        receipt['files'].append(row); archive.write(path, name)
    # Historical submitted script is preserved for the pending original write readback.
    old_path = 'artifacts/code-audit-fix/ci-maintenance.py'
    historical = subprocess.check_output(['git', 'show', f'288ba743c88186f859de96399a6292cb5cc70ecc:{old_path}'])
    archive.writestr('historical/pr83-resume.py', historical)
    receipt['historical'] = {'commit': '288ba743c88186f859de96399a6292cb5cc70ecc', 'archive_path': 'historical/pr83-resume.py', **identity(historical)}
    archive.writestr('READBACK.json', json.dumps(receipt, indent=2))
Path('/tmp/pr83-readback.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({'commit': commit, 'checks_passed': status == 0, 'exit_code': status}))
raise SystemExit(0 if status == 0 else 1)
