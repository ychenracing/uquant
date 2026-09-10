"""Compare the three preregistered repaired-input native accounts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from research.historical_cohort_audit import inspect
from uquant.validation.acceptance_tolerance import acceptance_revision, order_ceiling


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    expected = {"full": 31.449725716864066, "remove_all_three": 1.8793222919467352,
                "strict": 2.537405639171974}
    for case, old_wealth in expected.items():
        left = inspect(args.runs / f"original-main-{case}")
        right = inspect(args.runs / f"original-slow-{case}")
        left_identity = {key: value for key, value in left["identity"].items()
                         if key not in ("source_sha256", "commit")}
        right_identity = {key: value for key, value in right["identity"].items()
                          if key not in ("source_sha256", "commit")}
        if left_identity != right_identity:
            raise ValueError("paired non-producer identity differs: " + case)
        if not math.isclose(left["metrics"]["final_wealth"], old_wealth, rel_tol=0, abs_tol=1e-12):
            raise ValueError("new main control does not reproduce prior repaired-input wealth")
        symbols = set(left["symbol_pnl"]) | set(right["symbol_pnl"])
        delta = {symbol: right["symbol_pnl"].get(symbol, 0) - left["symbol_pnl"].get(symbol, 0)
                 for symbol in sorted(symbols)}
        if not math.isclose(sum(delta.values()), right["net_profit"] - left["net_profit"], abs_tol=.01):
            raise ValueError("paired profit does not reconcile")
        rows.append({"case": case, "control": left, "candidate": right,
                     "candidate_minus_control_symbol_pnl": delta,
                     "wealth_difference": right["metrics"]["final_wealth"] - left["metrics"]["final_wealth"],
                     "drawdown_difference": right["metrics"]["max_drawdown"] - left["metrics"]["max_drawdown"],
                     "order_difference": right["metrics"]["account_orders"] - left["metrics"]["account_orders"],
                     "continuous_order_ceiling_reference": order_ceiling(20),
                     "candidate_exceeds_order_reference": right["metrics"]["account_orders"] > order_ceiling(20)})
    output = {"goal": "NOT_MET", "research_only": True, "cases": rows,
              "acceptance_revision": acceptance_revision(),
              "boundary": "Repaired versioned research input; no formal promotion judgment. Strict is a diagnostic removal, not a separately authorized numeric-gate waiver. All original failures and intended old-policy test incompatibility remain.",
              "audit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps([{key: row[key] for key in ("case", "wealth_difference", "drawdown_difference",
                                               "order_difference", "candidate_exceeds_order_reference")}
                      for row in rows]))


if __name__ == "__main__":
    main()
