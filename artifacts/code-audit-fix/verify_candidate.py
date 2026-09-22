"""Run bounded candidate checks and preserve their original output."""
from pathlib import Path
import gzip
import hashlib
import json
import os
import subprocess
import sys

BASE = '7bc5cd5e20038c94ab5cf556ce107104692236ac'
OUT = Path('artifacts/code-audit-fix')
RUN = os.environ['GITHUB_RUN_ID']
logs = OUT/'logs'/RUN
logs.mkdir(parents=True, exist_ok=True)
results = []

def check(name, args, timeout=600):
    try:
        process = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        code, output = process.returncode, process.stdout
    except subprocess.TimeoutExpired as exc:
        code, output = 124, (exc.stdout or b'') + b'\nTIMEOUT\n'
    log = logs/(name+'.txt.gz')
    log.write_bytes(gzip.compress(output, mtime=0))
    row = {'name':name,'command':args,'exit_code':code,'log':str(log),
           'output_bytes':len(output),'output_sha256':hashlib.sha256(output).hexdigest(),
           'tail':output[-8000:].decode('utf-8',errors='replace')}
    results.append(row)
    print(json.dumps({'check':name,'exit_code':code,'log':str(log)}),flush=True)
    (OUT/'VERIFICATION.json').write_text(json.dumps({'run':RUN,'checks':results},indent=2)+'\n')
    return code == 0

subprocess.run(['git','add','uquant','tests','benchmarks','pyproject.toml'],check=True)
changed = subprocess.check_output(['git','diff','--name-only',BASE],text=True).splitlines()
source = sorted(p for p in changed if p.endswith('.py') and p.startswith('uquant/'))
tests = ['tests/test_code_audit_boundaries.py','tests/test_broker_sync.py',
         'tests/test_cli_and_report.py','tests/test_market_workspace.py']
for path in ('tests/test_code_audit_structure.py','tests/test_code_audit_state.py'):
    if Path(path).exists(): tests.append(path)
check('format', ['uv','run','--no-sync','ruff','format',*source,*[p for p in tests if 'code_audit' in p]])
check('imports', ['uv','run','--no-sync','ruff','check','--select','I,F401','--fix',*source,*[p for p in tests if 'code_audit' in p]])
check('lint', ['uv','run','--no-sync','ruff','check',*source,*[p for p in tests if 'code_audit' in p]])
check('types', ['uv','run','--no-sync','mypy',*source])
check('regressions',['uv','run','--no-sync','pytest','-q',*tests],timeout=900)
check('state-invariants', ['uv','run','--no-sync','pytest','-q',
    'tests/test_sentinel_freeze_new_risk.py','tests/test_risk_transitions.py',
    'tests/test_ordinary_cash_rearm.py','tests/test_repair_capital_custody.py',
    'tests/test_core_residual_capital.py','tests/test_core_bounded_risk_restoration.py'],timeout=600)
check('public-seams',['uv','run','--no-sync','pytest','-q',
    'tests/architecture/test_portfolio_boundaries.py::test_portfolio_historical_class_and_instance_monkeypatch_seams_remain_live',
    'tests/architecture/test_execution_application_boundaries.py::test_execution_engine_imports_when_python_strips_docstrings_and_assertions'],timeout=120)
check('diff-check',['git','diff','--check'])
identity = {}
for path in sorted({*source,*tests}):
    data = Path(path).read_bytes()
    identity[path] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                      'git_blob':hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()}
report = {'run':RUN,'base':BASE,'source_head':os.environ['GITHUB_SHA'],
          'python':sys.version,'tested_files':identity,'checks':results,
          'passed':all(r['exit_code']==0 for r in results),
          'full_economic_acceptance':'NOT_RUN','main_merge':'NOT_AUTHORIZED'}
encoded=json.dumps(report,indent=2)+'\n'
(OUT/'VERIFICATION_SUMMARY.json').write_text(json.dumps({'run':RUN,'passed':report['passed'],'checks':[{k:r[k] for k in ('name','exit_code','tail')} for r in results]},indent=2)+'\n')
(OUT/'VERIFICATION.json').write_text(encoded)
(logs/'VERIFICATION.json').write_text(encoded)
sys.exit(0 if report['passed'] else 1)
