import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.cwd()))
from uquant.application.market_observations import recent_reversal_observations
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine

symbols = json.loads(Path("artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json").read_text())[
    "universe"
]
e = ProductionEngine("data/frozen")
e.workspace.prepare(e.workspace.bind_tradable(symbols))
panel = {s: f for s, f in e._features.items() if s not in ["sh000300", "sh000682"]}
tech = e._features["sh000682"]
rows = []
for day in ["2023-01-04", "2023-01-10", "2023-01-19"]:

    def observe(p, t, day=day):
        return recent_reversal_observations(
            data=e.data,
            cfg=DEFAULT_CONFIG,
            date=pd.Timestamp(day),
            panel=p,
            tech=t,
            allocator=e.allocator,
            score_cache=e._leader_score_cache,
            observation_cache=e._reversal_observation_cache,
        )

    e._reversal_observation_cache.clear()
    cold = observe(panel, tech)
    warm = observe(panel, tech)
    assert cold == warm
    e._reversal_observation_cache.clear()
    e._leader_score_cache.clear()
    bounded = observe({s: f.loc[:day].copy() for s, f in panel.items()}, tech.loc[:day].copy())
    assert bounded == cold
    for owner, event in cold.items():
        assert panel[owner].loc[day, "close"] >= panel[owner].loc[event["observed_session"], "close"]
    rows.append(
        {
            "date": day,
            "cold_equals_warm": True,
            "truncated_future_equals_full": True,
            "formation_price_confirmed": True,
            "owners": sorted(cold),
        }
    )
Path("../c9-market-observation-verification.json").write_text(json.dumps(rows, indent=2) + "\n")
print(json.dumps(rows))
