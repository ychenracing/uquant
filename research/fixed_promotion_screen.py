"""One fixed native Performance unit; never a full acceptance report."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from uquant.engine import ProductionEngine
from uquant.engine import code_fingerprint as _diagnostic_code_fingerprint
from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
from uquant.validation.promotion import (
    AI_ERA_POLICY,
    PROTECTED_INTERVALS,
    current_promotion_acceptance_basis,
)
from uquant.validation.promotion import (
    champion_violations as _champion_violations,
)
from uquant.validation.promotion import (
    compact_promotion_payload as _compact,
)
from uquant.validation.promotion import (
    hard_violations as _hard_violations,
)
from uquant.validation.promotion import (
    load_promotion_spec as _load_spec,
)
from uquant.validation.promotion import (
    protected_gate as _protected_gate,
)
from uquant.validation.promotion import (
    replay_promotion_unit as _replay_promotion_unit,
)
from uquant.validation.promotion import (
    runtime_provenance as _runtime_provenance,
)


def diagnose_promotion_unit(*, case: str, output: Path) -> dict[str, Any]:
    """Replay one bounded diagnostic; it never satisfies full promotion."""
    if case not in {"h1_sentinel", "h1_2024", "bull"}:
        raise ValueError("unregistered diagnostic promotion unit")
    baseline = Path("benchmarks/promotion_baseline.json")
    data = Path("data/frozen")
    baseline_bytes, spec = _load_spec(baseline)
    runtime = _runtime_provenance(data)
    if runtime["data"] != spec["provenance"]["data"]:
        raise RuntimeError("fixed screen data differs from frozen baseline")
    source = _diagnostic_code_fingerprint()
    basis = current_promotion_acceptance_basis()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / f"{case}-result.json"
    if result_path.exists():
        raise RuntimeError("fixed screen result already exists; preserve it")
    if case == "h1_sentinel":
        start, end, acute = "2024-01-02", "2024-01-08", None
    elif case == "bull":
        bounds = PROTECTED_INTERVALS["bull"]
        start, end, acute = bounds["start"], bounds["end"], None
    else:
        start, end = AI_ERA_WINDOWS[case]
        acute = AI_ERA_ACUTE_WINDOWS[case]
    if not "2023-01-03" <= start <= end <= "2026-08-05":
        raise RuntimeError("fixed screen exceeds authorized historical dates")
    name = f"a/{case}"
    print(json.dumps({"status": "STARTED", "name": name, "runtime": runtime}), flush=True)
    raw = _replay_promotion_unit(
        name, spec["pools"]["a"], start, end, engine=ProductionEngine(data),
        cache_dir=output / "native-cache", runtime=runtime, baseline_path=baseline,
        baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
        acceptance_basis=basis, data_dir=data, profile="full",
    )
    curve = raw["equity_curve"]
    peak, drawdown = 0.0, 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        drawdown = max(drawdown, 1.0 - row["equity"] / peak)
    if not (math.isclose(drawdown, raw["max_drawdown"], abs_tol=1e-12)):
        raise RuntimeError("Evidence validation failed: math.isclose(drawdown, raw['max_drawdown'], abs_tol=1e-12)")
    account = raw["final_account"]
    if not (math.isclose(curve[-1]["equity"] / account["initial_cash"], raw["final_wealth"], rel_tol=1e-12)):
        raise RuntimeError("Evidence validation failed: math.isclose(curve[-1]['equity'] / account['initial_cash'], raw['final_wealth'], rel_tol=1e-12)")
    if not (sum(order["filled_shares"] > 0 for order in account["order_ledger"]) == raw["account_orders"]):
        raise RuntimeError("Evidence validation failed: sum((order['filled_shares'] > 0 for order in account['order_ledger'])) == raw['account_orders']")
    if not (raw["effective_config_sha256"] == runtime["effective_config_sha256"]):
        raise RuntimeError("Evidence validation failed: raw['effective_config_sha256'] == runtime['effective_config_sha256']")
    if not (_diagnostic_code_fingerprint() == source):
        raise RuntimeError('Evidence validation failed: code_fingerprint() == source')
    metrics = _compact(raw, acute=acute)
    failures: list[str] = []
    original: list[str] = []
    if case != "h1_sentinel":
        gate = _protected_gate("bull", "a") if case == "bull" else AI_ERA_POLICY["official"][case]
        section = "protected" if case == "bull" else "cells"
        champion = spec["champion"][section][name]
        for authorized, target in ((True, failures), (False, original)):
            target.extend(_hard_violations(name=name, metrics=metrics, gate=gate, authorized=authorized))
            target.extend(_champion_violations(name=name, metrics=metrics, champion=champion, authorized=authorized))
    result = {
        "name": name, "interval": [start, end], "runtime": runtime,
        "economic_source_sha256": source, "metrics": metrics, "sessions": len(curve),
        "failures": failures, "original_failures": original,
        "status": "FAIL" if failures else "COMPLETE_SENTINEL" if case == "h1_sentinel" else "PASS_FIXED_UNIT",
        "complete_full_profile": False, "scope": "fixed_native_Performance_unit_not_full_acceptance",
        "wealth_drawdown_filled_orders_reconciled": True,
    }
    with result_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("h1_sentinel", "h1_2024", "bull"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose_promotion_unit(case=args.case, output=args.output)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
