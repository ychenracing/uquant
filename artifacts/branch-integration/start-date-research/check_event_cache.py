import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path.cwd()))
from uquant.application.decision import _recent_reversal_observations
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine

symbols = json.loads(Path("artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json").read_text())[
    "universe"
]
e = ProductionEngine("data/frozen")
e.workspace.prepare(e.workspace.bind_tradable(symbols))
panel = {s: f for s, f in e._features.items() if s not in ["sh000300", "sh000682"]}
m = SimpleNamespace(
    qualification_reference_panel=panel, user_panel={}, tech=e._features["sh000682"], cfg=DEFAULT_CONFIG
)
rows = []
for day in ["2023-01-04", "2023-01-10", "2023-01-19"]:
    i = SimpleNamespace(date=pd.Timestamp(day))
    e._reversal_observation_cache.clear()
    t = time.monotonic()
    cold = _recent_reversal_observations(e, i, m)
    cold_seconds = time.monotonic() - t
    t = time.monotonic()
    warm = _recent_reversal_observations(e, i, m)
    warm_seconds = time.monotonic() - t
    assert cold == warm
    e._reversal_observation_cache.clear()
    e._leader_score_cache.clear()
    past = SimpleNamespace(
        qualification_reference_panel={s: f.loc[:day].copy() for s, f in panel.items()},
        user_panel={},
        tech=m.tech.loc[:day].copy(),
        cfg=m.cfg,
    )
    bounded = _recent_reversal_observations(e, i, past)
    assert bounded == cold
    rows.append(
        {
            "date": day,
            "cold_equals_warm": True,
            "truncated_future_equals_full": True,
            "cold_seconds": cold_seconds,
            "warm_seconds": warm_seconds,
        }
    )
Path("../event-cache-verification.json").write_text(json.dumps(rows, indent=2))
print(json.dumps(rows))
