"""Collect actual CI failures and recheck their current paths without a long-run wait."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path.cwd()
BRANCH = 'codex/code-audit-fixes-20260922'
API = 'https://api.github.com/repos/ychenracing/uquant/'
OUT = ROOT / 'artifacts/code-audit-fix/full-validation' / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def response(endpoint):
    request = urllib.request.Request(API + endpoint, headers={
        'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
        'Accept': 'application/vnd.github+json', 'User-Agent': 'uquant-pr83-verification'})
    try:
        return urllib.request.build_opener(NoRedirect()).open(request, timeout=40)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers['Location']
        if not location.startswith('https://'):
            raise ValueError('non-HTTPS download redirect') from exc
        # Signed blob downloads must not receive the GitHub Authorization header.
        return urllib.request.urlopen(location, timeout=60)

def api(endpoint):
    with response(endpoint) as stream:
        return json.load(stream)

def download(endpoint, path):
    with response(endpoint) as stream, path.open('wb') as output:
        shutil.copyfileobj(stream, output, length=1024 * 1024)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def identity(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}

head = git('rev-parse', 'HEAD')
assert head == os.environ['GITHUB_SHA'] and os.environ['GITHUB_REF_NAME'] == BRANCH
summary = {'source': head, 'completed_jobs': [], 'checks': [], 'errors': []}

def checkpoint():
    (OUT / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')

nodes = set()
for job_id in (106591666407, 106591666348, 106591666479, 106591666531):
    try:
        job = api(f'actions/jobs/{job_id}')
        assert job['status'] == 'completed' and job['run_id'] == 35679019524
        raw_path = OUT / f'{job_id}.log'
        download(f'actions/jobs/{job_id}/logs', raw_path)
        raw = raw_path.read_bytes()
        packed = raw_path.with_suffix('.log.gz')
        packed.write_bytes(gzip.compress(raw, mtime=0)); raw_path.unlink()
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw.decode(errors='replace'))
        failures = []
        for line in text.splitlines():
            if 'FAILED tests/' in line:
                node = line.split('FAILED ', 1)[1].split(' - ', 1)[0].strip()
                failures.append(node)
                if node.startswith('tests/test_'):
                    nodes.add(node)
        summary['completed_jobs'].append({'job_id': job_id, 'name': job['name'],
            'run_id': job['run_id'], 'conclusion': job['conclusion'], 'failures': failures,
            'summaries': [line for line in text.splitlines() if re.search(r'\d+ failed.*\d+ passed', line)][-2:],
            'log': str(packed.relative_to(ROOT)), 'raw': identity(raw)})
    except Exception as exc:
        summary['errors'].append({'part': str(job_id), 'error': type(exc).__name__ + ': ' + str(exc)})
    checkpoint()

if nodes:
    command = ['uv', 'run', '--no-sync', 'pytest', '-vv', '--tb=short', *sorted(nodes)]
    raw_path = OUT / 'failed-paths.log'
    with raw_path.open('wb') as output:
        try:
            rc = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=300).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    data = raw_path.read_bytes(); raw_path.unlink()
    (OUT / 'failed-paths.log.gz').write_bytes(gzip.compress(data, mtime=0))
    summary['checks'].append({'name': 'previously-failed-application-paths', 'command': command,
        'exit_code': rc, 'raw': identity(data), 'tail': data.decode(errors='replace')[-10000:]})
    checkpoint()

try:
    run = api('actions/runs/35679019431')
    summary['absolute'] = {key: run.get(key) for key in ('id', 'head_sha', 'status', 'conclusion')}
    artifacts = api('actions/runs/35679019431/artifacts?per_page=100')['artifacts']
    summary['absolute']['artifacts'] = [{key: a.get(key) for key in ('id', 'name', 'size_in_bytes', 'digest', 'expires_at')} for a in artifacts]
    finals = [a for a in artifacts if a['name'] == 'absolute-generalization-summary-35679019431-attempt-1']
    if finals:
        a = finals[0]; path = OUT / 'absolute-final-original.zip'
        download(f'actions/artifacts/{a["id"]}/zip', path)
        assert path.stat().st_size == a['size_in_bytes']
        assert 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() == a['digest']
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith('.json')]
            assert len(names) == 1
            raw = z.read(names[0]); result = json.loads(raw)
        (OUT / 'ABSOLUTE_FINAL_ORIGINAL.json').write_bytes(raw)
        summary['absolute']['final'] = {k: result.get(k) for k in ('passed', 'runner_success', 'capability_pass', 'canonical_sha256', 'summary')}
except Exception as exc:
    summary['errors'].append({'part': 'absolute', 'error': type(exc).__name__ + ': ' + str(exc)})
checkpoint()

# Preserve the exact current source for local review, not old snapshots over current code.
files = []
with zipfile.ZipFile('/tmp/pr83-changed-source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for entry in subprocess.check_output(['git', 'ls-tree', '-r', '-z', head]).split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1); mode, kind, blob = meta.decode().split(); name = name.decode()
        if kind != 'blob' or mode == '120000':
            continue
        if name.split('/')[0] in ('artifacts', 'data'):
            if not name.startswith('artifacts/code-audit-fix/') or Path(name).suffix not in ('.py', '.md', '.json'):
                continue
        data = subprocess.check_output(['git', 'show', f'{head}:{name}'])
        row = {'path': name, **identity(data)}
        assert row['git_blob'] == blob
        files.append(row); z.writestr(name, data)
    z.writestr('READBACK.json', json.dumps({'commit': head, 'files': files}, indent=2))
Path('/tmp/pr83-readback.json').write_text(json.dumps({'commit': head, 'files': files}, indent=2))
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('add', str(OUT)); git('commit', '-m', 'test: retain actual CI failure logs and current-path verification')
result = git('rev-parse', 'HEAD')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == head
git('push', 'origin', f'HEAD:refs/heads/{BRANCH}')
git('fetch', 'origin', BRANCH); assert git('rev-parse', 'FETCH_HEAD') == result
for name in git('diff', '--name-only', head, result).splitlines():
    assert (ROOT / name).read_bytes() == subprocess.check_output(['git', 'show', f'FETCH_HEAD:{name}'])
print(json.dumps({'head': result, 'errors': summary['errors'],
                  'checks': [{k: c[k] for k in ('name', 'exit_code')} for c in summary['checks']]}))
raise SystemExit(1 if summary['errors'] or any(c['exit_code'] for c in summary['checks']) else 0)
