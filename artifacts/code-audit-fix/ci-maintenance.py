"""Bounded PR83 checks and file-backed collection of existing CI evidence."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

BRANCH = 'codex/code-audit-fixes-20260922'
REPO = 'ychenracing/uquant'
OUT = Path('artifacts/code-audit-fix/full-validation') / os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True, exist_ok=True)

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPO}/{endpoint}']))

def store(name, data):
    p=OUT/(name+'.gz'); p.write_bytes(gzip.compress(data,mtime=0))
    return {'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'compressed_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

head=git('rev-parse','HEAD')
assert os.environ['GITHUB_REF_NAME']==BRANCH
summary={'head':head,'jobs':[],'logs':[],'checks':[]}
for rid in (35678573820,35678573917,35678573838,35678573831):
    for job in api(f'actions/runs/{rid}/jobs?per_page=100')['jobs']:
        row={k:job.get(k) for k in ('id','name','status','conclusion','started_at','completed_at')}; row['run_id']=rid
        summary['jobs'].append(row)
        if job['status']!='completed' or job['conclusion']=='skipped': continue
        log=OUT/f"{rid}-{job['id']}.raw"
        with log.open('wb') as f:
            ret=subprocess.run(['gh','api','--allow-escape-sequences',f"repos/{REPO}/actions/jobs/{job['id']}/logs"],stdout=f,stderr=subprocess.PIPE)
        if ret.returncode:
            row['log_error']=ret.stderr.decode(errors='replace')[:400]
        else:
            data=log.read_bytes(); summary['logs'].append(store(f"{rid}-{job['id']}.txt",data))
            row['failures']=[s for s in data.decode(errors='replace').splitlines() if 'FAILED ' in s or 'ERROR ' in s][-100:]
        log.unlink(missing_ok=True)
command=['uv','run','--no-sync','pytest','-q','--tb=short','tests/architecture/test_execution_application_boundaries.py','tests/architecture/test_portfolio_public_owners.py','tests/test_absolute_generalization_contract.py',f'--junitxml={OUT}/focused.xml']
log=OUT/'focused.raw'
with log.open('wb') as f:
    try: result=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=420); code=result.returncode
    except subprocess.TimeoutExpired: code=124
raw=log.read_bytes(); log.unlink(); summary['logs'].append(store('focused.txt',raw))
summary['checks'].append({'command':command,'exit_code':code,'tail':raw.decode(errors='replace')[-18000:]})
summary['passed']=code==0
(OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
git('fetch','origin',BRANCH)
assert git('rev-parse','FETCH_HEAD')==head,'concurrent branch update; evidence remains in artifact'
git('config','user.name','github-actions[bot]'); git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
git('add',str(OUT)); git('commit','-m','test: preserve full-CI originals and exact owner/Absolute diagnostic results')
commit=git('rev-parse','HEAD'); git('push','origin',f'HEAD:refs/heads/{BRANCH}'); git('fetch','origin',BRANCH)
assert git('rev-parse','FETCH_HEAD')==commit
files=[]
for name in sorted(set(git('diff','--name-only',head,commit).splitlines())|{'artifacts/code-audit-fix/ci-maintenance.py','.github/workflows/audit-ci-maintenance.yml'}):
    b=Path(name).read_bytes(); remote=subprocess.check_output(['git','show',f'FETCH_HEAD:{name}']); assert b==remote
    blob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest(); assert git('rev-parse',f'FETCH_HEAD:{name}')==blob
    files.append({'path':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'git_blob':blob})
Path('/tmp/pr83-readback.json').write_text(json.dumps({'commit':commit,'files':files},indent=2)+'\n')
print(json.dumps({'commit':commit,'passed':summary['passed'],'logs':len(summary['logs'])}))
raise SystemExit(0 if summary['passed'] else 1)
