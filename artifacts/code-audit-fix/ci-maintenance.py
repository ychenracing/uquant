"""Recompute the coverage aggregate from original shards and preserve completed-run evidence."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
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

def download_artifact(artifact_id, target):
    metadata = api(f'actions/artifacts/{artifact_id}')
    assert not metadata['expired']
    with target.open('wb') as f:
        subprocess.run(['gh', 'api', f'repos/{REPO}/actions/artifacts/{artifact_id}/zip'], stdout=f, check=True, timeout=60)
    data = target.read_bytes()
    assert len(data) == metadata['size_in_bytes']
    assert 'sha256:' + hashlib.sha256(data).hexdigest() == metadata['digest']
    return {key: metadata[key] for key in ('id', 'name', 'size_in_bytes', 'digest', 'expires_at', 'workflow_run')}

head = git('rev-parse', 'HEAD')
assert head == os.environ['GITHUB_SHA'] and os.environ['GITHUB_REF_NAME'] == BRANCH
summary = {'input_head': head, 'coverage': {}, 'completed_job_logs': [], 'absolute': {}, 'checks': []}
input_head = head
subprocess.run(['python', 'artifacts/code-audit-fix/full-validation/finish-governance.py'], check=True)
changed_tests = ['tests/architecture/test_architecture_governance.py',
                 'tests/architecture/_initialization_edges.py',
                 'tests/architecture/test_runtime_dependency_contracts.py']
subprocess.run(['uv', 'run', '--no-sync', 'ruff', 'check', '--fix', *changed_tests], check=True)
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', *changed_tests)
git('commit', '-m', 'test: distinguish eager import cycles and verified compatibility delegation')
source_commit = git('rev-parse', 'HEAD')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == input_head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == source_commit
head = source_commit
summary['tested_source'] = source_commit
raw_test_log = OUT / 'governance-failures.raw'
with raw_test_log.open('wb') as f:
    try:
        test_status = subprocess.run(['uv', 'run', '--no-sync', 'pytest', '-vv', '--tb=short',
            'tests/architecture/test_architecture_governance.py::test_architecture_duplicate_private_helper_debt_is_zero_without_generic_utils',
            'tests/architecture/test_architecture_governance.py::test_architecture_current_blockers_match_empty_acceptance_allowlist',
            'tests/architecture/test_runtime_dependency_contracts.py'], stdout=f, stderr=subprocess.STDOUT, timeout=130).returncode
    except subprocess.TimeoutExpired:
        test_status = 124
raw_tests = raw_test_log.read_bytes(); raw_test_log.unlink()
(OUT / 'governance-failures.txt.gz').write_bytes(gzip.compress(raw_tests, mtime=0))
summary['checks'].append({'name': 'remaining-debt-regressions', 'exit_code': test_status,
                         'raw_bytes': len(raw_tests), 'raw_sha256': hashlib.sha256(raw_tests).hexdigest(),
                         'tail': raw_tests.decode(errors='replace')[-5000:]})
inputs = OUT / 'coverage-inputs'; inputs.mkdir()
coverage_artifacts = {}
for side, artifact_id in (('left', 10676051586), ('right', 10674162954)):
    archive = inputs / f'application-{side}.zip'
    coverage_artifacts[side] = download_artifact(artifact_id, archive)
    assert coverage_artifacts[side]['workflow_run']['id'] == 35679019524
    with zipfile.ZipFile(archive) as z:
        expected = '.coverage.application-' + side
        assert z.namelist() == [expected]
        (inputs / expected).write_bytes(z.read(expected))
source = coverage_artifacts['left']['workflow_run']['head_sha']
assert source == coverage_artifacts['right']['workflow_run']['head_sha'] == '6d4f8ab1cd72b5a7471bd2e3b92b0e035f278286'
base = Path(tempfile.mkdtemp(prefix='pr83-coverage-source-')) / 'source'
git('worktree', 'add', '--detach', str(base), source)
helper = r'''
import hashlib, io, json, os, sys
from pathlib import Path
from coverage import Coverage, CoverageData
base, inputs, output = map(Path, sys.argv[1:])
combined = CoverageData(basename=str(output / '.coverage'))
original_files = set()
for side in ('left', 'right'):
    data = CoverageData(basename=str(inputs / ('.coverage.application-' + side)))
    data.read()
    def remap(name):
        prefix = '/home/runner/work/uquant/uquant/'
        assert name.startswith(prefix), name
        relative = name.removeprefix(prefix)
        assert relative.startswith('uquant/') and (base / relative).is_file()
        original_files.add(relative)
        return str(base / relative)
    combined.update(data, map_path=remap)
combined.write()
os.chdir(base)
coverage = Coverage(data_file=str(output / '.coverage'), config_file=str(base / 'pyproject.toml'))
coverage.load()
buf = io.StringIO()
percent = coverage.report(file=buf)
(output / 'coverage-report.txt.gz').write_bytes(__import__('gzip').compress(buf.getvalue().encode(), mtime=0))
coverage.json_report(outfile=str(output / 'coverage.json'))
coverage.xml_report(outfile=str(output / 'coverage.xml'))
result = {'percent': percent, 'threshold': 85, 'passed': percent >= 85,
          'python': sys.version, 'coverage_version': __import__('coverage').__version__,
          'source_files': {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in sorted(original_files)}}
(output / 'COVERAGE_RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ('percent', 'threshold', 'passed', 'python', 'coverage_version')}))
sys.exit(0 if result['passed'] else 1)
'''
status = subprocess.run(['uv', 'run', '--no-sync', 'python', '-c', helper, str(base), str(inputs), str(OUT)], timeout=60).returncode
summary['coverage'] = {'run_id': 35679019524, 'source': source, 'artifacts': coverage_artifacts,
                       'result': json.loads((OUT / 'COVERAGE_RESULT.json').read_text()), 'exit_code': status,
                       'scope': 'Full collected application-shard coverage; independent from pytest and architecture outcomes.'}
summary['checks'].append({'name': 'coverage-85', 'exit_code': status})
# Preserve the complete original failed application log without printing its body.
job_id = 106591666407
job = api(f'actions/jobs/{job_id}')
assert job['status'] == 'completed' and job['run_id'] == 35679019524
raw_path = OUT / 'application-left.raw'
with raw_path.open('wb') as f:
    subprocess.run(['gh', 'api', f'repos/{REPO}/actions/jobs/{job_id}/logs'], stdout=f, check=True, timeout=60)
raw = raw_path.read_bytes(); raw_path.unlink()
packed = OUT / f'{job_id}.log.gz'; packed.write_bytes(gzip.compress(raw, mtime=0))
text = raw.decode(errors='replace')
summary['completed_job_logs'].append({'id': job_id, 'run_id': job['run_id'], 'conclusion': job['conclusion'],
    'path': str(packed.relative_to(ROOT)), 'raw_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
    'failures': [line.split('FAILED ', 1)[-1] for line in text.splitlines() if 'FAILED tests/' in line],
    'summaries': [line for line in text.splitlines() if re.search(r'\d+ failed.*\d+ passed', line)][-3:]})
run = api('actions/runs/35679019431')
summary['absolute'] = {key: run.get(key) for key in ('id', 'head_sha', 'status', 'conclusion', 'run_attempt')}
artifacts = api('actions/runs/35679019431/artifacts?per_page=100')['artifacts']
summary['absolute']['artifacts'] = [{key: a.get(key) for key in ('id', 'name', 'size_in_bytes', 'digest', 'expires_at')} for a in artifacts]
finals = [a for a in artifacts if a['name'] == 'absolute-generalization-summary-35679019431-attempt-1']
assert len(finals) <= 1
if finals:
    archive = OUT / 'absolute-final-original.zip'
    download_artifact(finals[0]['id'], archive)
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
# Do not label current HEAD as the producer of either earlier execution.
summary['production_diff_since_coverage_source'] = git('diff', '--stat', source, head, '--', 'uquant')
(OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
# Raw coverage databases remain in verified ZIP inputs; no duplicate mutable DB is committed.
for name in ('.coverage',):
    (OUT / name).unlink(missing_ok=True)
for path in inputs.glob('.coverage*'):
    path.unlink()
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', str(OUT)); git('commit', '-m', 'test: preserve full coverage aggregate and original completed acceptance evidence')
commit = git('rev-parse', 'HEAD')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == commit
names = set(git('diff', '--name-only', input_head, commit).splitlines())
names.update(changed_tests)
names.update(('artifacts/code-audit-fix/ci-maintenance.py', 'artifacts/code-audit-fix/full-validation/finish-governance.py'))
receipt = {'commit': commit, 'files': []}
with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for name in sorted(names):
        data = (ROOT / name).read_bytes()
        assert data == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
        row = {'path': name, **identity(data)}
        assert row['git_blob'] == git('rev-parse', f'FETCH_HEAD:{name}')
        receipt['files'].append(row); z.write(ROOT / name, name)
    z.writestr('READBACK.json', json.dumps(receipt, indent=2))
Path('/tmp/pr83-readback.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps({'commit': commit, 'coverage': summary['coverage']['result']['percent'],
                  'failure_nodes': summary['completed_job_logs'][0]['failures'], 'absolute': run['conclusion']}))
raise SystemExit(0 if status == 0 and test_status == 0 else 1)
