"""Finish independent PR83 CI repairs without waiting on long formal runs."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

BRANCH='codex/code-audit-fixes-20260922'
REPO='ychenracing/uquant'
OUT=Path('artifacts/code-audit-fix/full-validation')/os.environ['GITHUB_RUN_ID']
OUT.mkdir(parents=True,exist_ok=True)
def git(*args):return subprocess.check_output(['git',*args],text=True).strip()
def api(endpoint):return json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/{endpoint}']))
def store(name,data):
 p=OUT/(name+'.gz');p.write_bytes(gzip.compress(data,mtime=0))
 return {'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'compressed_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
head=git('rev-parse','HEAD');assert os.environ['GITHUB_REF_NAME']==BRANCH
assert not git('status','--porcelain','--untracked-files=no')
git('config','user.name','github-actions[bot]');git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
patch='artifacts/code-audit-fix/full-validation/patches/independent-ci.patch'
paths=[r.split('\t')[-1] for r in git('apply','--numstat',patch).splitlines()]
assert not git('diff','--name-only','014a14244184930ccdbecde284f4535a9da90c62','HEAD','--',*paths)
git('apply','--check',patch);git('apply',patch)
p=Path('tests/architecture/_source_mutations.py');data=p.read_bytes()
assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()=='71e4a2c950b3638c4099c231610b8b4492d90199'
s=data.decode();before='            for token in iterator:\n                if token.type not in ignored:\n                    result.append(token)';assert s.count(before)==1
p.write_text(s.replace(before,'            result.extend(token for token in iterator if token.type not in ignored)'))
paths.append(str(p));python_paths=[p for p in paths if p.endswith('.py')]
summary={'input_head':head,'checks':[],'logs':[],'runs':[],'known_blocker':'batch-3 test repair not admitted; remaining owner and mutation-fixture failures are not declared fixed'}
def check(name,command,timeout):
 output=OUT/(name+'.raw')
 with output.open('wb') as f:
  try:r=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=timeout);code=r.returncode
  except subprocess.TimeoutExpired:code=124
 raw=output.read_bytes();output.unlink();summary['logs'].append(store(name+'.txt',raw))
 summary['checks'].append({'name':name,'command':command,'exit_code':code,'tail':raw.decode(errors='replace')[-5000:]});return code
check('imports',['uv','run','--no-sync','ruff','check','--select','I','--fix',*python_paths],30)
git('add',*paths);git('commit','-m','fix: collect complete coverage independently and retain physical complexity diagnostics')
summary['tested_source']=git('rev-parse','HEAD')
summary['source_files']={p:{'bytes':len(Path(p).read_bytes()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest(),'git_blob':git('rev-parse',f'HEAD:{p}')} for p in paths}
check('lint',['uv','run','--no-sync','ruff','check',*python_paths],30)
check('affected',['uv','run','--no-sync','pytest','-q','--tb=short','tests/architecture/test_execution_application_boundaries.py','tests/test_absolute_generalization_contract.py',f'--junitxml={OUT}/affected.xml'],360)
for rid in (35678573820,35678573838,35678573831,35679019524,35679019431):
 run=api(f'actions/runs/{rid}');record={k:run.get(k) for k in ('id','head_sha','status','conclusion','created_at','run_started_at')};record['jobs']=[]
 record['artifacts']=[{k:a.get(k) for k in ('id','name','size_in_bytes','digest','expired')} for a in api(f'actions/runs/{rid}/artifacts?per_page=100')['artifacts']]
 for job in api(f'actions/runs/{rid}/jobs?per_page=100')['jobs']:
  row={k:job.get(k) for k in ('id','name','status','conclusion','started_at','completed_at')};record['jobs'].append(row)
  if job['status']!='completed' or job['conclusion']=='skipped':continue
  # Reuse already byte-preserved logs; only fetch newly completed job originals.
  previous=list(Path('artifacts/code-audit-fix/full-validation').glob(f'*/{rid}-{job["id"]}.txt.gz'))
  if previous:
   row['original_log']=str(sorted(previous)[-1]);continue
  output=OUT/f'{rid}-{job["id"]}.raw'
  with output.open('wb') as f:
   ret=subprocess.run(['gh','api','--allow-escape-sequences',f'repos/{REPO}/actions/jobs/{job["id"]}/logs'],stdout=f,stderr=subprocess.PIPE,timeout=25)
  if ret.returncode:row['log_error']=ret.stderr.decode(errors='replace')[:300]
  else:
   raw=output.read_bytes();saved=store(f'{rid}-{job["id"]}.txt',raw);summary['logs'].append(saved);row['original_log']=saved['path']
   row['failures']=[s for s in raw.decode(errors='replace').splitlines() if 'FAILED ' in s or 'ERROR ' in s][-30:]
  output.unlink(missing_ok=True)
 summary['runs'].append(record)
summary['independent_checks_passed']=all(c['exit_code']==0 for c in summary['checks'])
summary['full_acceptance_passed']=False
(OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
git('diff','--check');git('fetch','origin',BRANCH);assert git('rev-parse','FETCH_HEAD')==head
git('add',str(OUT));git('commit','-m','test: preserve independent CI verification and latest completed formal evidence')
commit=git('rev-parse','HEAD');git('push','origin',f'HEAD:refs/heads/{BRANCH}');git('fetch','origin',BRANCH);assert git('rev-parse','FETCH_HEAD')==commit
files=[]
for name in sorted(set(git('diff','--name-only',head,commit).splitlines())|{'artifacts/code-audit-fix/ci-maintenance.py','.github/workflows/audit-ci-maintenance.yml',patch}):
 b=Path(name).read_bytes();assert b==subprocess.check_output(['git','show',f'FETCH_HEAD:{name}'])
 blob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest();assert git('rev-parse',f'FETCH_HEAD:{name}')==blob
 files.append({'path':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'git_blob':blob})
Path('/tmp/pr83-readback.json').write_text(json.dumps({'commit':commit,'files':files},indent=2)+'\n')
with __import__('zipfile').ZipFile('/tmp/pr83-changed-source.zip','w',compression=8) as archive:
 for row in files:archive.write(row['path'],row['path'])
 archive.write('/tmp/pr83-readback.json','READBACK.json')
print(json.dumps({'commit':commit,'independent_checks_passed':summary['independent_checks_passed']}))
raise SystemExit(0 if summary['independent_checks_passed'] else 1)
