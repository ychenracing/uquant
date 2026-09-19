import dataclasses
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.account import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine

symbols = json.loads(Path("artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json").read_text())[
    "universe"
]
results = []
for name, cost in [("offset5", 1), ("offset5-cost2", 2)]:
    with gzip.open("../diagnostic-c9-resume/" + name + ".json.gz") as stream:
        rows = json.load(stream)
    cfg = dataclasses.replace(
        DEFAULT_CONFIG,
        **{
            k: getattr(DEFAULT_CONFIG, k) * cost
            for k in ("commission_rate", "min_commission", "stamp_duty", "transfer_fee", "slippage")
        },
    )
    for checkpoint in ["2023-07-19", "2023-07-21"]:
        account = account_from_dict(
            next(r["account"] for r in rows if r["date"] == checkpoint), require_hashes=True
        )
        e = ProductionEngine("data/frozen", cfg=cfg)
        e.workspace.prepare(e.workspace.bind_tradable(symbols))
        panel = {s: e._raw[s] for s in symbols}
        cal = e._raw["sh000300"].index
        for date in cal[(cal > checkpoint) & (cal <= "2023-07-24")]:
            e.execution.execute_open(date=date, account=account, panel=panel)
            d = e.decide(symbols=symbols, as_of=str(date.date()), account=account)
            account.pending_orders = list(d.pending_orders)
            expected = next(r for r in rows if r["date"] == str(date.date()))
            actual = json.loads(json.dumps(account.to_dict(), default=str))
            decision = json.loads(json.dumps(dataclasses.asdict(d), default=str))
            assert actual == expected["account"], (
                name,
                checkpoint,
                str(date),
                "account",
                [k for k in actual if actual[k] != expected["account"][k]],
            )
            assert decision == expected["decision"], (name, checkpoint, str(date), "decision")
        results.append(
            {
                "case": name,
                "checkpoint": checkpoint,
                "end": "2023-07-24",
                "account_and_decision_identical": True,
            }
        )
Path("../c9-resume-result.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results))
