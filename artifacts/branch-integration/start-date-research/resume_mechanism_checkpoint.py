import argparse
import gzip
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.account import account_from_dict, load_account, migrate_code_identity, save_account
from uquant.account.economic_identity import economic_state_sha256
from uquant.engine import ProductionEngine, code_fingerprint

p = argparse.ArgumentParser()
p.add_argument("--case", required=True)
p.add_argument("--label", required=True)
args = p.parse_args()

root = Path("../mechanism-checkpoints")
meta = json.loads((root / (args.case + "-prefix.json")).read_text())
source = root / (args.case + "-prefix-account.json")
account = load_account(source)
before = economic_state_sha256(account)
target = root / (args.label + "-" + args.case + "-start-account.json")
if account.code_hash != code_fingerprint():
    account = migrate_code_identity(
        source, target, new_code_hash=code_fingerprint(), acknowledge_code_change=True
    )
else:
    save_account(account, target)
assert before == economic_state_sha256(account)
with gzip.open(Path("../mechanism-runs") / args.label / (args.case + ".json.gz")) as stream:
    expected = json.load(stream)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert head == expected["source_head"]
e = ProductionEngine("data/frozen")
symbols = meta["symbols"]
e.workspace.prepare(e.workspace.bind_tradable(symbols))
panel = {s: e._raw[s] for s in symbols}
cal = e.workspace.common_sessions(*e.workspace.universe.index_symbols)
curve = list(meta["equity_curve"])
days = []
for date in cal[(cal > meta["checkpoint"]) & (cal <= meta["end"])]:
    e.execution.execute_open(date=date, account=account, panel=panel)
    curve.append({"date": str(date.date()), "equity": e.equity(account, date)})
    d = e.decide(symbols=symbols, as_of=str(date.date()), account=account)
    account.pending_orders = list(d.pending_orders)
    days.append(
        {
            "date": str(date.date()),
            "cash": account.cash,
            "position_shares": {s: pos.shares for s, pos in account.positions.items() if pos.shares > 0},
        }
    )
assert len(curve) == len(expected["result"]["equity_curve"])
for a, b in zip(curve, expected["result"]["equity_curve"], strict=True):
    assert a["date"] == b["date"] and math.isclose(a["equity"], b["equity"], rel_tol=1e-12, abs_tol=1e-7), (
        args.label,
        a,
        b,
    )
expected_days = {r["date"]: r for r in expected["result"]["daily_replay_evidence"]}
for row in days:
    b = expected_days[row["date"]]
    assert row["cash"] == b["cash"]
    assert row["position_shares"] == {s: q for s, q in b["position_shares"].items() if q > 0}
account_from_dict(account.to_dict(), require_hashes=True)
save_account(account, root / (args.label + "-" + args.case + "-final-account.json"))
facts = {
    "case": args.case,
    "label": args.label,
    "prefix_source": meta["source_head"],
    "suffix_source": head,
    "checkpoint": meta["checkpoint"],
    "prefix_account_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "economic_sha256_before_migration": before,
    "economic_sha256_after_migration": economic_state_sha256(load_account(target)),
    "all_equity_cash_positions_match_full_native_pair": True,
    "sessions": len(curve),
    "W": curve[-1]["equity"] / account.initial_cash,
    "final_account_strict_decode": True,
}
(root / (args.label + "-" + args.case + "-verification.json")).write_text(json.dumps(facts, indent=2) + "\n")
print(json.dumps(facts), flush=True)
