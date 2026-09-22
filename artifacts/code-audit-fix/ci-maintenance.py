"""Preserve already verified results and exact completed logs; do not rerun tests."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path.cwd()
BRANCH = 'codex/code-audit-fixes-20260922'
REPO = 'ychenracing/uquant'
OUT = ROOT / 'artifacts/code-audit-fix/full-validation' / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPO}/{endpoint}'], timeout=30))

def identity(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}

def download(artifact_id, path):
    metadata = api(f'actions/artifacts/{artifact_id}')
    assert not metadata['expired']
    with path.open('wb') as f:
        subprocess.run(['gh', 'api', f'repos/{REPO}/actions/artifacts/{artifact_id}/zip'],
                       stdout=f, check=True, timeout=90)
    data = path.read_bytes()
    assert len(data) == metadata['size_in_bytes']
    assert 'sha256:' + hashlib.sha256(data).hexdigest() == metadata['digest']
    return {key: metadata[key] for key in ('id', 'name', 'size_in_bytes', 'digest', 'expires_at', 'workflow_run')}

head = git('rev-parse', 'HEAD')
assert head == os.environ['GITHUB_SHA'] and os.environ['GITHUB_REF_NAME'] == BRANCH
assert not git('diff', '--name-only', '53b36e4e58458bcd0d60177e4995e5b00fd4ab3d', head, '--', 'uquant', 'tests', 'benchmarks', 'data', 'uv.lock', 'pyproject.toml')
summary = {'input_head': head, 'coverage': {}, 'completed_job_logs': [], 'absolute': {},
           'governance': {}, 'collector_errors': [], 'tests_rerun': False}

def checkpoint():
    (OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')

original = OUT / 'verified-run-35685639978.zip'
summary['previous_original'] = download(10676991624, original)
assert summary['previous_original']['digest'] == 'sha256:f961475c23c24b3bb70ad0cd3d77c459678e987691258c3dce7c4299a66184ac'
with zipfile.ZipFile(original) as bundle:
    prefix = 'home/runner/work/uquant/uquant/artifacts/code-audit-fix/full-validation/35685639978/'
    coverage_raw = bundle.read(prefix + 'COVERAGE_RESULT.json')
    coverage = json.loads(coverage_raw)
    governance_raw = bundle.read(prefix + 'governance-failures.txt.gz')
    governance = gzip.decompress(governance_raw).decode()
    assert '10 passed' in governance and ' FAILED ' not in governance
    assert coverage['python'].startswith('3.12.13') and coverage['threshold'] == 85
    assert coverage['passed'] is True and coverage['percent'] >= 85
    (OUT / 'COVERAGE_RESULT.json').write_bytes(coverage_raw)
    (OUT / 'governance-failures.txt.gz').write_bytes(governance_raw)
summary['coverage'] = {'run_id': 35679019524, 'producer_source': '6d4f8ab1cd72b5a7471bd2e3b92b0e035f278286',
    'aggregate_run_id': 35685639978, 'percent': coverage['percent'], 'threshold': 85, 'passed': True,
    'python': coverage['python'], 'coverage_version': coverage['coverage_version'],
    'scope': 'Full application-shard coverage, independent of test-suite outcomes.'}
summary['governance'] = {'source': '53b36e4e58458bcd0d60177e4995e5b00fd4ab3d', 'run_id': 35685639978,
    'last_failures_passed': 2, 'new_negative_and_sanity_cases_passed': 8,
    'earlier_passed': {'35683876795': {'api_and_boundaries': 33, 'governance_prefix': 38},
                       '35684675683': {'remaining_governance': 55}},
    'raw_sha256': hashlib.sha256(gzip.decompress(governance_raw)).hexdigest()}
checkpoint()
# The CLI safety flag is scoped to a pipe into a file, never terminal rendering.
job_id = 106591666407
try:
    job = api(f'actions/jobs/{job_id}')
    assert job['status'] == 'completed' and job['run_id'] == 35679019524
    raw_path = OUT / 'application-left.raw'
    with raw_path.open('wb') as f:
        subprocess.run(['gh', '--allow-escape-sequences', 'api', f'repos/{REPO}/actions/jobs/{job_id}/logs'],
                       stdout=f, check=True, timeout=60)
    raw = raw_path.read_bytes(); raw_path.unlink()
    packed = OUT / f'{job_id}.log.gz'; packed.write_bytes(gzip.compress(raw, mtime=0))
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw.decode(errors='replace'))
    summary['completed_job_logs'].append({'id': job_id, 'run_id': job['run_id'], 'conclusion': job['conclusion'],
        'path': str(packed.relative_to(ROOT)), 'raw_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'failures': [line.split('FAILED ', 1)[-1] for line in text.splitlines() if 'FAILED tests/' in line],
        'summaries': [line for line in text.splitlines() if re.search(r'\d+ failed.*\d+ passed', line)][-3:]})
except Exception as exc:
    summary['collector_errors'].append({'part': 'application-log', 'error': repr(exc)})
checkpoint()
try:
    run = api('actions/runs/35679019431')
    summary['absolute'] = {key: run.get(key) for key in ('id', 'head_sha', 'status', 'conclusion', 'run_attempt')}
    artifacts = api('actions/runs/35679019431/artifacts?per_page=100')['artifacts']
    summary['absolute']['artifacts'] = [{key: a.get(key) for key in ('id', 'name', 'size_in_bytes', 'digest', 'expires_at')} for a in artifacts]
    finals = [a for a in artifacts if a['name'] == 'absolute-generalization-summary-35679019431-attempt-1']
    assert len(finals) <= 1
    if finals:
        archive = OUT / 'absolute-final-original.zip'; download(finals[0]['id'], archive)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist(); assert len(names) == 1 and names[0].endswith('.json')
            raw = z.read(names[0]); report = json.loads(raw)
        seal = report['canonical_sha256']
        payload = {key: value for key, value in report.items() if key != 'canonical_sha256'}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert digest == seal
        summary['absolute']['final'] = {key: report.get(key) for key in ('head', 'tree', 'run_id', 'run_attempt', 'runner_success', 'capability_pass', 'passed', 'canonical_sha256', 'summary', 'effective_config_sha256', 'production_source_sha256')}
        (OUT / 'ABSOLUTE_FINAL_ORIGINAL.json').write_bytes(raw)
    else:
        summary['absolute']['final'] = 'NOT_YET_AVAILABLE_NO_WAIT'
except Exception as exc:
    summary['collector_errors'].append({'part': 'absolute-final', 'error': repr(exc)})
checkpoint()
for p in OUT.glob('*.raw'):
    if p.stat().st_size:
        (OUT / (p.name + '.gz')).write_bytes(gzip.compress(p.read_bytes(), mtime=0))
    p.unlink()
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', str(OUT)); git('commit', '-m', 'test: retain verified full coverage and actual completed CI originals')
commit = git('rev-parse', 'HEAD')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == commit
names = set(git('diff', '--name-only', head, commit).splitlines())
names.update(('artifacts/code-audit-fix/ci-maintenance.py', 'artifacts/code-audit-fix/full-validation/finish-governance.py',
              'tests/architecture/_initialization_edges.py', 'tests/architecture/test_runtime_dependency_contracts.py',
              'tests/architecture/test_architecture_governance.py'))
receipt = {'commit': commit, 'files': [], 'submitted_files': []}
with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for name in sorted(names):
        data = (ROOT / name).read_bytes()
        assert data == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
        row = {'path': name, **identity(data)}
        assert row['git_blob'] == git('rev-parse', f'FETCH_HEAD:{name}')
        receipt['files'].append(row); z.write(ROOT / name, name)
    submitted = '4cf95a30e69e2cf96099327c1ceb5c0f81f386cb'
    for name in ('artifacts/code-audit-fix/ci-maintenance.py', 'artifacts/code-audit-fix/full-validation/finish-governance.py',
                 'tests/architecture/_initialization_edges.py', 'tests/architecture/test_runtime_dependency_contracts.py'):
        data = subprocess.check_output(['git', 'show', f'{submitted}:{name}'])
        saved = 'submitted/' + name
        receipt['submitted_files'].append({'source_commit': submitted, 'path': name, 'archive_path': saved, **identity(data)})
        z.writestr(saved, data)
    z.writestr('READBACK.json', json.dumps(receipt, indent=2))
Path('/tmp/pr83-readback.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({'commit': commit, 'collector_errors': summary['collector_errors'], 'coverage': summary['coverage'],
                  'failure_nodes': summary['completed_job_logs'], 'absolute': summary['absolute'].get('final')}))
raise SystemExit(1 if summary['collector_errors'] else 0)
