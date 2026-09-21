"""Validate native final serialization and all observed cash/fill timing boundaries."""
import argparse
import gzip
import json
from pathlib import Path

from uquant.account.codec import account_from_dict

p=argparse.ArgumentParser()
p.add_argument('directory',type=Path)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
rows=[]
for path in sorted(a.directory.rglob('*.json.gz')):
    if path.parent.name not in {'baseline','c1-fixed','c2'}:
        continue
    with gzip.open(path,'rt') as stream:
        d=json.load(stream)
    if not d.get('completed'):
        continue
    r=d['result']
    account=r['final_account']
    decoded=account_from_dict(account)
    assert decoded.to_dict()==account, path
    assert all(day['cash'] >= 0 for day in r['daily_replay_evidence']), path
    assert all(f['fill_date']>f['signal_date'] and f['shares']>0 for f in account['fills']),path
    rows.append({'path':str(path),'source':d['source_head'],'sessions':len(r['equity_curve']),
                 'exact_account_roundtrip':True,'nonnegative_daily_cash':True,'strict_next_session_fills':True})
a.output.write_text(json.dumps(rows,indent=2)+'\n')
print('Validated',len(rows),'complete native accounts')
