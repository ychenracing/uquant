"""Economic differences only; keep source identities intact in the raw replays."""
import argparse
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('baseline',type=Path)
p.add_argument('candidate',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args()
b=json.load(gzip.open(a.baseline,'rt'))['result']
c=json.load(gzip.open(a.candidate,'rt'))['result']

def contribution(r):
    values=defaultdict(float)
    for f in r['final_account']['fills']:
        fees=sum(f[k] for k in ('commission','stamp_duty','transfer_fee'))
        values[f['symbol']] += (f['gross_value'] if f['side']=='SELL' else -f['gross_value'])-fees
    last=r['daily_replay_evidence'][-1]
    for s,shares in last['position_shares'].items():
        values[s]+=shares*last['close_marks'][s]
    assert math.isclose(sum(values.values()),r['final_equity']-r['final_account']['initial_cash'],abs_tol=.01)
    return values

def economic(day):
    return {'risk':day['risk'],'targets':[{k:t.get(k) for k in ('symbol','weight','lifecycle','mechanism','reason')} for t in day['targets']],
            'orders':[{k:o.get(k) for k in ('symbol','side','target_weight','reason','mechanism')} for o in day['orders']]}

bv,cv=contribution(b),contribution(c)
diffs=[{'symbol':s,'baseline':bv[s],'candidate':cv[s],'delta':cv[s]-bv[s]} for s in bv.keys()|cv.keys()]
diffs.sort(key=lambda r:r['delta'])
days=[]
for bd,cd in zip(b['decision_trace'],c['decision_trace'],strict=True):
    assert bd['date']==cd['date']
    be,ce=economic(bd),economic(cd)
    if be!=ce:days.append({'date':bd['date'],'baseline':be,'candidate':ce})
# Preserve all differing decisions; the report can cite a bounded sample.
out={'contributions':diffs,'equity_delta':c['final_equity']-b['final_equity'],
     'contribution_delta':sum(r['delta'] for r in diffs),'different_decision_days':len(days),
     'decision_differences':days,'new_buy_symbols':sorted({f['symbol'] for f in c['final_account']['fills'] if f['side']=='BUY'}-{f['symbol'] for f in b['final_account']['fills'] if f['side']=='BUY'})}
a.output.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k!='decision_differences'},indent=2))
if days:print('first difference',json.dumps(days[0]))
