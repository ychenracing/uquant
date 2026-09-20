"""Observe exposure-vs-name-count disagreements on original filled accounts."""
import dataclasses
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0,str(Path.cwd()))
from uquant.config import DEFAULT_CONFIG as cfg
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.risk_sector import observe_deployed_sector

paths=list(Path('../cross-vintage/native/continuity-main').glob('*.json.gz'))
paths.append(Path('../cross-vintage/native/recovery-c2/2024-offset0.json.gz'))
e=ProductionEngine('data/frozen')
sources=[(p,json.loads(gzip.decompress(p.read_bytes()))) for p in paths]
e._load(set(INDEX_SYMBOLS)|{s for _,r in sources for s in r['symbols']})
panel={s:e.workspace.feature_frame(s) for s in e._features}
rows=[]
for path,raw in sources:
    for day in raw['result']['daily_replay_evidence']:
        date=pd.Timestamp(day['date'])
        held={s:n for s,n in day['position_shares'].items() if n>0}
        if len(held)<2:continue
        obs=observe_deployed_sector(date=date,panel=panel,symbols=set(held),cfg=cfg,
            weights={s:n*day['close_marks'][s] for s,n in held.items()})
        divergence=float(panel['sh000682'].loc[date,'ret120']-panel['sh000300'].loc[date,'ret120'])
        if (obs is not None and divergence>=cfg.sector_guard_divergence
                and obs.weighted_return<=cfg.risk_fast_return
                and obs.negative_exposure>=cfg.sector_weighted_negative_exposure
                and obs.equal_return>cfg.risk_fast_return):
            rows.append(dict(source=str(path),source_head=raw['source_head'],date=day['date'],held=held,
                observation=dataclasses.asdict(obs),leadership_divergence=divergence))
out=dict(note='Necessary exposure/breadth comparison only; active guard, shock and concentrated-owner arbitration still require native replay.',rows=rows)
Path('artifacts/recovery-capital/SECTOR_VETO_DIAGNOSIS.json').write_text(json.dumps(out,indent=2)+'\n')
for r in rows:print(Path(r['source']).parent.name,Path(r['source']).name,r['date'],r['observation'])
