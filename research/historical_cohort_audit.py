"""Read completed native cohort evidence; never create simulated trade profits."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from uquant.contracts.strict_json import canonical_json_bytes


def inspect(path: Path) -> dict[str, Any]:
    result = json.loads((path / "result.json").read_text())
    readback = json.loads((path / "readback.json").read_text())
    unsigned = {key: value for key, value in result.items() if key != "canonical_sha256"}
    if (hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest() != result["canonical_sha256"]
            or result["status"] != "COMPLETE" or result["sessions"] != 869
            or readback.get("native_readback") is not True
            or readback["result_sha256"] != result["canonical_sha256"]):
        raise ValueError("complete sealed native readback required")
    for filename, key in (("observations.jsonl.gz", "raw_sha256"),
                          ("final_account.json", "final_account_sha256")):
        if hashlib.file_digest((path / filename).open("rb"), "sha256").hexdigest() != result[key]:
            raise ValueError("native evidence bytes changed")
    counts: Counter[str] = Counter()
    gates: Counter[str] = Counter()
    repair: Counter[str] = Counter()
    years = {}
    with gzip.open(path / "observations.jsonl.gz", "rt") as stream:
        for line in stream:
            row = json.loads(line)
            held = {symbol for symbol, position in row["state"]["positions"].items()
                    if position["shares"] > 0}
            core = row["observation"]["risk_assessment"]["evidence"].get("core_allocation", {})
            symbols = core.get("symbols", {})
            ready = [symbol for symbol, entry in symbols.items()
                     if symbol not in held and entry.get("entry", {}).get("block") == "READY"]
            counts["sessions"] += 1
            if not held:
                counts["flat_days"] += 1
                counts["flat_ready_days"] += bool(ready)
                counts["flat_ready_unfrozen_days"] += bool(ready) and not core.get("freeze_new_risk", False)
                repair[row["state"]["flat_book_capital_repair"]["status"]] += 1
                for entry in symbols.values():
                    key = str(entry.get("entry", {}).get("block")) + ":" + str(entry.get("entry_gate"))
                    gates[key] += 1
            years[row["date"][:4]] = {"date": row["date"], "equity": row["equity"]}
    if counts["sessions"] != result["sessions"]:
        raise ValueError("observation count differs from native result")
    metrics = result["metrics"]
    pnl = readback["verified_symbol_pnl"]
    net = result["identity"]["initial_cash"] * (metrics["final_wealth"] - 1)
    if not math.isclose(sum(pnl.values()), net, abs_tol=.01):
        raise ValueError("native symbol profit does not reconcile")
    keys = ("final_wealth", "max_drawdown", "account_orders", "fees", "slippage_cost",
            "benchmark_total_return", "gross_turnover", "annual_turnover")
    return {"source": result["identity"]["source_sha256"], "identity": result["identity"],
            "native_result_sha256": result["canonical_sha256"],
            "metrics": {key: metrics[key] for key in keys}, "net_profit": net,
            "symbol_pnl": pnl, "counts": dict(counts), "flat_entry_blocks": dict(gates),
            "flat_repair_status": dict(repair), "period_end_equity": years,
            "actual_orders": metrics["order_ledger"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--simple", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    left, right = inspect(args.main), inspect(args.simple)
    for key in ("data", "research_universe_sha256", "config_sha256", "effective_config", "runtime",
                "start", "end", "initial_cash", "runner_sha256", "session_dates"):
        if left["identity"][key] != right["identity"][key]:
            raise ValueError("paired input/runtime/execution identity differs: " + key)
    symbols = set(left["symbol_pnl"]) | set(right["symbol_pnl"])
    differences = {symbol: right["symbol_pnl"].get(symbol, 0) - left["symbol_pnl"].get(symbol, 0)
                   for symbol in sorted(symbols)}
    if not math.isclose(sum(differences.values()), right["net_profit"] - left["net_profit"], abs_tol=.01):
        raise ValueError("paired profit attribution does not reconcile")
    output = {"research_only": True, "goal": "NOT_MET", "main": left, "simple": right,
              "simple_minus_main_symbol_pnl": differences,
              "decision": "RETAIN_MAIN_REJECT_SIMPLE_REPLACEMENT",
              "boundary": "Fixed dated23-company entry cohort; endpoint-affine research data, not causal raw-share execution or fresh temporal OOS. Counts are overlapping observations, not independent opportunities or profits.",
              "audit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"main": left["metrics"], "simple": right["metrics"], "decision": output["decision"]}))


if __name__ == "__main__":
    main()
