"""Read-only native decision observer; check every prefix decision against its raw replay."""
import argparse
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--raw', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--end', required=True)
a=p.parse_args()
root=a.root.resolve(); raw=a.raw.resolve(); out=a.output.resolve()
os.chdir(root);sys.path.insert(0,str(root))
from uquant.engine import ProductionEngine
from uquant.config import config_fingerprint
b=json.load(gzip.open(raw,'rt'))
rows=[]
original=ProductionEngine.decide

def observed(self, *args, **kwargs):
    account=kwargs['account']
    decision=original(self,*args,**kwargs)
    rows.append({'date':decision.date,'cash':account.cash,
                 'positions':{s:p.shares for s,p in account.positions.items()},
                 'anchors':dict(account.anchor_weights),'protected':dict(account.protected_weights),
                 'risk':decision.risk_summary,'canonical':decision.canonical_payload(effective_config_sha256=config_fingerprint(self.cfg))})
    return decision
ProductionEngine.decide=observed
result=ProductionEngine(root/'data/frozen').backtest(symbols=b['symbols'],start=b['start'],end=a.end)
assert result['decision_trace']==b['result']['decision_trace'][:len(rows)], 'Observer changed native decisions'
assert result['equity_curve']==b['result']['equity_curve'][:len(rows)], 'Observer changed native equity'
out.parent.mkdir(parents=True,exist_ok=True)
with gzip.open(out,'xt') as f:json.dump({'source':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'exact_native_prefix':True,'rows':rows},f,default=str,sort_keys=True)
print(json.dumps({'rows':len(rows),'exact_native_prefix':True,'source':b['source_head']}))
