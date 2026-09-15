"""Reuse five unchanged public production traces, keeping the original producer."""
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[3]
folder=Path(__file__).resolve().parent
contract_hash=hashlib.sha256((root/'benchmarks/generalization_tradeoff_acceptance.json').read_bytes()).hexdigest()
historical=Path('/workspace/scratch/73b9104c478a/uquant-restoration-risk/artifacts/alpha-recovery/canonical-baselines')
candidate=Path('/workspace/scratch/14f51f0acedb/uquant-readiness/artifacts/alpha-recovery/reference-readiness')
rows=[('historical','a-bull_crash_2025_2026',historical/'historical-a.json.gz'),
      ('historical','e-bull_crash_2025_2026',historical/'historical-e.json.gz'),
      ('candidate','a-bull_crash_2025_2026',candidate/'a.json.gz'),
      ('candidate','e-bull_crash_2025_2026',candidate/'e.json.gz'),
      ('candidate','e-h2_2024',candidate/'e-h2.json.gz')]
for role,case,path in rows:
    raw=path.read_bytes();d=json.loads(gzip.decompress(raw))
    assert d['completed'] is True
    target=root if role=='candidate' else root.parent/'uquant-historical'
    inp={}
    for name,sha in d['source_files'].items():
        if (name.startswith('uquant/') and name.endswith(('.py','.json'))) or name=='benchmarks/reference_registry.json':
            assert hashlib.sha256((target/name).read_bytes()).hexdigest()==sha,(case,name)
            inp[name]=sha
        elif name.startswith('data/frozen/'):
            assert hashlib.sha256((root/name).read_bytes()).hexdigest()==sha,(case,name)
            inp['data/'+name.removeprefix('data/frozen/')]=sha
    fills=[f for row in d['trace'] for f in row['new_fills']]
    result={**d['metrics'],'equity_curve':[{'date':r['date'],'equity':r['equity']} for r in d['trace']],
            'fees':sum(f['commission']+f['stamp_duty']+f['transfer_fee'] for f in fills),
            'slippage_cost':sum(f['slippage_cost'] for f in fills)}
    env={'schema_version':1,'method':'native_public_trace_reuse','case':case,'source_head':d['commit'],
         'source_tree':subprocess.check_output(['git','-C',str(root),'rev-parse',d['commit']+':uquant'],text=True).strip(),
         'inputs':inp,'runtime':d['environment'],'config':d['config'],'contract_sha256':contract_hash,
         'runner_sha256':d['runner_sha256'],'adapter_sha256':d['adapter_sha256'],
         'symbols':sorted(d['symbols']),'start':d['interval']['start'],'end':d['interval']['end'],
         'cost_multiplier':1,'completed':True,'result':result,'sessions':len(d['trace']),
         'elapsed_seconds':d['elapsed_seconds'],'reused_from':{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}}
    out=folder/'runs'/role/(case+'.json.gz');out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('xb') as stream:stream.write(gzip.compress(json.dumps(env,sort_keys=True,allow_nan=False).encode(),mtime=0))
    assert json.loads(gzip.decompress(out.read_bytes()))==env
    print(case,role,'REUSED',env['source_head'],result['final_wealth'])
