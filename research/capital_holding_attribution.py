"""Exact accounting attribution; hypothetical mixes are not account simulations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

from research.cross_ai_acceptance import read_case

SOURCES = {
    "C": "b928db51ea6a7bbdfbe79588408717241200d44b3d98b2c39d7668b28e90259d",
    "simple": "b7f54f137576ba15787827103103d4710369d105519c463f4289b1251dbf2942",
    "S": "834e59d27552d80ce4804371027b2b5041b5fbaf2e30b1121371282e76e6b017",
    "H": "a69bd9264e0f2a2e7f53c002c7b6cee36640ffa2b2ed4839fdfbf58529c5c513",
}


def value(state, sale_price, terminal_price):
    quantity, purchase_price, sold_fraction = state
    return quantity * (sold_fraction * sale_price + (1 - sold_fraction) * terminal_price - purchase_price)


def shapley(before, after, sale_price, terminal_price):
    contributions = [0.0] * 3
    for order in itertools.permutations(range(3)):
        state = list(before)
        for index in order:
            old = value(state, sale_price, terminal_price)
            state[index] = after[index]
            contributions[index] += (value(state, sale_price, terminal_price) - old) / 6
    return dict(zip(("quantity", "purchase_price", "sold_fraction"), contributions, strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--simple", type=Path, required=True)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results, accounts = {}, {}
    for name, root in (("C", args.baseline), ("simple", args.simple), ("S", args.arms / "S"), ("H", args.arms / "H")):
        read_case(root, case="no_optical", interval=["2023-01-03", "2023-06-30"], source=SOURCES[name])
        sealed = json.loads((root / "result.json").read_text())
        accounts[name] = json.loads((root / "final_account.json").read_text())
        results[name] = {"source": SOURCES[name], "seal": sealed["canonical_sha256"],
                         "raw_sha256": sealed["raw_sha256"], "native_readback": True,
                         "metrics": sealed["metrics"],
                         "pnl": {s: x["total_pnl"] for s, x in sealed["attribution"]["by_symbol"].items()},
                         "fills": [{k: f[k] for k in ("symbol", "side", "shares", "signal_date", "fill_date", "price", "gross_value", "commission", "stamp_duty", "transfer_fee", "mechanism")} for f in accounts[name]["fills"]]}
    path = Path("data/frozen/sh688256.csv")
    with path.open() as stream:
        terminal = float(next(r for r in csv.DictReader(stream) if r["date"] == "2023-06-30")["close"])
    states, fees = {}, {}
    sale_prices = []
    for name in ("C", "simple"):
        fills = [f for f in accounts[name]["fills"] if f["symbol"] == "sh688256"]
        buys, sells = ([f for f in fills if f["side"] == side] for side in ("BUY", "SELL"))
        assert len(buys) == len(sells) == 1
        buy, sell = buys[0], sells[0]
        assert sell["fill_date"] == "2023-05-08" and sell["mechanism"] == "CRISIS"
        assert buy["shares"] - sell["shares"] == accounts[name]["positions"]["sh688256"]["shares"]
        states[name] = (buy["shares"], buy["price"], sell["shares"] / buy["shares"])
        fees[name] = sum(f[k] for f in fills for k in ("commission", "stamp_duty", "transfer_fee"))
        sale_prices.append(sell["price"])
        assert math.isclose(value(states[name], sell["price"], terminal) - fees[name], results[name]["pnl"]["sh688256"], abs_tol=1e-6)
    assert sale_prices[0] == sale_prices[1]
    decomposition = shapley(states["C"], states["simple"], sale_prices[0], terminal)
    decomposition["cash_fees"] = fees["C"] - fees["simple"]
    delta = results["simple"]["pnl"]["sh688256"] - results["C"]["pnl"]["sh688256"]
    assert math.isclose(sum(decomposition.values()), delta, abs_tol=1e-6)
    for name in ("simple", "S", "H"):
        base = "C" if name == "simple" else "simple"
        results[name]["pnl_difference"] = {s: results[name]["pnl"].get(s, 0) - results[base]["pnl"].get(s, 0) for s in sorted(set(results[name]["pnl"]) | set(results[base]["pnl"]))}
        assert math.isclose(sum(results[name]["pnl_difference"].values()), 2_000_000 * (results[name]["metrics"]["final_wealth"] - results[base]["metrics"]["final_wealth"]), abs_tol=1e-6)
    output = {"diagnostic_only": True, "paths": results, "sh688256_accounting": {
        "states": states, "sale_price": sale_prices[0], "terminal_price": terminal,
        "price_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "contributions": decomposition,
        "net_delta": delta, "not_an_executable_counterfactual": True}}
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"decomposition": decomposition, "net_delta": delta}))


if __name__ == "__main__":
    main()
