import dataclasses
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.types import AccountState

symbols = json.loads(Path("artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json").read_text())[
    "universe"
]
for start in ["2023-01-03", "2023-01-10"]:
    e = ProductionEngine("data/frozen")
    e.workspace.prepare(e.workspace.bind_tradable(symbols))
    a = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    rows = []
    panel = {s: e._raw[s] for s in symbols}
    cal = e._raw["sh000300"].index
    for date in cal[(cal >= start) & (cal <= "2023-01-31")]:
        e.execution.execute_open(date=date, account=a, panel=panel)
        d = e.decide(symbols=symbols, as_of=str(date.date()), account=a)
        a.pending_orders = list(d.pending_orders)
        rows.append({"date": str(date.date()), "account": a.to_dict(), "decision": dataclasses.asdict(d)})
    out = Path("../diagnostic-c4c")
    out.mkdir(exist_ok=True)
    with gzip.open(out / (start + ".json.gz"), "wt") as f:
        json.dump(rows, f, default=str)
    print(start, "done", flush=True)
