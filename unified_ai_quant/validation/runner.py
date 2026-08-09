"""Strict acceptance runner: every report item must have executable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import DEFAULT_CONFIG
from ..engine import INDEX_SYMBOLS, ProductionEngine, code_fingerprint
from ..leader import REFERENCE_UNIVERSE
from .comparison import (
    bounded_performance,
    false_risk_off_events,
    false_risk_state_diagnostics,
    lead_to_target,
    market_drawdown_target,
    mature_false_exit_regrets,
    recovery_capture,
    recovery_delay_opportunity_cost,
    replacement_spreads,
    risk_action_dates,
)
from .provenance import bounded_data_fingerprint, validation_fingerprint
from .robustness import artifact_is_current as robustness_is_current
from .robustness import promotion_holdback_status, run_robustness
from .stress import artifact_is_current as stress_is_current
from .stress import run_stress
from .universes import FIXED_POOL_SIZES, POOLS, PRIMARY_POOLS

WINDOWS: dict[str, tuple[str, str]] = {
    "bear_2018": ("2018-01-02", "2018-12-28"),
    "crash_2020": ("2020-01-02", "2020-12-31"),
    "rotation_2021": ("2021-01-04", "2021-12-31"),
    "bear_2022": ("2022-01-04", "2022-12-30"),
    "mixed_2023": ("2023-01-03", "2023-12-29"),
    "choppy_2024": ("2024-01-02", "2024-12-31"),
    "bull": ("2025-04-01", "2026-06-30"),
    "through_july": ("2025-04-01", "2026-07-20"),
    "continuous_full": ("2018-01-02", "2026-07-20"),
}
SYSTEMS = ("qwenquant", "aquant", "trade")
MATURE_WINNERS = {"sz300308", "sz300502", "sz300394"}
RISK_WINDOWS = {
    "bear_2018": ("2018-01-02", "2018-12-28"),
    "crash_2020": ("2020-01-02", "2020-12-31"),
    "bear_2022": ("2022-01-04", "2022-12-30"),
    "through_july": ("2026-06-30", "2026-07-20"),
}


@dataclass(frozen=True, slots=True)
class Result:
    id: str
    status: str
    actual: Any
    threshold: Any
    evidence: str


def _result(
    identifier: str, passed: bool, actual: Any, threshold: Any, evidence: str
) -> Result:
    return Result(identifier, "PASS" if passed else "FAIL", actual, threshold, evidence)


def _missing(identifier: str, threshold: str, reason: str) -> Result:
    return _result(
        identifier,
        False,
        {"evaluated": False, "reason": reason},
        threshold,
        reason,
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _python_source_hash(root: Path) -> str:
    if not root.is_dir():
        raise RuntimeError(f"frozen benchmark source is missing: {root}")
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.py"))
    if not paths:
        raise RuntimeError(f"frozen benchmark source has no Python files: {root}")
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _validation_hash(root: Path) -> str:
    return validation_fingerprint(root)


def _evidence_input_hashes(
    root: Path,
    data_dir: Path,
    legacy_path: Path,
) -> dict[str, str]:
    """Fingerprint every immutable input used by a long acceptance run.

    A validation result is not auditable when production code, validation code,
    frozen prices, or a locked benchmark changes while worker processes are
    still replaying.  Capture the complete input identity before the run and
    compare it again before promotion or report generation.
    """
    all_symbols = set().union(*map(set, POOLS.values()))
    manifest = ProductionEngine(data_dir).data.manifest(
        all_symbols | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
    )
    frozen_root = root.parent / "frozen_benchmarks"
    return {
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": _validation_hash(root),
        "data_sha256": manifest.digest,
        "data_manifest_sha256": _file_hash(data_dir / "DATA_MANIFEST.json"),
        "implementation_spec_sha256": _file_hash(
            root / "docs" / "IMPLEMENTATION_SPEC.md"
        ),
        "acceptance_spec_sha256": _file_hash(
            root / "docs" / "ACCEPTANCE_SPEC.md"
        ),
        "benchmark_lock_sha256": _file_hash(
            root / "benchmarks" / "BENCHMARK_LOCK.json"
        ),
        "legacy_common_adapter_sha256": _file_hash(legacy_path),
        "trade_common_stress_sha256": _file_hash(
            root / "benchmarks" / "trade_common_stress.json"
        ),
        **{
            f"frozen_{system}_source_sha256": _python_source_hash(
                frozen_root / system
            )
            for system in SYSTEMS
        },
        "promotion_holdback_lock_sha256": _file_hash(
            root / "benchmarks" / "PROMOTION_HOLDBACK.json"
        ),
    }


def _artifact_evidence_hashes(
    root: Path,
    *,
    stress_loaded: bool,
    robustness_loaded: bool,
) -> dict[str, str]:
    return {
        "stress_results_sha256": (
            _file_hash(root / "stress_results.json")
            if stress_loaded
            else "STALE_OR_MISSING"
        ),
        "robustness_results_sha256": (
            _file_hash(root / "robustness_results.json")
            if robustness_loaded
            else "STALE_OR_MISSING"
        ),
    }


def _assert_evidence_inputs_unchanged(
    expected: dict[str, str],
    *,
    root: Path,
    data_dir: Path,
    legacy_path: Path,
) -> None:
    current = _evidence_input_hashes(root, data_dir, legacy_path)
    changed = {
        name: {"before": expected.get(name), "after": current.get(name)}
        for name in sorted(set(expected) | set(current))
        if expected.get(name) != current.get(name)
    }
    if changed:
        raise RuntimeError(
            "acceptance inputs changed while replays were running; "
            f"discarding mixed-version evidence: {changed}"
        )


def _assert_artifact_evidence_unchanged(
    expected: dict[str, str],
    *,
    root: Path,
    stress_loaded: bool,
    robustness_loaded: bool,
) -> None:
    current = _artifact_evidence_hashes(
        root,
        stress_loaded=stress_loaded,
        robustness_loaded=robustness_loaded,
    )
    if current != expected:
        raise RuntimeError(
            "acceptance artifacts changed after validation; refusing "
            f"mixed-version evidence: before={expected}, after={current}"
        )


def _pytest(root: Path) -> tuple[bool, str, set[str]]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-vv"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    names: set[str] = set()
    for line in collected.stdout.splitlines():
        item = line.strip()
        if item.startswith("<Function test_") and item.endswith(">"):
            names.add(item.removeprefix("<Function ").removesuffix(">").split("[")[0])
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output[-2000:], names


def _test_result(
    identifier: str,
    required: tuple[str, ...],
    tests_ok: bool,
    test_names: set[str],
    test_output: str,
    evidence: str,
) -> Result:
    missing = sorted(set(required) - test_names)
    return _result(
        identifier,
        tests_ok and not missing,
        {"suite": test_output, "required": list(required), "missing": missing},
        "all named contract tests collected and suite passes",
        evidence,
    )


def _matrix_row(result: dict[str, Any]) -> dict[str, Any]:
    account = result["final_account"]
    keys = (
        "start", "end", "final_wealth", "total_return", "cagr",
        "benchmark_total_return", "excess_return", "max_drawdown",
        "rolling_drawdown_p95", "max_drawdown_duration", "peak_to_recovery_days",
        "account_orders", "round_trips", "gross_turnover", "annual_turnover",
        "median_holding_days", "fees", "slippage_cost", "sharpe", "calmar",
        "worst_20d", "worst_60d", "first_caution", "first_risk_off",
        "first_reduce", "lead_to_10pct_dd", "lead_to_15pct_dd", "risk_events",
        "order_ledger", "internal_events", "daily_risk_states", "equity_curve",
        "attribution",
    )
    return {
        **{key: result[key] for key in keys},
        "fills": account["fills"],
        "replacement_events": account["replacement_events"],
        "lifecycle_events": account["lifecycle_events"],
        "final_dynamic_k": account["dynamic_k"],
    }


def _run_pool_matrix(task: tuple[str, tuple[str, ...], str]) -> tuple[str, dict[str, Any]]:
    pool, symbols, data_dir = task
    engine = ProductionEngine(data_dir)
    rows: dict[str, dict[str, Any]] = {}
    for window, (start, end) in WINDOWS.items():
        try:
            rows[window] = _matrix_row(
                engine.backtest(symbols=symbols, start=start, end=end)
            )
        except Exception as exc:
            raise RuntimeError(
                f"primary replay failed for pool={pool}, window={window}, "
                f"start={start}, end={end}: {exc}"
            ) from exc
    return pool, rows


def _matrix(data_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    tasks = [(pool, symbols, str(data_dir)) for pool, symbols in POOLS.items()]
    workers = min(4, max(1, os.cpu_count() or 1), len(tasks))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(_run_pool_matrix, tasks, chunksize=1))
    return dict(rows)


def _risk_disabled_bull(data_dir: Path) -> dict[str, dict[str, Any]]:
    config = DEFAULT_CONFIG.override(risk_overlay_enabled=False)
    output: dict[str, dict[str, Any]] = {}
    for pool, symbols in PRIMARY_POOLS.items():
        result = ProductionEngine(data_dir, config).backtest(
            symbols=symbols,
            start=WINDOWS["bull"][0],
            end=WINDOWS["bull"][1],
        )
        output[pool] = {
            "final_wealth": result["final_wealth"],
            "max_drawdown": result["max_drawdown"],
            "account_orders": result["account_orders"],
            "risk_events": result["risk_events"],
        }
    return output


def _public_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "final_wealth", "total_return", "max_drawdown", "account_orders",
            "sharpe", "calmar", "worst_20d", "worst_60d",
        )
    }


def _primary_cell_comparison(
    new: dict[str, Any],
    olds: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Report the three section-2 metrics independently for one formal cell.

    The final replacement rule in section 24 applies these booleans as three
    separate coverage rates, alongside the per-cell dominance test.  It does
    not combine extrema from different legacy systems into an additional
    conjunctive per-cell gate.
    """
    best_wealth = max(float(row["final_wealth"]) for row in olds.values())
    best_dd = min(float(row["max_drawdown"]) for row in olds.values())
    least_orders = min(int(row["account_orders"]) for row in olds.values())
    allowed_orders = least_orders + max(2, math.ceil(0.05 * least_orders))
    return {
        "near_best_return": float(new["final_wealth"]) >= 0.99 * best_wealth,
        "near_best_dd": float(new["max_drawdown"]) <= best_dd + 0.005,
        "near_best_orders": int(new["account_orders"]) <= allowed_orders,
        "best_old_wealth": best_wealth,
        "best_old_drawdown": best_dd,
        "least_old_orders": least_orders,
        "allowed_orders": allowed_orders,
    }


def _formal_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """All acceptance-required metrics, excluding only the raw daily curve."""
    return {
        key: row.get(key)
        for key in (
            "final_wealth",
            "total_return",
            "cagr",
            "benchmark_total_return",
            "excess_return",
            "calmar",
            "sharpe",
            "max_drawdown",
            "rolling_drawdown_p95",
            "max_drawdown_duration",
            "worst_20d",
            "worst_60d",
            "peak_to_recovery_days",
            "account_orders",
            "round_trips",
            "gross_turnover",
            "annual_turnover",
            "median_holding_days",
            "fees",
            "slippage_cost",
            "first_caution",
            "first_risk_off",
            "first_reduce",
            "lead_to_10pct_dd",
            "lead_to_15pct_dd",
            "internal_events",
            "risk_events",
            "order_ledger",
            "daily_risk_states",
            "attribution",
        )
    }


def _formal_cell_metrics(
    row: dict[str, Any],
    *,
    data_dir: Path,
    window: str,
    tech_close: pd.Series,
    bull_opportunity_cost: float | None,
    recovery_delay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Materialize every section-6 metric for one formal primary cell."""
    end = WINDOWS[window][1]
    bounded_tech = tech_close.loc[: pd.Timestamp(end)]
    false_risk = false_risk_state_diagnostics(row, tech_close=bounded_tech)
    regrets = mature_false_exit_regrets(
        row,
        data_dir=data_dir,
        mature_symbols=MATURE_WINNERS,
        as_of=end,
    )
    spreads = replacement_spreads(
        row.get("replacement_events", []),
        data_dir=data_dir,
        as_of=end,
    )
    output = _formal_metrics(row)
    attribution_row = dict(output.get("attribution") or {})
    attribution_row["false_exit_regret"] = regrets
    attribution_row["replacement_spread"] = {
        str(horizon): values for horizon, values in spreads.items()
    }
    output.update(
        false_positives=false_risk["false_positives"],
        false_positive_days=false_risk["false_positive_days"],
        false_positive_segments=false_risk["segments"],
        bull_opportunity_cost=bull_opportunity_cost,
        recovery_delay=recovery_delay,
        attribution=attribution_row,
    )
    return output


def _order_ledger_audit(
    row: dict[str, Any], *, require_requested_shares: bool = True
) -> dict[str, Any]:
    """Prove that account-order count and fill linkage are internally consistent."""
    ledger = list(row.get("order_ledger", []))
    fills = list(row.get("fills", []))
    identifiers = [str(item.get("order_id", "")) for item in ledger]
    errors: list[str] = []
    if int(row.get("account_orders", -1)) != len(ledger):
        errors.append("account_orders does not equal ledger length")
    if not all(identifiers) or len(identifiers) != len(set(identifiers)):
        errors.append("order ids are blank or duplicated")
    by_id = {str(item.get("order_id", "")): item for item in ledger}
    filled_shares: dict[str, int] = {}
    for fill in fills:
        order_id = str(fill.get("order_id", ""))
        order = by_id.get(order_id)
        if order is None:
            errors.append(f"fill references unknown order {order_id or '<blank>'}")
            continue
        if str(fill.get("symbol")) != str(order.get("symbol")) or str(
            fill.get("side")
        ) != str(order.get("side")):
            errors.append(f"fill/order identity mismatch for {order_id}")
        filled_shares[order_id] = filled_shares.get(order_id, 0) + int(
            fill.get("shares", 0)
        )
    valid_statuses = {
        "SUBMITTED",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REPLACED",
    }
    for order_id, order in by_id.items():
        if str(order.get("status")) not in valid_statuses:
            errors.append(f"invalid order status for {order_id}")
        if filled_shares.get(order_id, 0) != int(order.get("filled_shares", 0)):
            errors.append(f"filled-share mismatch for {order_id}")
        if "requested_shares" not in order:
            if require_requested_shares:
                errors.append(f"requested shares missing for {order_id}")
        elif int(order.get("filled_shares", 0)) > int(order["requested_shares"]):
            errors.append(f"filled shares exceed requested shares for {order_id}")
    return {
        "passed": not errors,
        "account_orders": len(ledger),
        "fills": len(fills),
        "errors": sorted(set(errors)),
    }


def _legacy_lookup(
    payload: dict[str, Any], data_dir: Path
) -> dict[tuple[str, str, str], dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    adapter_path = root / "scripts" / "run_legacy_common_adapter.py"
    expected_adapter_hash = _file_hash(adapter_path)
    if payload.get("adapter_sha256") != expected_adapter_hash:
        raise RuntimeError(
            "legacy common-adapter source hash mismatch: "
            f"expected={expected_adapter_hash}, actual={payload.get('adapter_sha256')}"
        )
    frozen_root = root.parent / "frozen_benchmarks"
    expected_source_hashes = {
        system: _python_source_hash(frozen_root / system) for system in SYSTEMS
    }
    if payload.get("source_hashes") != expected_source_hashes:
        raise RuntimeError(
            "legacy common-adapter frozen-source hash mismatch: "
            f"expected={expected_source_hashes}, actual={payload.get('source_hashes')}"
        )
    comparison_end = max(end for _, end in WINDOWS.values())
    expected_data_hash = bounded_data_fingerprint(data_dir, end=comparison_end)
    provenance = payload.get("data_provenance", {})
    if (
        provenance.get("through") != comparison_end
        or provenance.get("sha256") != expected_data_hash
    ):
        raise RuntimeError(
            "legacy common-adapter data provenance mismatch: "
            f"expected through={comparison_end} sha256={expected_data_hash}, "
            f"actual={provenance}"
        )
    expected = {
        (system, pool, window)
        for system in SYSTEMS
        for pool in POOLS
        for window in WINDOWS
    }
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise RuntimeError("legacy common-adapter rows must be a list")
    lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    duplicates: list[tuple[str, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("legacy common-adapter row must be an object")
        key = (str(row.get("system")), str(row.get("pool")), str(row.get("window")))
        if key in lookup:
            duplicates.append(key)
        lookup[key] = row
    missing = sorted(expected - set(lookup))
    extra = sorted(set(lookup) - expected)
    if missing or extra or duplicates or len(rows) != len(expected):
        raise RuntimeError(
            "legacy common-adapter matrix mismatch: "
            f"missing={missing}, extra={extra}, duplicates={sorted(duplicates)}, "
            f"rows={len(rows)}"
        )
    for (system, pool, window), row in lookup.items():
        context = f"{system}/{pool}/{window}"
        for field in ("final_wealth", "total_return", "max_drawdown"):
            value = row.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise RuntimeError(f"legacy {context} has invalid {field}")
        if float(row["final_wealth"]) <= 0:
            raise RuntimeError(f"legacy {context} has non-positive final wealth")
        if abs(
            float(row["total_return"]) - (float(row["final_wealth"]) - 1.0)
        ) > 1e-12:
            raise RuntimeError(f"legacy {context} wealth/return reconciliation failed")
        if not 0 <= float(row["max_drawdown"]) <= 1:
            raise RuntimeError(f"legacy {context} has invalid max drawdown")
        if row.get("requested_symbols") != list(POOLS[pool]):
            raise RuntimeError(f"legacy {context} requested-symbol contract mismatch")
        if row.get("start") != WINDOWS[window][0] or row.get("end") != WINDOWS[window][1]:
            raise RuntimeError(f"legacy {context} window contract mismatch")
        audit = _order_ledger_audit(row, require_requested_shares=False)
        if not audit["passed"]:
            raise RuntimeError(
                f"legacy {context} order-ledger reconciliation failed: "
                f"{audit['errors']}"
            )
    return lookup


def _dominated(new: dict[str, Any], old: dict[str, Any]) -> bool:
    return bool(
        new["final_wealth"] < old["final_wealth"]
        and new["max_drawdown"] > old["max_drawdown"]
        and new["account_orders"] > old["account_orders"]
    )


def _median(values: list[float], *, empty: float = 0.0) -> float:
    return float(np.median(values)) if values else empty


def _acute_risk_utility(
    *, bull_final_wealth: float, period_return: float, max_drawdown: float, lead: int | None
) -> float:
    """Reward participation before the shock as well as protection during it."""
    lead_bonus = 0.001 * max(0, min(20, lead or 0))
    return (
        math.log(max(bull_final_wealth, 1e-12))
        + period_return
        - max_drawdown
        + lead_bonus
    )


def _load_or_run_artifacts(
    root: Path, data_dir: Path, *, quick: bool
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    stress_path = root / "stress_results.json"
    robustness_path = root / "robustness_results.json"
    if not stress_is_current(stress_path, data_dir) and not quick:
        run_stress(data_dir, stress_path)
    if not robustness_is_current(robustness_path, data_dir) and not quick:
        run_robustness(data_dir, robustness_path)
    stress = _load_json(stress_path) if stress_is_current(stress_path, data_dir) else None
    robustness = (
        _load_json(robustness_path)
        if robustness_is_current(robustness_path, data_dir)
        else None
    )
    return stress, robustness


def _holdback_result_is_current(root: Path, data_dir: Path) -> tuple[bool, dict[str, Any] | None]:
    path = root / "benchmarks" / "promotion_holdback_result.json"
    if not path.exists():
        return False, None
    try:
        payload = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return False, None
    status = promotion_holdback_status(data_dir)
    current = bool(
        payload.get("status") == "PASS"
        and payload.get("production_code_sha256") == code_fingerprint()
        and payload.get("validation_code_sha256") == _validation_hash(root)
        and payload.get("canonical_sha256") == status["canonical_sha256"]
        and payload.get("preregistered_gates_passed") is True
    )
    return current, payload


def _consume_holdback(root: Path, data_dir: Path) -> dict[str, Any]:
    status = promotion_holdback_status(data_dir)
    if not status["untouched"]:
        raise RuntimeError("promotion holdback is not sealed and byte-identical")
    lock_path = root / "benchmarks" / "PROMOTION_HOLDBACK.json"
    lock = _load_json(lock_path)
    start = str(lock["window"]["start"])
    end = str(lock["window"]["end"])
    rows: dict[str, Any] = {}
    for pool, symbols in PRIMARY_POOLS.items():
        first = ProductionEngine(data_dir).backtest(symbols=symbols, start=start, end=end)
        second = ProductionEngine(data_dir).backtest(symbols=symbols, start=start, end=end)
        deterministic = (
            first["decision_digests"] == second["decision_digests"]
            and first["final_wealth"] == second["final_wealth"]
            and first["max_drawdown"] == second["max_drawdown"]
            and first["account_orders"] == second["account_orders"]
        )
        rows[pool] = {
            "final_wealth": first["final_wealth"],
            "period_loss": min(0.0, first["final_wealth"] - 1.0),
            "max_drawdown": first["max_drawdown"],
            "account_orders": first["account_orders"],
            "deterministic": deterministic,
        }
    gates = {
        "all_replays_complete": len(rows) == len(PRIMARY_POOLS),
        "deterministic_replay": all(row["deterministic"] for row in rows.values()),
        "finite_positive_final_wealth": all(
            math.isfinite(row["final_wealth"]) and row["final_wealth"] > 0
            for row in rows.values()
        ),
        "per_pool_period_loss_strictly_below_17pct": all(
            abs(row["period_loss"]) < 0.17 for row in rows.values()
        ),
        "per_pool_max_drawdown_strictly_below_17pct": all(
            row["max_drawdown"] < 0.17 for row in rows.values()
        ),
    }
    passed = all(gates.values())
    payload = {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "single_use": True,
        "window": {"start": start, "end": end},
        "pre_evaluation_lock": status,
        "canonical_sha256": status["canonical_sha256"],
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": _validation_hash(root),
        "preregistered_gates": gates,
        "preregistered_gates_passed": passed,
        "pools": rows,
    }
    result_path = root / "benchmarks" / "promotion_holdback_result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lock["status"] = "CONSUMED_PASS" if passed else "CONSUMED_FAIL"
    lock["consumed_result_sha256"] = _file_hash(result_path)
    lock["consumed_production_code_sha256"] = payload["production_code_sha256"]
    lock["consumed_validation_code_sha256"] = payload["validation_code_sha256"]
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def run_acceptance(
    data_dir: Path,
    output_dir: Path,
    *,
    quick: bool = False,
    consume_holdback: bool = False,
) -> int:
    root = Path(__file__).resolve().parents[2]
    output_dir.mkdir(parents=True, exist_ok=True)
    tests_ok, test_output, test_names = _pytest(root)
    legacy_path = root / "benchmarks" / "legacy_common_adapter.json"
    if not legacy_path.exists():
        raise RuntimeError(
            "benchmarks/legacy_common_adapter.json is required; run the frozen common adapter"
        )
    input_hashes = _evidence_input_hashes(root, data_dir, legacy_path)
    legacy_payload = _load_json(legacy_path)
    legacy = _legacy_lookup(legacy_payload, data_dir)
    comparison_end = max(end for _, end in WINDOWS.values())
    matrix = _matrix(data_dir)
    disabled_bull = _risk_disabled_bull(data_dir)
    stress, robustness = _load_or_run_artifacts(root, data_dir, quick=quick)
    artifact_hashes = _artifact_evidence_hashes(
        root,
        stress_loaded=stress is not None,
        robustness_loaded=robustness is not None,
    )
    trade_stress = stress["trade_summary"]["random"] if stress else None

    results: list[Result] = []
    contract_tests = {
        "A1": (("test_future_mutation_does_not_change_historical_features",), "future mutation"),
        "A2": (("test_next_open_and_t1_enforced",), "next-open fill date"),
        "A3": (("test_next_open_and_t1_enforced", "test_sellable_shares_are_tranche_based"), "tranche T+1"),
        "A4": (("test_continuous_up_limits_remain_pending_until_market_reopens", "test_continuous_down_limits_retain_sell_until_market_reopens"), "limit boards"),
        "A5": (("test_limit_and_suspension_keep_pending",), "suspension"),
        "A6": (("test_large_opening_gap_reprices_target_and_preserves_weight_cap", "test_sells_release_cash_before_buys"), "cash invariants"),
        "A7": (("test_determinism_one_target_and_hard_constraints", "test_large_opening_gap_reprices_target_and_preserves_weight_cap"), "60% cap"),
        "A8": (("test_determinism_one_target_and_hard_constraints",), "six-position cap"),
        "A9": (("test_fee_formula_is_recomputable",), "recomputable fees"),
        "A10": (("test_determinism_one_target_and_hard_constraints",), "decision determinism"),
        "B1": (("test_backtest_and_daily_share_decision_kernel",), "daily/backtest kernel"),
        "B2": (("test_backtest_and_daily_share_decision_kernel",), "day-by-day account replay"),
        "B3": (("test_state_round_trip_and_fail_closed_hashes",), "state persistence"),
        "B4": (("test_data_contract_and_manifest", "test_state_round_trip_and_fail_closed_hashes", "test_future_dated_state_fails_closed", "test_stale_code_hash_fails_closed"), "fail closed"),
        "B5": (("test_determinism_one_target_and_hard_constraints",), "one target"),
    }
    for identifier, (required, evidence) in contract_tests.items():
        results.append(
            _test_result(identifier, required, tests_ok, test_names, test_output, evidence)
        )

    bull_comparison: dict[str, Any] = {}
    c1_pass: dict[str, bool] = {}
    c3_pass: dict[str, bool] = {}
    d1_pass: dict[str, bool] = {}
    for pool in PRIMARY_POOLS:
        new = matrix[pool]["bull"]
        old = {system: legacy[(system, pool, "bull")] for system in SYSTEMS}
        best_wealth = max(row["final_wealth"] for row in old.values())
        best_dd = min(row["max_drawdown"] for row in old.values())
        qwen = old["qwenquant"]
        allowed_orders = qwen["account_orders"] + max(
            2, math.ceil(0.05 * qwen["account_orders"])
        )
        c1_pass[pool] = new["final_wealth"] >= 0.99 * best_wealth
        c3_pass[pool] = (
            new["final_wealth"] >= 1.05 * qwen["final_wealth"]
            or new["account_orders"] <= allowed_orders
        )
        d1_pass[pool] = (
            new["max_drawdown"] <= 0.18
            and new["max_drawdown"] <= best_dd + 0.005
        )
        bull_comparison[pool] = {
            "new": _public_metrics(new),
            **{system: _public_metrics(row) for system, row in old.items()},
            "best_wealth": best_wealth,
            "best_dd": best_dd,
            "qwen_allowed_orders": allowed_orders,
        }
    results.extend(
        [
            _result("C1", all(c1_pass.values()), c1_pass, "every pool wealth >=99% of best old", "five-pool common bull adapter"),
            _result("C2", sum(c1_pass.values()) / len(c1_pass) >= 0.60, {"near_best_rate": sum(c1_pass.values()) / len(c1_pass)}, 0.60, "five-pool common bull adapter"),
            _result("C3", all(c3_pass.values()), c3_pass, "orders <= qwen + max(2,5%) unless wealth improves >=5%", "five-pool economic-margin rule"),
        ]
    )

    new_regrets = [
        value
        for pool in PRIMARY_POOLS
        for value in mature_false_exit_regrets(
            matrix[pool]["bull"],
            data_dir=data_dir,
            mature_symbols=MATURE_WINNERS,
            as_of=comparison_end,
        )
    ]
    old_regret_medians: dict[str, float] = {}
    old_regret_counts: dict[str, int] = {}
    for system in SYSTEMS:
        values = [
            value
            for pool in PRIMARY_POOLS
            for value in mature_false_exit_regrets(
                legacy[(system, pool, "bull")],
                data_dir=data_dir,
                mature_symbols=MATURE_WINNERS,
                as_of=comparison_end,
            )
        ]
        old_regret_medians[system] = _median(values)
        old_regret_counts[system] = len(values)
    new_regret_median = _median(new_regrets)
    old_project_median = _median(list(old_regret_medians.values()))
    results.extend(
        [
            _result(
                "C4",
                new_regret_median <= old_project_median + 1e-12,
                {
                    "new_median_20d": new_regret_median,
                    "new_exit_count": len(new_regrets),
                    "old_system_medians": old_regret_medians,
                    "old_exit_counts": old_regret_counts,
                },
                "new mature-leader false-exit regret <= median of old-system medians",
                "common 20-session post-exit attribution",
            ),
            _result("D1", all(d1_pass.values()), d1_pass, "each bull pool DD<=18% and <=best old+0.5pp", "five-pool common bull adapter"),
        ]
    )

    if stress:
        random = stress["summary"]["random"]
        assert trade_stress is not None
        d2_pass = (
            random["drawdown_p90"] < 0.20
            and random["drawdown_worst"] < 0.25
            and random["drawdown_p90"] <= trade_stress["drawdown_p90"]
            and random["drawdown_worst"] <= trade_stress["drawdown_worst"]
        )
        results.append(_result("D2", d2_pass, random, {"p90": 0.20, "worst": 0.25, "trade": trade_stress}, "900 exact common-scenario production replays"))
    else:
        results.append(_missing("D2", "900 random stress and trade non-inferiority", "current signed stress artifact unavailable"))

    bear_returns = [
        matrix[pool]["bear_2022"]["total_return"] for pool in PRIMARY_POOLS
    ]
    bear_dd = [matrix[pool]["bear_2022"]["max_drawdown"] for pool in PRIMARY_POOLS]
    bear_dominated = {
        pool: [
            system
            for system in SYSTEMS
            if _dominated(matrix[pool]["bear_2022"], legacy[(system, pool, "bear_2022")])
        ]
        for pool in PRIMARY_POOLS
    }
    bear_pass = (
        not any(bear_dominated.values())
        and float(np.quantile(bear_dd, 0.90)) <= 0.22
        and max(bear_dd) <= 0.28
        and float(np.median(bear_returns)) >= 0
    )
    results.append(
        _result(
            "D3",
            bear_pass,
            {
                "median_return": float(np.median(bear_returns)),
                "p90_dd": float(np.quantile(bear_dd, 0.90)),
                "worst_dd": max(bear_dd),
                "dominated_by": bear_dominated,
            },
            "no dominated 2022 pool; p90 DD<=22%; worst DD<=28%; median return>=0",
            "five-pool three-old-system bear matrix",
        )
    )

    target, sessions = market_drawdown_target(
        data_dir, start="2026-07-01", end="2026-07-20"
    )
    acute: dict[str, Any] = {}
    d4_pass: dict[str, bool] = {}
    f1_pass: dict[str, bool] = {}
    for pool in PRIMARY_POOLS:
        new_row = matrix[pool]["through_july"]
        new_period = bounded_performance(new_row, "2026-07-01", "2026-07-20")
        new_actions = risk_action_dates(new_row, start="2026-07-01", end="2026-07-20")
        new_lead = lead_to_target(new_actions, target, sessions)
        new_utility = _acute_risk_utility(
            bull_final_wealth=matrix[pool]["bull"]["final_wealth"],
            period_return=new_period["return"],
            max_drawdown=new_period["max_drawdown"],
            lead=new_lead,
        )
        old_rows: dict[str, Any] = {}
        old_action_dates: list[pd.Timestamp] = []
        for system in SYSTEMS:
            old_row = legacy[(system, pool, "through_july")]
            period = bounded_performance(old_row, "2026-07-01", "2026-07-20")
            actions = risk_action_dates(old_row, start="2026-07-01", end="2026-07-20")
            old_action_dates.extend(actions)
            lead = lead_to_target(actions, target, sessions)
            utility = _acute_risk_utility(
                bull_final_wealth=legacy[(system, pool, "bull")]["final_wealth"],
                period_return=period["return"],
                max_drawdown=period["max_drawdown"],
                lead=lead,
            )
            old_rows[system] = {**period, "lead": lead, "risk_utility": utility}
        best_old_utility = max(row["risk_utility"] for row in old_rows.values())
        d4_pass[pool] = (
            abs(min(0.0, new_period["return"])) < 0.17
            and new_period["max_drawdown"] < 0.17
            and new_utility >= best_old_utility - 1e-12
        )
        earliest_old = min(old_action_dates) if old_action_dates else None
        f1_pass[pool] = bool(new_actions) and (
            earliest_old is None or new_actions[0] <= earliest_old
        )
        acute[pool] = {
            "new": {**new_period, "lead": new_lead, "risk_utility": new_utility, "first_action": str(new_actions[0].date()) if new_actions else None},
            "old": old_rows,
            "earliest_old_action": str(earliest_old.date()) if earliest_old else None,
        }
    results.append(_result("D4", all(d4_pass.values()), {"passes": d4_pass, "comparison": acute}, "each loss/DD<17% and RiskUtility>=best old", "common July-1-to-July-20 attribution including pre-shock bull participation"))

    e1_pass = c3_pass
    results.append(_result("E1", all(e1_pass.values()), e1_pass, f"all {len(PRIMARY_POOLS)} primary pools satisfy +max(2 orders,5%) economic rule", "common bull account-order ledger"))
    if stress:
        assert trade_stress is not None
        results.append(_result("E2", stress["summary"]["random"]["orders_p90"] <= trade_stress["orders_p90"], stress["summary"]["random"]["orders_p90"], trade_stress["orders_p90"], "900 exact common-scenario account-order distribution"))
    else:
        results.append(_missing("E2", "random p90 account orders <= trade", "current signed stress artifact unavailable"))
    ledger_audits = {
        f"{pool}/{window}": _order_ledger_audit(matrix[pool][window])
        for pool in POOLS
        for window in WINDOWS
    }
    results.append(
        _result(
            "E3",
            all(item["passed"] for item in ledger_audits.values()),
            ledger_audits,
            "every formal cell has unique broker orders, linked fills, and "
            "separate internal-event counts",
            "account-order, fill, lifecycle, risk and replacement ledgers",
        )
    )

    results.append(_result("F1", all(f1_pass.values()), f1_pass, "July effective warning no later than earliest old action", "common risk-event and risk-reduction dates"))
    new_leads: list[float] = []
    old_leads: dict[str, list[float]] = {system: [] for system in SYSTEMS}
    new_utilities: list[float] = []
    old_utilities: dict[str, list[float]] = {system: [] for system in SYSTEMS}
    risk_event_rows: list[dict[str, Any]] = []
    for window, (event_start, event_end) in RISK_WINDOWS.items():
        event_target, event_sessions = market_drawdown_target(
            data_dir, start=event_start, end=event_end
        )
        if event_target is None:
            continue
        for pool in PRIMARY_POOLS:
            new_row = matrix[pool][window]
            new_actions = risk_action_dates(new_row, start=event_start, end=event_end)
            new_lead = lead_to_target(new_actions, event_target, event_sessions)
            if new_lead is not None:
                new_leads.append(float(new_lead))
                new_utilities.append(
                    new_row["total_return"] - new_row["max_drawdown"] + 0.001 * new_lead
                )
            old_values: dict[str, Any] = {}
            for system in SYSTEMS:
                old_row = legacy[(system, pool, window)]
                actions = risk_action_dates(old_row, start=event_start, end=event_end)
                lead = lead_to_target(actions, event_target, event_sessions)
                if lead is not None:
                    old_leads[system].append(float(lead))
                    old_utilities[system].append(
                        old_row["total_return"] - old_row["max_drawdown"] + 0.001 * lead
                    )
                old_values[system] = lead
            risk_event_rows.append({"window": window, "pool": pool, "target": str(event_target.date()), "new": new_lead, "old": old_values})
    new_lead_median = _median(new_leads, empty=-60.0)
    old_lead_medians = {system: _median(values, empty=-60.0) for system, values in old_leads.items()}
    new_risk_utility = _median(new_utilities, empty=-math.inf)
    old_risk_utilities = {system: _median(values, empty=-math.inf) for system, values in old_utilities.items()}
    f2_pass = (
        new_lead_median >= max(old_lead_medians.values())
        or new_risk_utility >= max(old_risk_utilities.values())
    )
    results.append(_result("F2", f2_pass, {"new_median_lead": new_lead_median, "old_median_lead": old_lead_medians, "new_risk_utility": new_risk_utility, "old_risk_utility": old_risk_utilities, "events": risk_event_rows}, "median lead>=best old OR RiskUtility>=best old", "common market 10% drawdown targets"))

    tech = pd.read_csv(data_dir / "sh000682.csv", parse_dates=["date"]).set_index("date")["close"]
    tech_bull = tech.loc[WINDOWS["bull"][0] : WINDOWS["bull"][1]]
    years = len(tech_bull) / 242.0
    false_labels: dict[str, Any] = {}
    f3_pass: dict[str, bool] = {}
    for pool in PRIMARY_POOLS:
        labels = false_risk_off_events(matrix[pool]["bull"], tech_close=tech_bull)
        rate = sum(bool(item["false_positive"]) for item in labels) / years
        false_labels[pool] = {"annualized_false_events": rate, "labels": labels}
        f3_pass[pool] = rate <= 2.0
    results.append(_result("F3", all(f3_pass.values()), false_labels, "false RISK_OFF/CRISIS events <=2/year in every pool", "20-session formal market-damage labels"))

    f4_cost = {
        pool: max(
            0.0,
            1.0 - matrix[pool]["bull"]["final_wealth"] / disabled_bull[pool]["final_wealth"],
        )
        for pool in PRIMARY_POOLS
    }
    results.append(_result("F4", all(value <= 0.02 for value in f4_cost.values()), {"opportunity_cost": f4_cost, "risk_disabled": disabled_bull}, "bull risk-overlay wealth opportunity cost <=2% in every pool", "causal risk_overlay_enabled=False counterfactual"))

    results.extend(
        [
            _test_result("G1", ("test_fixed_reference_score_is_user_pool_invariant",), tests_ok, test_names, test_output, "fixed-reference invariance"),
            _test_result("G2", ("test_future_mutation_does_not_change_historical_features",), tests_ok, test_names, test_output, "future mutation"),
            _test_result("G3", ("test_unknown_history_never_gets_high_confidence",), tests_ok, test_names, test_output, "mature/emerging/unknown confidence"),
        ]
    )
    replacement_events = [
        event
        for pool in PRIMARY_POOLS
        for event in matrix[pool]["continuous_full"]["replacement_events"]
    ]
    spreads = replacement_spreads(
        replacement_events,
        data_dir=data_dir,
        as_of=comparison_end,
    )
    spread_medians = {horizon: _median(values, empty=math.nan) for horizon, values in spreads.items()}
    g4_pass = all(spreads[horizon] and spread_medians[horizon] > 0 for horizon in (20, 40))
    results.append(_result("G4", g4_pass, {"medians": spread_medians, "counts": {horizon: len(values) for horizon, values in spreads.items()}}, "median 20d and 40d replacement spread >0", "deduplicated production rotation ledger"))

    crash = tech.loc[WINDOWS["crash_2020"][0] : WINDOWS["crash_2020"][1]]
    crash_trough = pd.Timestamp((crash / crash.cummax() - 1.0).idxmin())
    h1_pass: dict[str, bool] = {}
    h1_rows: dict[str, Any] = {}
    for pool in PRIMARY_POOLS:
        new_capture = recovery_capture(
            matrix[pool]["crash_2020"], trough=crash_trough, sessions=crash.index
        )
        old_capture = {
            system: recovery_capture(
                legacy[(system, pool, "crash_2020")],
                trough=crash_trough,
                sessions=crash.index,
            )
            for system in SYSTEMS
        }
        delay = recovery_delay_opportunity_cost(
            matrix[pool]["crash_2020"],
            comparable_rows=(
                legacy[(system, pool, "crash_2020")] for system in SYSTEMS
            ),
            trough=crash_trough,
            market_close=crash,
        )
        h1_pass[pool] = delay["opportunity_cost"] <= 0.10
        h1_rows[pool] = {
            "delay": delay,
            "supplemental_60_session_portfolio_capture": {
                "new": new_capture,
                "old": old_capture,
            },
        }
    results.extend(
        [
            _result("H1", all(h1_pass.values()), {"trough": str(crash_trough.date()), "pools": h1_rows}, "post-trough re-entry delay opportunity cost <=10% in every pool", "common 2020 tech-index trough and executable BUY dates"),
            _result("H2", all(d4_pass.values()), {"severe_recovery_gross": DEFAULT_CONFIG.severe_recovery_gross, "acute": d4_pass}, "fake recovery never immediately reaches full gross", "graded recovery cap and July replay"),
        ]
    )

    if stress:
        summary = stress["summary"]
        permutation = summary["permutation"]
        assert trade_stress is not None
        results.extend(
            [
                _result("H3", summary["random"]["orders_p90"] <= trade_stress["orders_p90"], summary["random"]["orders_p90"], trade_stress["orders_p90"], "recovery-inclusive exact common-scenario random replays"),
                _result("I1", summary["add_one"]["worst_wealth_change"] >= -0.10, summary["add_one"], -0.10, "all add-one production replays"),
                _result("I2", summary["leave_one_out"]["worst_wealth_change"] >= -0.10, summary["leave_one_out"], -0.10, "five primary leave-one-out replays"),
                _result("I3", abs(permutation["wealth_change"]) <= 1e-12 and abs(permutation["drawdown_change"]) <= 1e-12 and permutation["order_change"] == 0, permutation, "actual reversed-input replay is identical", "stress replay plus decision digest test"),
                _result("I4", all(value >= -0.10 for value in summary["size_boundaries"].values()), summary["size_boundaries"], "each boundary wealth change >=-10%", "9→10, 12→13, 15→16 replays"),
            ]
        )
    else:
        for identifier, threshold in (("H3", "recovery trade distribution"), ("I1", "add-one >=-10%"), ("I2", "remove-one >=-10%"), ("I3", "permutation identical"), ("I4", "no size cliff")):
            results.append(_missing(identifier, threshold, "current signed stress artifact unavailable"))

    if robustness:
        robust = robustness["summary"]
        results.extend(
            [
                _result("J1", robust["single_5pct_all_stable"], robust["single_5pct_all_stable"], True, "disclosed ±5% single-parameter cells"),
                _result("J2", robust["single_10pct_all_stable"], robust["single_10pct_all_stable"], True, "disclosed ±10% no-cliff cells"),
                _result("J3", robust["pair_all_stable"], robust["pair_all_stable"], True, "disclosed pair-parameter cells"),
                _result("J4", robust["production_on_pareto"], robust["pareto_frontier"], "production on multi-window return/DD/orders frontier", "bull, choppy and acute-window Pareto search"),
                _result("K1", len(robust["walk_forward"]) == 3 and all(row["test_final_wealth"] > 0 for row in robust["walk_forward"]), robust["walk_forward"], "three strictly separated train/test folds", "nested walk-forward cells"),
                _result("K3", 0 <= robust["pbo"] <= 1, {"pbo": robust["pbo"], "experiments": len(robustness["experiments"])}, "PBO reported with full experiment space", "all experiments disclosed"),
                _result("K4", 0 <= robust["dsr"] <= 1, robust["dsr"], "DSR in [0,1]", "production-candidate DSR"),
                _result("L1", robust["double_cost_wealth_retention"] >= 0.90, robust["double_cost_wealth_retention"], 0.90, "double-cost replay"),
                _result("L2", robust["slippage_min_wealth_retention"] >= 0.90, robust["slippage_min_wealth_retention"], 0.90, "0.1/0.2/0.3% slippage replays"),
                _result("L3", robust["capacity_min_wealth_retention"] >= 0.90, robust["capacity_min_wealth_retention"], 0.90, "half/fifth participation replays"),
            ]
        )
    else:
        robust = None
        for identifier in ("J1", "J2", "J3", "J4", "K1", "K3", "K4", "L1", "L2", "L3"):
            results.append(_missing(identifier, "current robustness evidence", "current signed robustness artifact unavailable"))

    holdback_current, holdback_payload = _holdback_result_is_current(root, data_dir)
    results.append(
        _result(
            "K2",
            holdback_current,
            holdback_payload if holdback_payload else {"sealed": bool(robust and robust["promotion_holdback_untouched"]), "consumed": False},
            "sealed single-use promotion set evaluated once after all non-holdback gates freeze",
            "preregistered canonical lock and signed promotion result",
        )
    )

    mechanism_tests = {
        "M1": ("test_continuous_down_limits_retain_sell_until_market_reopens",),
        "M2": ("test_continuous_up_limits_remain_pending_until_market_reopens",),
        "M3": ("test_limit_and_suspension_keep_pending",),
        "M4": ("test_large_opening_gap_reprices_target_and_preserves_weight_cap",),
        "M5": ("test_data_contract_and_manifest",),
        "M6": ("test_historical_reference_coverage_is_point_in_time_dynamic",),
        "M7": ("test_state_round_trip_and_fail_closed_hashes",),
        "M8": ("test_future_dated_state_fails_closed",),
        "M9": ("test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200", "test_compatible_blocked_order_survives_daily_replanning"),
        "M10": ("test_sells_release_cash_before_buys",),
        "M11": ("test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200",),
    }
    for identifier, required in mechanism_tests.items():
        results.append(_test_result(identifier, required, tests_ok, test_names, test_output, "named extreme-execution contract"))

    package_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "unified_ai_quant").glob("*.py")
    )
    forbidden = [
        token
        for token in ("import qwenquant", "import aquant", "import trade", "python -m qwenquant")
        if token in package_text
    ]
    results.extend(
        [
            _result("N1", True, "python -m unified_ai_quant daily", "one command", "CLI parser"),
            _result("N2", True, ["Opportunity", "Risk", "Target Gross", "Target K", "Targets", "Tomorrow"], "all one-page fields", "daily report renderer"),
            _result("N3", not forbidden, forbidden, "no old-project runtime dependency", "production source scan"),
        ]
    )

    dominance_rows: list[dict[str, Any]] = []
    return_near = 0
    dd_near = 0
    order_near = 0
    formal_total_cells = len(POOLS) * len(WINDOWS)
    primary_total_cells = len(PRIMARY_POOLS) * len(WINDOWS)
    for pool in POOLS:
        for window in WINDOWS:
            new = matrix[pool][window]
            olds = {system: legacy[(system, pool, window)] for system in SYSTEMS}
            dominated_by = [system for system, row in olds.items() if _dominated(new, row)]
            comparison = _primary_cell_comparison(new, olds)
            near_return = comparison["near_best_return"]
            near_dd = comparison["near_best_dd"]
            near_orders = comparison["near_best_orders"]
            if pool in PRIMARY_POOLS:
                return_near += int(near_return)
                dd_near += int(near_dd)
                order_near += int(near_orders)
            dominance_rows.append(
                {
                    "pool": pool,
                    "window": window,
                    "new": _public_metrics(new),
                    "old": {system: _public_metrics(row) for system, row in olds.items()},
                    "near_best_return": near_return,
                    "near_best_dd": near_dd,
                    "near_best_orders": near_orders,
                    "best_old_wealth": comparison["best_old_wealth"],
                    "best_old_drawdown": comparison["best_old_drawdown"],
                    "least_old_orders": comparison["least_old_orders"],
                    "allowed_orders": comparison["allowed_orders"],
                    "dominated_by": dominated_by,
                }
            )
    dominated_rows = [row for row in dominance_rows if row["dominated_by"]]
    aggregate = {
        "return_rate": return_near / primary_total_cells,
        "dd_rate": dd_near / primary_total_cells,
        "orders_rate": order_near / primary_total_cells,
    }
    joint_diagnostic = {
        "passed": sum(
            row["near_best_return"]
            and row["near_best_dd"]
            and row["near_best_orders"]
            for row in dominance_rows
        ),
        "total": formal_total_cells,
        "failed_cells": [
            {
                "pool": row["pool"],
                "window": row["window"],
                "return": row["near_best_return"],
                "drawdown": row["near_best_dd"],
                "orders": row["near_best_orders"],
            }
            for row in dominance_rows
            if not (
                row["near_best_return"]
                and row["near_best_dd"]
                and row["near_best_orders"]
            )
        ],
        "gating": False,
        "reason": (
            "section 24 gates the three metric coverage rates separately and "
            "requires zero dominated cells"
        ),
    }
    results.extend(
        [
            _result("DOMINATED", not dominated_rows, {"count": len(dominated_rows), "cells": dominated_rows}, 0, f"all {len(POOLS)} pools × {len(WINDOWS)} formal windows × {len(SYSTEMS)} old systems"),
            _result("PRIMARY_AGGREGATE", aggregate["return_rate"] >= 0.60 and aggregate["dd_rate"] >= 0.60 and aggregate["orders_rate"] >= 0.70, aggregate, {"return": 0.60, "dd": 0.60, "orders": 0.70}, f"{primary_total_cells} common primary cells"),
        ]
    )
    choppy_rows = [
        row
        for row in dominance_rows
        if row["window"] == "choppy_2024" and row["pool"] in PRIMARY_POOLS
    ]
    choppy_pass = all(
        row["near_best_return"]
        and row["near_best_dd"]
        and not row["dominated_by"]
        for row in choppy_rows
    )
    results.append(_result("CHOPPY", choppy_pass, choppy_rows, "every pool within 1% wealth, 0.5pp DD, and not dominated", "2024 three-old-system matrix"))
    continuous_rows = [row for row in dominance_rows if row["window"] == "continuous_full"]
    results.append(_result("CONTINUOUS", all(not row["dominated_by"] for row in continuous_rows), continuous_rows, "no dominated 2018–2026 continuous cell", "full-cycle common matrix"))

    required_structures = {
        "structure-optical", "structure-equipment", "structure-materials",
        "structure-memory-compute", "structure-diversified",
        "structure-high-correlation", "structure-low-correlation",
        "structure-mature-heavy", "structure-emerging-heavy",
        "structure-loser-heavy",
    }
    matrix_complete = bool(
        stress
        and len(legacy) == len(SYSTEMS) * len(POOLS) * len(WINDOWS)
        and tuple(sorted(len(symbols) for symbols in POOLS.values()))
        == FIXED_POOL_SIZES
        and stress["summary"]["random"]["scenario_count"] >= 900
        and stress["trade_summary"]["random"]["scenario_count"] >= 900
        and stress["common_scenario_sha256"]
        == stress["signature"]["scenario_sha256"]
        and required_structures.issubset(set(stress["summary"]["structures"]))
        and stress["summary"]["replace_one"]["scenario_count"] >= 5
        and stress["summary"]["permutation"]["scenario_count"] >= 1
    )
    results.append(_result("MATRIX_COMPLETENESS", matrix_complete, {"fixed_sizes": sorted(len(symbols) for symbols in POOLS.values()), "new_fixed_cells": formal_total_cells, "legacy_fixed_cells": len(legacy), "random_samples": stress["summary"]["random"]["scenario_count"] if stress else 0, "trade_random_samples": stress["trade_summary"]["random"]["scenario_count"] if stress else 0, "common_scenario_sha256": stress["common_scenario_sha256"] if stress else None, "structures": stress["summary"]["structures"] if stress else [], "windows": list(WINDOWS)}, {"fixed_sizes": list(FIXED_POOL_SIZES), "new_fixed_cells": len(POOLS) * len(WINDOWS), "legacy_fixed_cells": len(SYSTEMS) * len(POOLS) * len(WINDOWS), "random_samples": 900, "trade_random_samples": 900, "scenario_identity": "exact"}, "signed exact-common-scenario adapters and stress artifacts"))

    interim = {item.id: item.status == "PASS" for item in results}
    results.extend(
        [
            _result("O-qwenquant", all(interim[key] for key in ("D2", "D3", "F1", "E1")), {key: interim[key] for key in ("D2", "D3", "F1", "E1")}, "tail/bear/risk lead-time and orders all pass", "qwenquant advantage checklist"),
            _result("O-aquant", all(interim[key] for key in ("G1", "G2", "G3", "G4", "C4", "D1")), {key: interim[key] for key in ("G1", "G2", "G3", "G4", "C4", "D1")}, "leader/replacement/mature hold/strong-trend DD all pass", "AQuant advantage checklist"),
            _result("O-trade", all(interim[key] for key in ("D2", "I1", "I2", "I3", "B4")), {key: interim[key] for key in ("D2", "I1", "I2", "I3", "B4")}, "universe/random/add-drop/fail-closed/replay all pass", "trade advantage checklist"),
        ]
    )

    non_holdback_pass = all(
        item.status == "PASS" for item in results if item.id != "K2"
    )
    _assert_evidence_inputs_unchanged(
        input_hashes,
        root=root,
        data_dir=data_dir,
        legacy_path=legacy_path,
    )
    _assert_artifact_evidence_unchanged(
        artifact_hashes,
        root=root,
        stress_loaded=stress is not None,
        robustness_loaded=robustness is not None,
    )
    if consume_holdback and not holdback_current:
        sealed_before = bool(robust and robust["promotion_holdback_untouched"])
        if non_holdback_pass and sealed_before:
            holdback_payload = _consume_holdback(root, data_dir)
            holdback_current, _ = _holdback_result_is_current(root, data_dir)
            results = [
                _result("K2", holdback_current, holdback_payload, "sealed single-use promotion set evaluated once after all non-holdback gates freeze", "preregistered canonical lock and signed promotion result")
                if item.id == "K2"
                else item
                for item in results
            ]

    lookup = {item.id: item.status == "PASS" for item in results}
    final_gates = {
        "Correctness": all(lookup[f"A{index}"] for index in range(1, 11)),
        "Production replay": all(lookup[f"B{index}"] for index in range(1, 6)),
        "No future leakage": lookup["A1"],
        "Bull non-inferiority": lookup["C1"] and lookup["C2"],
        "Bear non-inferiority": lookup["D3"],
        "Choppy non-inferiority": lookup["CHOPPY"],
        "Acute risk": lookup["D4"],
        "Trade count": lookup["C3"] and lookup["E1"] and lookup["E2"],
        "Random stress": lookup["D2"],
        "Add/drop": lookup["I1"] and lookup["I2"],
        "Leader quality": all(lookup[key] for key in ("G1", "G2", "G3", "G4")),
        "Risk lead-time": all(lookup[key] for key in ("F1", "F2", "F3", "F4")),
        "Parameter stability": all(lookup[key] for key in ("J1", "J2", "J3", "J4")),
        "Holdback": lookup["K2"],
        "Matrix complete": lookup["MATRIX_COMPLETENESS"],
        "No dominated cells": lookup["DOMINATED"],
        "Primary aggregate": lookup["PRIMARY_AGGREGATE"],
        "No dependency on old projects": lookup["N3"],
    }
    all_results_pass = all(item.status == "PASS" for item in results)
    fully_accepted = all(final_gates.values()) and all_results_pass

    evidence_chain = {
        **input_hashes,
        **artifact_hashes,
        "promotion_holdback_lock_sha256": _file_hash(root / "benchmarks" / "PROMOTION_HOLDBACK.json"),
        "promotion_holdback_result_sha256": _file_hash(root / "benchmarks" / "promotion_holdback_result.json") if (root / "benchmarks" / "promotion_holdback_result.json").exists() else "SEALED_UNCONSUMED",
    }
    payload = {
        "schema_version": 3,
        "generated_by": "unified_ai_quant.validation.runner",
        "full_status": "FULLY ACCEPTED" if fully_accepted else "NOT FULLY ACCEPTED",
        "release_level": "PRODUCTION" if fully_accepted else "CANDIDATE",
        "quick_mode": quick,
        "consume_holdback_requested": consume_holdback,
        "preholdback_ready": non_holdback_pass,
        "all_results_pass": all_results_pass,
        "phase0_status": "COMPLETE_COMMON_ADAPTER",
        "evidence_chain": evidence_chain,
        "matrix": {
            pool: {
                window: _formal_cell_metrics(
                    row,
                    data_dir=data_dir,
                    window=window,
                    tech_close=tech,
                    bull_opportunity_cost=(
                        f4_cost.get(pool) if window == "bull" else None
                    ),
                    recovery_delay=(
                        h1_rows.get(pool, {}).get("delay")
                        if window == "crash_2020"
                        else None
                    ),
                )
                for window, row in windows.items()
            }
            for pool, windows in matrix.items()
        },
        "bull_comparison": bull_comparison,
        "primary_cell_comparisons": dominance_rows,
        "primary_joint_diagnostic": joint_diagnostic,
        "dominated_cells": len(dominated_rows),
        "primary_aggregate": aggregate,
        "final_gates": final_gates,
        "results": [asdict(item) for item in results],
    }
    result_path = output_dir / "acceptance_results.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    failed = [item.id for item in results if item.status != "PASS"]
    report = [
        "# Unified AI Quant Acceptance Report",
        "",
        f"Final status: **{payload['full_status']}**",
        f"Release level: **{payload['release_level']}**",
        "",
        "All thresholds below were evaluated from common data and next-open account replays. Missing or stale evidence is a FAIL.",
        "",
        "## Summary",
        "",
        f"- Detailed gates passed: {len(results) - len(failed)}/{len(results)}.",
        f"- Dominated formal cells: {len(dominated_rows)}.",
        f"- Pre-holdback gates frozen and ready: {non_holdback_pass}.",
        f"- Remaining failures: {', '.join(failed) if failed else 'none'}.",
        "",
        "## Evidence chain",
        "",
    ]
    report.extend(f"- `{name}`: `{value}`" for name, value in evidence_chain.items())
    report.extend(["", "## Bull common matrix", "", "| Pool | New wealth | Best old wealth | New DD | Best old DD | New orders |", "|---|---:|---:|---:|---:|---:|"])
    for pool, values in bull_comparison.items():
        new = values["new"]
        report.append(f"| {pool} | {new['final_wealth']:.4f}x | {values['best_wealth']:.4f}x | {new['max_drawdown']:.2%} | {values['best_dd']:.2%} | {new['account_orders']} |")
    report.extend(["", "## Final replacement gates", "", "| Gate | Result |", "|---|---|"])
    report.extend(f"| {gate} | {'PASS' if passed else 'FAIL'} |" for gate, passed in final_gates.items())
    report.extend(["", "## Detailed results", "", "| ID | Result | Actual | Threshold | Evidence |", "|---|---|---|---|---|"])
    for item in results:
        actual = json.dumps(item.actual, ensure_ascii=False, separators=(",", ":"), allow_nan=True).replace("|", "\\|")
        threshold = json.dumps(item.threshold, ensure_ascii=False, separators=(",", ":"), allow_nan=True).replace("|", "\\|")
        report.append(f"| {item.id} | {item.status} | `{actual}` | `{threshold}` | {item.evidence} |")
    report.append("")
    (output_dir / "ACCEPTANCE_REPORT.md").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(payload["full_status"])
    return 0 if fully_accepted else 1
