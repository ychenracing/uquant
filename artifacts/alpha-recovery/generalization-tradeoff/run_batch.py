"""Run only explicit cells, preserving all original per-cell production outputs."""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--stage',choices=['screen','performance','loo','stress'],required=True)
p.add_argument('--workers',type=int,default=4)
p.add_argument('--candidate-root',type=Path)
p.add_argument('--candidate-label',default='candidate')
p.add_argument('--only',help='Comma-separated diagnostic cell names; no final acceptance claim')
a=p.parse_args()
root=Path(__file__).resolve().parents[3]
folder=Path(__file__).resolve().parent
contract=root/'benchmarks/generalization_tradeoff_acceptance.json'
c=json.loads(contract.read_text())
universe=c['universe']
core={'sz300308','sz300502','sz300394'}
optical=core|{'sh600487','sh601869'}
cases={'full':universe,'remove_all_three':[s for s in universe if s not in core],
       'no_optical':[s for s in universe if s not in optical]}
rows=[]
if a.stage=='screen':
    for role in ('candidate','historical'):
        for name,symbols in cases.items():
            rows.append((role,name,symbols,c['window']['start'],c['window']['end'],1))
elif a.stage=='performance':
    for role in ('candidate','historical'):
        for pool,symbols in c['pools'].items():
            for window,dates in c['performance_windows'].items():
                rows.append((role,f'{pool}-{window}',symbols,dates['start'],dates['end'],1))
elif a.stage=='loo':
    for role in ('candidate','historical'):
        for symbol in universe:
            rows.append((role,f'loo-{symbol}',[s for s in universe if s!=symbol],c['window']['start'],c['window']['end'],1))
else:
    import pandas as pd
    calendars=[set(pd.read_csv(root/f'data/frozen/{symbol}.csv')['date'].str[:10])
               for symbol in ('sh000300','sh000682')]
    dates=sorted(day for day in calendars[0]&calendars[1]
                 if c['window']['start']<=day<=c['window']['end'])
    for scenario,removed in [('full',None),('remove308','sz300308'),('remove502','sz300502')]:
        symbols=[s for s in universe if s!=removed]
        for offset in c['stress']['start_session_offsets']:
            rows.append(('candidate',f'{scenario}-offset{offset}',symbols,dates[offset],c['window']['end'],1))
        rows.append(('candidate',f'{scenario}-cost2',symbols,dates[0],c['window']['end'],2))
if a.only:
    requested=set(a.only.split(','))
    rows=[row for row in rows if row[1] in requested]
    if requested-set(row[1] for row in rows):
        raise ValueError('Unknown requested cell')
env=dict(os.environ)
env['PATH']='/root/.cache/uv/archive-v0/QHAPFXmUc4qpZm4J/uv-0.11.33.data/scripts:'+env['PATH']
env['OPENBLAS_NUM_THREADS']='1'
env['OMP_NUM_THREADS']='1'

def run(row):
    role,name,symbols,start,end,cost=row
    work=(a.candidate_root.resolve() if a.candidate_root else root) if role=='candidate' else root.parent/'uquant-historical'
    label=a.candidate_label if role=='candidate' else role
    out=folder/'runs'/label/f'{name}.json.gz'
    log=folder/'runs'/label/f'{name}.log'
    if out.exists():
        return {'case':name,'role':role,'existing':True,'path':str(out)}
    log.parent.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(folder/'replay.py'),'--root',str(work),'--data-dir',str(root/'data/frozen'),
             '--contract',str(contract),'--case',name,'--symbols',','.join(symbols),'--start',start,'--end',end,
             '--cost-multiplier',str(cost),'--output',str(out)]
    with log.open('x') as stream:
        r=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT)
    return {'case':name,'role':role,'exit_code':r.returncode,'last_output':log.read_text()[-1500:]}

with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as executor:
    futures=[executor.submit(run,row) for row in rows]
    for future in concurrent.futures.as_completed(futures):
        print(json.dumps(future.result()),flush=True)
