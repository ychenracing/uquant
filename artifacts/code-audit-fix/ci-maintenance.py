"""Resume PR83 patches, preserving source before bounded verification."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

BRANCH = 'codex/code-audit-fixes-20260922'
ROOT = Path.cwd()
OUT = ROOT / 'artifacts/code-audit-fix/full-validation' / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def identity(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}

def export_source(commit):
    records = []
    with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in git('ls-files').splitlines():
            if name.startswith(('data/', 'artifacts/')):
                continue
            p = ROOT / name
            if not p.is_file():
                continue
            record = {'path': name, **identity(p)}
            assert record['git_blob'] == git('rev-parse', f'{commit}:{name}')
            records.append(record)
            z.write(p, name)
        z.writestr('READBACK.json', json.dumps({'commit': commit, 'files': records}, indent=2))
    Path('/tmp/pr83-readback.json').write_text(json.dumps({'commit': commit, 'files': records}, indent=2))

head = git('rev-parse', 'HEAD')
assert os.environ['GITHUB_REF_NAME'] == BRANCH
if head != os.environ['GITHUB_SHA']:
    raise RuntimeError('superseded input: refuse to apply repairs to another checkout')
assert not git('status', '--porcelain', '--untracked-files=no')
paths = []
for filename in ('independent-ci.patch', 'batch-3.patch', 'owner-followup.patch'):
    patch = 'artifacts/code-audit-fix/full-validation/patches/' + filename
    paths.extend(row.split('\t')[-1] for row in git('apply', '--numstat', patch).splitlines())
    git('apply', '--check', patch)
    git('apply', patch)
p = ROOT / 'tests/architecture/_source_mutations.py'
source = p.read_text()
before = '            for token in iterator:\n                if token.type not in ignored:\n                    result.append(token)'
assert source.count(before) == 1
p.write_text(source.replace(before, '            result.extend(token for token in iterator if token.type not in ignored)'))
paths = sorted(set(paths) | {'tests/architecture/_source_mutations.py', 'tests/architecture/_explicit_delegation.py'})
python_paths = [p for p in paths if p.endswith('.py')]
subprocess.run(['uv', 'run', '--no-sync', 'ruff', 'check', '--select', 'I', '--fix', *python_paths], check=True)
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', *paths)
git('commit', '-m', 'fix: integrate pending owner checks and independent coverage aggregation')
source_commit = git('rev-parse', 'HEAD')
export_source(source_commit)
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == source_commit
for name in paths:
    assert (ROOT / name).read_bytes() == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
summary = {'input_head': head, 'tested_source': source_commit, 'source_saved': True, 'checks': [], 'logs': []}

def check(name, args, timeout):
    log = OUT / (name + '.txt')
    with log.open('wb') as f:
        try:
            status = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            status = 124
    data = log.read_bytes()
    packed = OUT / (name + '.txt.gz')
    packed.write_bytes(gzip.compress(data, mtime=0))
    summary['logs'].append({'path': str(packed.relative_to(ROOT)), 'raw_bytes': len(data),
                            'raw_sha256': hashlib.sha256(data).hexdigest(), **identity(packed)})
    summary['checks'].append({'name': name, 'command': args, 'exit_code': status, 'tail': data.decode(errors='replace')[-5000:]})
    (OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
    log.unlink()

check('lint', ['uv', 'run', '--no-sync', 'ruff', 'check', *python_paths], 30)
check('first-failure', ['uv', 'run', '--no-sync', 'pytest', '-x', '-vv', '--tb=short',
      'tests/architecture/test_execution_application_boundaries.py',
      'tests/architecture/test_architecture_governance.py',
      'tests/architecture/test_portfolio_public_owners.py'], 180)
check('state-negative', ['uv', 'run', '--no-sync', 'pytest', '-vv', '--tb=short',
      'tests/architecture/test_portfolio_boundaries.py::test_allocator_sentinel_copy_rejects_entry_or_unfiltered_events',
      'tests/test_code_audit_structure.py::test_explicit_delegate_check_rejects_non_equivalent_forwarding',
      'tests/test_lifecycle_and_risk.py::test_failed_restoration_retires_strategic_restore_before_early_return'], 90)
summary['passed'] = all(c['exit_code'] == 0 for c in summary['checks'])
(OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == source_commit
git('add', str(OUT))
git('commit', '-m', 'test: preserve immediate failure details and source-bound validation results')
evidence_commit = git('rev-parse', 'HEAD')
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == evidence_commit
for p in OUT.rglob('*'):
    if p.is_file():
        assert p.read_bytes() == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{p.relative_to(ROOT)}'])
print(json.dumps({'source': source_commit, 'evidence': evidence_commit, 'checks': [(c['name'], c['exit_code']) for c in summary['checks']]}))
raise SystemExit(0 if summary['passed'] else 1)
