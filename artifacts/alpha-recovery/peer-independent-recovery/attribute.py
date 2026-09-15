"""Reconcile native diagnostic net P&L without rerunning or altering evidence."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def attribute(path):
    encoded = path.read_bytes()
    raw = json.loads(gzip.decompress(encoded))
    assert raw["status"] == "COMPLETE" and not raw["replay_error"]
    pnl, counts, exits, years = defaultdict(float), Counter(), Counter(), {}
    previous = 2_000_000.
    for item in raw["fills"]:
        fill = item["payload"]
        assert fill["signal_date"] < fill["fill_date"]
        symbol = fill["symbol"]
        assert symbol != raw["scenario"]["removed_symbol"]
        pnl[symbol] += fill["gross_value"] * (1 if fill["side"] == "SELL" else -1)
        pnl[symbol] -= sum(fill.get(k, 0) for k in ("commission", "stamp_duty", "transfer_fee"))
        counts[symbol] += 1
        if fill["side"] == "SELL":
            exits[fill["reason"]] += 1
    for row in raw["rows"]:
        book = row["decision"]["risk_summary"]["core_allocation"]
        gross = sum(item.get("held_weight", 0) for item in book["symbols"].values())
        year = years.setdefault(row["session"][:4], dict(start=previous, end=0., sessions=0, gross_sum=0., frozen=0))
        year["end"] = previous = row["equity"]
        year["sessions"] += 1
        year["gross_sum"] += gross
        year["frozen"] += bool(book["final_freeze_new_risk"])
    last = raw["rows"][-1]
    for symbol, item in last["decision"]["risk_summary"]["core_allocation"]["symbols"].items():
        pnl[symbol] += item.get("held_weight", 0) * last["equity"]
    residual = sum(pnl.values()) - (last["equity"] - 2_000_000.)
    assert abs(residual) < 1e-7, residual
    return dict(source=raw["source"], runtime=raw["runtime"], scenario=raw["scenario"],
                input_path=str(path.resolve()), input_sha256=hashlib.sha256(encoded).hexdigest(),
                reconciliation_residual=residual, metrics=raw["metrics"],
                symbol_pnl={s: dict(net_pnl=pnl[s], fills=counts[s]) for s in sorted(pnl)},
                annual={y: dict(net_wealth=v["end"]/v["start"], mean_gross=v["gross_sum"]/v["sessions"],
                                frozen_sessions=v["frozen"], sessions=v["sessions"]) for y,v in years.items()},
                exit_reasons=dict(exits))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    result = dict(kind="RECONCILED_HISTORICAL_DIAGNOSTIC_NOT_COUNTERFACTUAL_ALPHA",
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  results=[attribute(p) for p in args.inputs])
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
    print(json.dumps({r["scenario"]["removed_symbol"]: r["reconciliation_residual"] for r in result["results"]}))
