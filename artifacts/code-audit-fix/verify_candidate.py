"""Verify final interface metadata; reuse only byte-applicable completed evidence."""
from pathlib import Path
import ast
import gzip
import hashlib
import json
import os
import subprocess
import sys

ROOT=Path.cwd()
OUT=ROOT/'artifacts/code-audit-fix'
RUN=os.environ['GITHUB_RUN_ID']
prior=json.loads((OUT/'VERIFICATION.json').read_text())
receipt=json.loads((OUT/'REMOTE_READBACK.json').read_text())
checks=prior['checks']
assert all(r['exit_code']==0 for r in checks if r['name']!='lint')
assert [r for r in checks if r['name']=='lint'][0]['exit_code']==1
assert 'RUF022' in [r for r in checks if r['name']=='lint'][0]['tail']
logs=OUT/'logs'/RUN
logs.mkdir(parents=True,exist_ok=True)
results=[]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def normalized_exports(data):
    tree=ast.parse(data)
    found=0
    for node in tree.body:
        if (isinstance(node,ast.Assign) and len(node.targets)==1
                and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='__all__'):
            values=ast.literal_eval(node.value)
            assert isinstance(values,tuple) and len(values)==len(set(values))
            node.value=ast.Tuple(elts=[ast.Constant(v) for v in sorted(values)],ctx=ast.Load())
            found+=1
    assert found==1
    return ast.dump(tree,include_attributes=False)

reused={}
for path,expected in prior['tested_files'].items():
    actual=(ROOT/path).read_bytes()
    if digest(actual)==expected['sha256'] and len(actual)==expected['bytes']:
        reused[path]='BYTE_IDENTICAL'
        continue
    assert path=='uquant/application/__init__.py',path
    original=subprocess.check_output(['git','show',receipt['commit']+':'+path])
    assert digest(original)==expected['sha256'] and len(original)==expected['bytes']
    assert normalized_exports(original)==normalized_exports(actual)
    # Production consumers import concrete names; the declaration order is not
    # executable strategy input or wildcard-import sequencing.
    for module in (ROOT/'uquant').rglob('*.py'):
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node,ast.ImportFrom) and node.module in {'uquant.application','application'}:
                assert all(alias.name!='*' for alias in node.names)
    reused[path]='ONLY_EXPLICIT_EXPORT_TUPLE_ORDER_CHANGED'

pair=json.loads((OUT/'paired-replay/RESULT.json').read_text())
assert pair['passed']
for name,meta in pair['originals'].items():
    encoded=(OUT/'paired-replay'/name).read_bytes()
    raw=gzip.decompress(encoded)
    assert len(encoded)==meta['compressed_bytes'] and digest(encoded)==meta['sha256']
    assert len(raw)==meta['raw_bytes'] and digest(raw)==meta['raw_sha256']


def check(name,args,timeout=300):
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        code,output=p.returncode,p.stdout
    except subprocess.TimeoutExpired as exc:
        code,output=124,(exc.stdout or b'')+b'\nTIMEOUT\n'
    path=logs/(name+'.txt.gz')
    path.write_bytes(gzip.compress(output,mtime=0))
    row={'name':name,'command':args,'exit_code':code,'log':str(path.relative_to(ROOT)),
         'output_bytes':len(output),'output_sha256':digest(output),'tail':output[-8000:].decode(errors='replace')}
    results.append(row)
    print(json.dumps({'check':name,'exit_code':code}),flush=True)

source=[p for p in prior['tested_files'] if p.startswith('uquant/')]
check('lint',['uv','run','--no-sync','ruff','check',*source,
    'tests/test_code_audit_boundaries.py','tests/test_code_audit_structure.py','tests/test_code_audit_state.py',
    'tests/architecture/test_portfolio_boundaries.py'])
check('reflection-contract',['uv','run','--no-sync','pytest','-q',
    'tests/architecture/test_portfolio_boundaries.py::test_portfolio_public_mro_pickle_reflection_and_import_modes_are_exact'])
check('diff-check',['git','diff','--check'])
sys.path.insert(0,str(ROOT))
from uquant.engine import code_fingerprint
files={}
for path in [*prior['tested_files'],'tests/architecture/test_portfolio_boundaries.py']:
    data=(ROOT/path).read_bytes()
    files[path]={'bytes':len(data),'sha256':digest(data),
                'git_blob':hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()}
report={'run':RUN,'input_commit':os.environ['GITHUB_SHA'],'python':sys.version,
        'current_code_fingerprint':code_fingerprint(),'verified_files':files,
        'reused_from_run':prior['run'],'reused_source_commit':receipt['commit'],
        'reuse_proof':reused,'reused_successful_checks':[
            {'name':r['name'],'log':r['log'],'output_sha256':r['output_sha256']}
            for r in checks if r['exit_code']==0],
        'paired_replay':pair,'checks':results,'passed':all(r['exit_code']==0 for r in results),
        'full_repository_ci':'NOT_RUN','full_economic_acceptance':'NOT_RUN','main_merge':'NOT_AUTHORIZED'}
encoded=json.dumps(report,indent=2)+'\n'
(OUT/'FINAL_VERIFICATION.json').write_text(encoded)
(logs/'FINAL_VERIFICATION.json').write_text(encoded)
summary={'run':RUN,'passed':report['passed'],'reused_tests':197,'reused_from_run':prior['run'],
         'paired_sessions':sum(r['sessions'] for r in pair['comparisons']),
         'checks':[{k:r[k] for k in ('name','exit_code','tail')} for r in results]}
(OUT/'VERIFICATION_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
sys.exit(0 if report['passed'] else 1)
