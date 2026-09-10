"""One fixed native Performance unit; never a full acceptance report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from uquant.engine import ProductionEngine, code_fingerprint
from uquant.validation import promotion as p
from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("h1_sentinel", "h1_2024", "bull"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = Path("benchmarks/promotion_baseline.json")
    data = Path("data/frozen")
    baseline_bytes, spec = p._load_spec(baseline)
    runtime = p._runtime_provenance(data)
    if runtime["data"] != spec["provenance"]["data"]:
        raise RuntimeError("fixed screen data differs from frozen baseline")
    source = code_fingerprint()
    basis = p.current_promotion_acceptance_basis()
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / f"{args.case}-result.json"
    if result_path.exists():
        raise RuntimeError("fixed screen result already exists; preserve it")
    if args.case == "h1_sentinel":
        start, end, acute = "2024-01-02", "2024-01-08", None
    elif args.case == "bull":
        bounds = p.PROTECTED_INTERVALS["bull"]
        start, end, acute = bounds["start"], bounds["end"], None
    else:
        start, end = AI_ERA_WINDOWS[args.case]
        acute = AI_ERA_ACUTE_WINDOWS[args.case]
    if not "2023-01-03" <= start <= end <= "2026-08-05":
        raise RuntimeError("fixed screen exceeds authorized historical dates")
    name = f"a/{args.case}"
    print(json.dumps({"status": "STARTED", "name": name, "runtime": runtime}), flush=True)
    raw = p._replay_promotion_unit(
        name, spec["pools"]["a"], start, end, engine=ProductionEngine(data),
        cache_dir=args.output / "native-cache", runtime=runtime, baseline_path=baseline,
        baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
        acceptance_basis=basis, data_dir=data, profile="full",
    )
    curve = raw["equity_curve"]
    peak, drawdown = 0.0, 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        drawdown = max(drawdown, 1.0 - row["equity"] / peak)
    assert math.isclose(drawdown, raw["max_drawdown"], abs_tol=1e-12)
    account = raw["final_account"]
    assert math.isclose(curve[-1]["equity"] / account["initial_cash"], raw["final_wealth"], rel_tol=1e-12)
    assert sum(order["filled_shares"] > 0 for order in account["order_ledger"]) == raw["account_orders"]
    assert raw["effective_config_sha256"] == runtime["effective_config_sha256"]
    assert code_fingerprint() == source
    metrics = p._compact(raw, acute=acute)
    failures: list[str] = []
    original: list[str] = []
    if args.case != "h1_sentinel":
        gate = p._protected_gate("bull", "a") if args.case == "bull" else p.AI_ERA_POLICY["official"][args.case]
        section = "protected" if args.case == "bull" else "cells"
        champion = spec["champion"][section][name]
        for authorized, target in ((True, failures), (False, original)):
            target.extend(p._hard_violations(name=name, metrics=metrics, gate=gate, authorized=authorized))
            target.extend(p._champion_violations(name=name, metrics=metrics, champion=champion, authorized=authorized))
    result = {
        "name": name, "interval": [start, end], "runtime": runtime,
        "economic_source_sha256": source, "metrics": metrics, "sessions": len(curve),
        "failures": failures, "original_failures": original,
        "status": "FAIL" if failures else "COMPLETE_SENTINEL" if args.case == "h1_sentinel" else "PASS_FIXED_UNIT",
        "complete_full_profile": False, "scope": "fixed_native_Performance_unit_not_full_acceptance",
        "wealth_drawdown_filled_orders_reconciled": True,
    }
    with result_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
