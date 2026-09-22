"""Apply admitted PR83 repairs and retain bounded verification and CI originals."""
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
assert not git('status','--porcelain','--untracked-files=no')
git('config','user.name','github-actions[bot]'); git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
patches=[str(Path('artifacts/code-audit-fix/full-validation/patches')/f'batch-{i}.patch') for i in (1,2,4)]
paths=[row.split('\t')[-1] for row in git('apply','--numstat',*patches).splitlines()]
assert not git('diff','--name-only','2a463024068386de98ed800752124eff3bcc5853','HEAD','--',*paths),'source changed since reviewed patch'
git('apply','--check',*patches);git('apply',*patches)
paths+=['tests/architecture/_source_mutations.py']
summary={'input_head':head,'jobs':[],'logs':[],'checks':[],'blocked_batch':'batch-3 was not admitted by tool safety; not applied or counted as fixed'}

def check(name,command,timeout):
    output=OUT/(name+'.raw')
    with output.open('wb') as f:
        try: result=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=timeout);code=result.returncode
        except subprocess.TimeoutExpired: code=124
    data=output.read_bytes();output.unlink();summary['logs'].append(store(name+'.txt',data))
    summary['checks'].append({'name':name,'command':command,'exit_code':code,'tail':data.decode(errors='replace')[-6000:]})
    return code

check('imports',['uv','run','--no-sync','ruff','check','--select','I','--fix',*paths],45)
git('add',*paths);git('commit','-m','fix: close new module authority and reflect reviewed explicit engine contracts')
summary['tested_source']=git('rev-parse','HEAD')
summary['source_files']={p:{'bytes':len(Path(p).read_bytes()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest(),'git_blob':git('rev-parse',f'HEAD:{p}')} for p in paths}
check('lint',['uv','run','--no-sync','ruff','check',*paths],45)
check('focused',['uv','run','--no-sync','pytest','-q','--tb=short','tests/architecture/test_execution_application_boundaries.py','tests/test_absolute_generalization_contract.py',f'--junitxml={OUT}/focused.xml'],360)
for rid in (35678573820,35678573917,35678573838,35678573831,35679019524,35679019431):
    for job in api(f'actions/runs/{rid}/jobs?per_page=100')['jobs']:
        row={k:job.get(k) for k in ('id','name','status','conclusion','started_at','completed_at')};row['run_id']=rid;summary['jobs'].append(row)
        if job['status']!='completed' or job['conclusion']=='skipped':continue
        output=OUT/f"{rid}-{job['id']}.raw"
        with output.open('wb') as f:
            ret=subprocess.run(['gh','api','--allow-escape-sequences',f"repos/{REPO}/actions/jobs/{job['id']}/logs"],stdout=f,stderr=subprocess.PIPE,timeout=20)
        if ret.returncode:row['log_error']=ret.stderr.decode(errors='replace')[:300]
        else:
            data=output.read_bytes();summary['logs'].append(store(f"{rid}-{job['id']}.txt",data))
            row['failures']=[s for s in data.decode(errors='replace').splitlines() if 'FAILED ' in s or 'ERROR ' in s][-80:]
        output.unlink(missing_ok=True)
summary['passed']=all(c['exit_code']==0 for c in summary['checks'])
(OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
git('diff','--check');git('fetch','origin',BRANCH)
assert git('rev-parse','FETCH_HEAD')==head,'concurrent branch update; evidence retained in artifact'
git('add',str(OUT));git('commit','-m','test: preserve exact check failures and completed full-validation logs')
commit=git('rev-parse','HEAD');git('push','origin',f'HEAD:refs/heads/{BRANCH}');git('fetch','origin',BRANCH);assert git('rev-parse','FETCH_HEAD')==commit
files=[]
for name in sorted(set(git('diff','--name-only',head,commit).splitlines())|{'artifacts/code-audit-fix/ci-maintenance.py','.github/workflows/audit-ci-maintenance.yml',*patches}):
    b=Path(name).read_bytes();remote=subprocess.check_output(['git','show',f'FETCH_HEAD:{name}']);assert b==remote
    blob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest();assert git('rev-parse',f'FETCH_HEAD:{name}')==blob
    files.append({'path':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'git_blob':blob})
Path('/tmp/pr83-readback.json').write_text(json.dumps({'commit':commit,'files':files},indent=2)+'\n')
with __import__('zipfile').ZipFile('/tmp/pr83-changed-source.zip','w',compression=8) as archive:
    for row in files:archive.write(row['path'],row['path'])
    archive.write('/tmp/pr83-readback.json','READBACK.json')
print(json.dumps({'commit':commit,'passed':summary['passed'],'logs':len(summary['logs'])}))
raise SystemExit(0 if summary['passed'] else 1)
