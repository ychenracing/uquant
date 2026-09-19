import concurrent.futures
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


def run(case):
    name, cost, weak = case
    cfg = dataclasses.replace(
        DEFAULT_CONFIG,
        **{
            k: getattr(DEFAULT_CONFIG, k) * cost
            for k in ("commission_rate", "min_commission", "stamp_duty", "transfer_fee", "slippage")
        },
    )
    ss = [s for s in symbols if not weak or s != "sz300308"]
    e = ProductionEngine("data/frozen", cfg=cfg)
    e.workspace.prepare(e.workspace.bind_tradable(ss))
    a = AccountState.empty(cfg.initial_cash)
    rows = []
    panel = {s: e._raw[s] for s in ss}
    cal = e._raw["sh000300"].index
    start, end = ("2023-01-03", "2023-01-06") if weak else ("2023-01-10", "2023-07-24")
    for date in cal[(cal >= start) & (cal <= end)]:
        e.execution.execute_open(date=date, account=a, panel=panel)
        d = e.decide(symbols=ss, as_of=str(date.date()), account=a)
        a.pending_orders = list(d.pending_orders)
        if weak or str(date.date()) >= "2023-07-17":
            rows.append({"date": str(date.date()), "account": a.to_dict(), "decision": dataclasses.asdict(d)})
    out = Path("../diagnostic-c9-resume")
    out.mkdir(exist_ok=True)
    with gzip.open(out / (name + ".json.gz"), "wt") as f:
        json.dump(rows, f, default=str)
    print(name, "done", flush=True)


with concurrent.futures.ProcessPoolExecutor(max_workers=2) as p:
    list(p.map(run, [("offset5", 1, False), ("offset5-cost2", 2, False)]))
