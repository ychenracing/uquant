"""One-shot PR83 registry repair and original completed-job evidence collection."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

BRANCH = 'codex/code-audit-fixes-20260922'
REPO = 'ychenracing/uquant'
RUNS = (35678573820, 35678573917, 35678573838, 35678573831)
OUT = Path('artifacts/code-audit-fix/full-validation/initial')
OUT.mkdir(parents=True, exist_ok=True)


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def api(endpoint: str) -> object:
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPO}/{endpoint}']))


head = git('rev-parse', 'HEAD')
assert os.environ['GITHUB_REF_NAME'] == BRANCH
assert not git('status', '--porcelain', '--untracked-files=no')
summary = {'input_head': head, 'runs': [], 'logs': []}
for run_id in RUNS:
    run = api(f'actions/runs/{run_id}')
    jobs = []
    page = 1
    while True:
        batch = api(f'actions/runs/{run_id}/jobs?per_page=100&page={page}')['jobs']
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    record = {'run_id': run_id, 'head_sha': run['head_sha'], 'status': run['status'],
              'conclusion': run['conclusion'], 'jobs': []}
    for job in jobs:
        row = {key: job.get(key) for key in ('id', 'name', 'status', 'conclusion', 'started_at', 'completed_at')}
        record['jobs'].append(row)
        if job['status'] != 'completed' or job['conclusion'] == 'skipped':
            continue
        raw = OUT / f"{run_id}-{job['id']}.txt"
        with raw.open('wb') as output:
            result = subprocess.run(['gh', 'api', f"repos/{REPO}/actions/jobs/{job['id']}/logs"], stdout=output, stderr=subprocess.PIPE)
        if result.returncode:
            row['log_error'] = result.stderr.decode(errors='replace')[:400]
            raw.unlink(missing_ok=True)
            continue
        data = raw.read_bytes()
        compressed = raw.with_suffix('.txt.gz')
        compressed.write_bytes(gzip.compress(data, mtime=0))
        raw.unlink()
        item = {'path': str(compressed), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                'compressed_sha256': hashlib.sha256(compressed.read_bytes()).hexdigest()}
        summary['logs'].append(item)
        text = data.decode(errors='replace')
        row['failure_lines'] = [line[28:] for line in text.splitlines() if 'FAILED ' in line or 'ERROR ' in line or ' error:' in line][-100:]
    summary['runs'].append(record)
path = Path('uquant/validation/absolute_generalization/contract.py')
content = path.read_bytes()
assert hashlib.sha256(content).hexdigest() == '45016d4bcbc2b6aca3123aeb09f7701296ffd7374ac4b7acc064ff01599f8a67'
old = b'f0e5d14f2f12af6f0711e0a4fa506fd29ce2a606fa74eddb89962643048105e5'
new = b'ef45ba91d143b77222607158d28fa8bf2b1ef7610f2bbb9c1121c32c707e07ec'
assert content.count(old) == 1
updated = content.replace(old, new)
assert hashlib.sha256(updated).hexdigest() == '4fba8711abe383aaf72b7a6b46221f9d9a316ffe64132a77a5faaad66dbcea60'
path.write_bytes(updated)
summary['repair'] = {'path': str(path), 'old_registry': old.decode(), 'new_registry': new.decode(),
                     'sha256': hashlib.sha256(updated).hexdigest(), 'scope': 'registry membership anchor only; economic policy/config/data unchanged'}
(OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
git('diff', '--check')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == head, 'concurrent branch update; preserve local files only'
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', str(path), str(OUT))
git('commit', '-m', 'fix: align Absolute source registry anchor and retain initial full-validation evidence')
commit = git('rev-parse', 'HEAD')
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH)
assert git('rev-parse', 'FETCH_HEAD') == commit
verified = []
for name in sorted(set(git('diff', '--name-only', head, commit).splitlines()) | {'artifacts/code-audit-fix/ci-maintenance.py', '.github/workflows/audit-ci-maintenance.yml'}):
    local = Path(name).read_bytes()
    remote = subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
    assert local == remote
    blob = hashlib.sha1(b'blob ' + str(len(local)).encode() + b'\0' + local).hexdigest()
    assert git('rev-parse', f'FETCH_HEAD:{name}') == blob
    verified.append({'path': name, 'bytes': len(local), 'sha256': hashlib.sha256(local).hexdigest(), 'git_blob': blob})
receipt = {'commit': commit, 'branch': BRANCH, 'verified_files': verified}
Path('/tmp/pr83-readback.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'commit': commit, 'logs_preserved': len(summary['logs']), 'readback_verified': True}))
