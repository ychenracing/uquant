"""Bounded paired production replay; originals retain independent source identities."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = 'ccb7be8907f8ec547c573d1e032a59ee5d4e2468'
ROOT = Path.cwd()
OUT = ROOT/'artifacts/code-audit-followup/paired-replay'
SCENARIOS = (
    ('early-trend', ('sz300308','sz300502','sz300394'), '2023-01-03','2023-03-31'),
    ('recent-risk', ('sz300308','sz300502','sz300394','sh688008','sh603986','sh688072','sz300666'), '2026-06-01','2026-08-05'),
)


def child(output: Path) -> None:
    sys.path.insert(0, str(Path.cwd()))
    import pandas as pd
    from dataclasses import asdict
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.engine import ProductionEngine, code_fingerprint
    from uquant.types import AccountState

    results = {'code_fingerprint': code_fingerprint(), 'config': config_fingerprint(), 'scenarios': []}
    for name, symbols, start, end in SCENARIOS:
        engine = ProductionEngine('data/frozen')
        engine.workspace.prepare(engine.workspace.bind_tradable(symbols))
        dates = engine.workspace.common_sessions('sh000300','sh000682')
        dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
        rows = []
        for date in dates:
            engine.execution.execute_open(date=date,account=account,panel={s:engine._raw[s] for s in symbols})
            decision = engine.decide(symbols=symbols,as_of=str(date.date()),account=account)
            account.pending_orders = list(decision.pending_orders)
            rows.append({'date':str(date.date()),'equity':engine.equity(account,date),
                         'decision':asdict(decision),'account':account.to_dict()})
        results['scenarios'].append({'name':name,'symbols':symbols,'start':start,'end':end,'rows':rows})
    raw=json.dumps(results,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    output.write_bytes(gzip.compress(raw,mtime=0))


def projection(row: dict) -> dict:
    decision, account = row['decision'], row['account']
    # Exact economic fields; source-derived grant/event IDs are retained in the
    # raw artifacts but cannot be equal between distinct production revisions.
    target_fields=('symbol','weight','lifecycle','alpha_score','confidence','reason',
                   'reduction_policy','reason_code','exit_kind','origin_subsystem','mechanism','replaces_symbol')
    order_fields=('order_id','signal_date','symbol','side','target_weight','lifecycle',
                  'remaining_shares','reduction_policy','reason_code','exit_kind','mechanism')
    fill_fields=('order_id','fill_date','symbol','side','shares','price','gross_value',
                 'commission','stamp_duty','transfer_fee','slippage_cost','lifecycle','mechanism','exit_kind')
    pos_fields=('symbol','shares','avg_cost','entry_date','highest_close','lifecycle')
    def select(value, keys): return {key:value[key] for key in keys}
    risk=decision['risk_summary']
    return {'date':row['date'],'equity':row['equity'],'cash':account['cash'],
            'opportunity':decision['opportunity'],'risk':decision['risk'],
            'target_gross':decision['target_gross'],'target_k':decision['target_k'],
            'risk_controls':{k:risk[k] for k in ('target_gross_cap','freeze_new_risk','shock_state','severity','reduction_level')},
            'positions':{k:select(v,pos_fields) for k,v in account['positions'].items()},
            'targets':[select(v,target_fields) for v in decision['targets']],
            'orders':[select(v,order_fields) for v in decision['pending_orders']],
            'fills':[select(v,fill_fields) for v in account['fills']]}


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    script=Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix='uquant-audit-base-') as temp:
        baseline=Path(temp)/'base'
        subprocess.run(['git','worktree','add','--detach',str(baseline),BASE],check=True,stdout=subprocess.DEVNULL)
        try:
            for name,cwd in (('baseline',baseline),('candidate',ROOT)):
                subprocess.run([sys.executable,str(script),'--child',str(OUT/(name+'.json.gz'))],cwd=cwd,check=True,timeout=500)
        finally:
            subprocess.run(['git','worktree','remove',str(baseline)],check=True)
    base=json.loads(gzip.decompress((OUT/'baseline.json.gz').read_bytes()))
    candidate=json.loads(gzip.decompress((OUT/'candidate.json.gz').read_bytes()))
    assert base['config']==candidate['config']
    comparisons=[]
    for a,b in zip(base['scenarios'],candidate['scenarios'],strict=True):
        assert a['name']==b['name'] and a['symbols']==b['symbols']
        left=[projection(row) for row in a['rows']];right=[projection(row) for row in b['rows']]
        mismatch=next((x['date'] for x,y in zip(left,right,strict=True) if x!=y),None)
        comparisons.append({'scenario':a['name'],'sessions':len(left),'equal':mismatch is None,
                            'first_difference':mismatch,'fills':len(right[-1]['fills']),
                            'final_equity':right[-1]['equity']})
    files={}
    for p in OUT.glob('*.json.gz'):
        raw=gzip.decompress(p.read_bytes())
        files[p.name]={'compressed_bytes':p.stat().st_size,'raw_bytes':len(raw),
                      'raw_sha256':hashlib.sha256(raw).hexdigest(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    report={'base':BASE,'candidate_source':os.environ.get('GITHUB_SHA'),'source_fingerprints':{
        'base':base['code_fingerprint'],'candidate':candidate['code_fingerprint']},
        'config':base['config'],'comparisons':comparisons,'originals':files,
        'scope':'diagnostic economic projection, not full frozen acceptance; identity invariants covered separately',
        'passed':all(row['equal'] for row in comparisons)}
    (OUT/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    if not report['passed']: raise SystemExit(1)

if __name__=='__main__':
    child(Path(sys.argv[2])) if len(sys.argv)>1 and sys.argv[1]=='--child' else main()
