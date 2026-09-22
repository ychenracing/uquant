"""Finish scoped integration checks; save source and originals without waiting on long CI."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path.cwd()
BRANCH = 'codex/code-audit-fixes-20260922'
OUT = ROOT / 'artifacts/code-audit-fix/full-validation' / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def save_log(name, data):
    path = OUT / (name + '.txt.gz')
    path.write_bytes(gzip.compress(data, mtime=0))
    return {'path': str(path.relative_to(ROOT)), 'raw_bytes': len(data),
            'raw_sha256': hashlib.sha256(data).hexdigest(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

head = git('rev-parse', 'HEAD')
assert os.environ['GITHUB_REF_NAME'] == BRANCH
assert head == os.environ['GITHUB_SHA'], 'superseded: no replay of repairs on another revision'
assert not git('status', '--porcelain', '--untracked-files=no')
summary = {'input_head': head, 'checks': [], 'logs': []}

def checkpoint():
    (OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')

def check(name, command, timeout=120):
    raw = OUT / (name + '.raw')
    with raw.open('wb') as f:
        try:
            status = subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            status = 124
    data = raw.read_bytes();raw.unlink()
    summary['logs'].append(save_log(name, data))
    summary['checks'].append({'name': name, 'command': command, 'exit_code': status,
                              'tail': data.decode(errors='replace')[-6000:]})
    checkpoint()
    return status

def export_files(commit):
    rows = []
    changed = git('diff', '--name-only', head, commit).splitlines()
    names = sorted(set(changed) | {'artifacts/code-audit-fix/ci-maintenance.py',
        'artifacts/code-audit-fix/full-validation/followup-repairs.py',
        'artifacts/code-audit-fix/full-validation/api-evolution.py'})
    with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in names:
            path = ROOT / name
            if not path.is_file():
                continue
            data = path.read_bytes()
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            assert blob == git('rev-parse', f'{commit}:{name}')
            rows.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'git_blob': blob})
            z.write(path, name)
        receipt = {'commit': commit, 'files': rows}
        z.writestr('READBACK.json', json.dumps(receipt, indent=2))
    Path('/tmp/pr83-readback.json').write_text(json.dumps(receipt, indent=2))

try:
    assert check('apply-fixtures', [sys.executable, 'artifacts/code-audit-fix/full-validation/followup-repairs.py'], 20) == 0
    assert check('api-evolution', ['uv', 'run', '--no-sync', 'python', 'artifacts/code-audit-fix/full-validation/api-evolution.py'], 45) == 0
    changed = [p for p in git('diff', '--name-only').splitlines() if p.endswith('.py')]
    changed += ['tests/architecture/_code_audit_api_projection.py']
    check('imports', ['uv', 'run', '--no-sync', 'ruff', 'check', '--select', 'I', '--fix', *changed], 30)
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', *changed, 'tests/fixtures/code_audit_api_delta.json')
    git('commit', '-m', 'test: align reviewed API and state fixtures while retaining mutation guards')
    source = git('rev-parse', 'HEAD');summary['tested_source'] = source
    # Workflows were published through the authorized connector, not this token.
    assert not any(p.startswith('.github/') for p in git('diff', '--name-only', head, source).splitlines())
    git('fetch', 'origin', BRANCH)
    assert git('rev-parse', 'FETCH_HEAD') == head
    git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
    git('fetch', 'origin', BRANCH)
    assert git('rev-parse', 'FETCH_HEAD') == source
    check('lint', ['uv', 'run', '--no-sync', 'ruff', 'check', *changed], 30)
    check('api-and-boundaries', ['uv', 'run', '--no-sync', 'pytest', '-vv', '--tb=short',
        'tests/architecture/test_public_api_contracts.py',
        'tests/architecture/test_current_allocation_book.py',
        'tests/architecture/test_risk_boundaries.py::test_current_holding_protection_gate_rejects_semantic_escape',
        'tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound',
        'tests/architecture/test_validation_boundaries.py::test_validation_policy_relocation_is_closed_and_source_bound',
        'tests/architecture/test_validation_boundaries.py::test_validation_policy_resigned_relocation_tamper_is_rejected',
        'tests/architecture/test_portfolio_public_owners.py',
        'tests/architecture/test_portfolio_boundaries.py::test_allocator_sentinel_copy_rejects_entry_or_unfiltered_events',
        'tests/test_code_audit_structure.py::test_explicit_delegate_check_rejects_non_equivalent_forwarding',
        'tests/test_lifecycle_and_risk.py::test_failed_restoration_retires_strategic_restore_before_early_return'], 190)
    check('remaining-governance', ['uv', 'run', '--no-sync', 'pytest', '-x', '-vv', '--tb=short',
        'tests/architecture/test_execution_application_boundaries.py',
        'tests/architecture/test_architecture_governance.py'], 160)
    summary['passed'] = all(c['exit_code'] == 0 for c in summary['checks'])
    checkpoint()
    git('add', str(OUT));git('commit', '-m', 'test: retain exact API, mutation and governance outcomes')
    result = git('rev-parse', 'HEAD')
    export_files(result)
    git('fetch', 'origin', BRANCH)
    assert git('rev-parse', 'FETCH_HEAD') == source
    git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
    git('fetch', 'origin', BRANCH)
    assert git('rev-parse', 'FETCH_HEAD') == result
    for row in json.loads(Path('/tmp/pr83-readback.json').read_text())['files']:
        assert (ROOT / row['path']).read_bytes() == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{row["path"]}'])
    print(json.dumps({'head': result, 'passed': summary['passed']}))
except Exception as exc:
    summary['runner_error'] = repr(exc);checkpoint()
    raise
finally:
    checkpoint()
raise SystemExit(0 if summary['passed'] else 1)
