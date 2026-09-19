import dataclasses
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.account import account_from_dict
from uquant.engine import ProductionEngine

symbols = json.loads(Path("artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json").read_text())[
    "universe"
]
with gzip.open("../diagnostic-c7-failures/offset5.json.gz") as stream:
    rows = json.load(stream)
results = []
for checkpoint in ["2023-07-19", "2023-07-21"]:
    original = next(r for r in rows if r["date"] == checkpoint)
    account = account_from_dict(original["account"], require_hashes=True)
    engine = ProductionEngine("data/frozen")
    engine.workspace.prepare(engine.workspace.bind_tradable(symbols))
    panel = {s: engine._raw[s] for s in symbols}
    calendar = engine._raw["sh000300"].index
    for date in calendar[(calendar > checkpoint) & (calendar <= "2023-07-24")]:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
        expected = next(r for r in rows if r["date"] == str(date.date()))
        actual_account = json.loads(json.dumps(account.to_dict(), default=str))
        actual_decision = json.loads(json.dumps(dataclasses.asdict(decision), default=str))
        assert actual_account == expected["account"], (
            checkpoint,
            str(date),
            "account",
            [k for k in actual_account if actual_account[k] != expected["account"][k]],
        )
        assert actual_decision == expected["decision"], (checkpoint, str(date), "decision")
    results.append({"checkpoint": checkpoint, "end": "2023-07-24", "account_and_decision_identical": True})
Path("../c7-resume-result.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results))
