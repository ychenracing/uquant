"""Replay the exact archived phase-one producer without changing its modules.

Run with PYTHONPATH and cwd set to the restored 9bb5842 checkout. This runner
records historical controls, not current-candidate acceptance or fresh OOS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from uquant.engine import ProductionEngine, code_fingerprint
from uquant.validation.promotion import _compact, _runtime_provenance

REFERENCE_COMMIT = "9bb58420365b471ee11b4cdfe31793008233ad50"
WINDOWS = {
    "sentinel": ("2024-01-02", "2024-02-29", None),
    "bull": ("2025-04-01", "2026-06-30", None),
    "h1_2024": ("2024-01-02", "2024-07-01", ("2024-01-03", "2024-02-02")),
}


def write_json(path: Path, value: object) -> None:
    """Publish once; preserve any existing evidence."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, allow_nan=False)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=WINDOWS, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline_bytes = args.baseline.read_bytes()
    baseline = json.loads(baseline_bytes)
    before = _runtime_provenance(args.data)
    assert before["production"]["commit"] == REFERENCE_COMMIT
    assert baseline["champion"]["production_commit"] == REFERENCE_COMMIT
    assert before["data"] == baseline["provenance"]["data"]
    start, end, acute = WINDOWS[args.case]
    identity = {
        "scope": "exact_historical_native_control_not_current_acceptance",
        "runtime": before,
        "legacy_engine_code_fingerprint": code_fingerprint(),
        "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "symbols": baseline["pools"]["a"],
        "interval": [start, end],
        "case": args.case,
    }
    write_json(args.output / "identity.json", identity)
    print(json.dumps({"status": "STARTED", **identity}), flush=True)
    raw = ProductionEngine(args.data).backtest(
        symbols=baseline["pools"]["a"], start=start, end=end,
    )
    write_json(args.output / "native_raw.json", raw)
    assert _runtime_provenance(args.data) == before
    assert args.baseline.read_bytes() == baseline_bytes
    assert code_fingerprint() == identity["legacy_engine_code_fingerprint"]
    assert raw["effective_config_sha256"] == before["effective_config_sha256"]
    curve = raw["equity_curve"]
    cash = raw["final_account"]["initial_cash"]
    peak = 0.0
    drawdown = 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = max(drawdown, 1.0 - point["equity"] / peak)
    assert math.isclose(curve[-1]["equity"] / cash, raw["final_wealth"], rel_tol=1e-12)
    assert math.isclose(drawdown, raw["max_drawdown"], abs_tol=1e-12)
    ledger = raw["final_account"]["order_ledger"]
    assert sum(order["filled_shares"] > 0 for order in ledger) == raw["account_orders"]
    metrics = _compact(raw, acute=acute)
    expected = None
    differences = {}
    if args.case != "sentinel":
        section = "protected" if args.case == "bull" else "cells"
        expected = baseline["champion"][section][f"a/{args.case}"]
        differences = {
            key: {"expected": value, "actual": metrics[key]}
            for key, value in expected.items()
            if value is not None and not math.isclose(metrics[key], value, rel_tol=1e-9, abs_tol=1e-9)
        }
    result = {
        **identity, "status": "COMPLETE", "sessions": len(curve),
        "metrics": metrics, "expected": expected, "differences": differences,
        "raw_sha256": hashlib.sha256((args.output / "native_raw.json").read_bytes()).hexdigest(),
        "readback": {"source_data_config_runtime_unchanged": True,
                     "wealth_drawdown_filled_orders_reconciled": True},
    }
    write_json(args.output / "result.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
