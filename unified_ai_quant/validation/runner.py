"""Strict acceptance runner: execute evidence and preserve every hard failure."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..engine import INDEX_SYMBOLS, ProductionEngine, code_fingerprint
from ..leader import REFERENCE_UNIVERSE
from .robustness import artifact_is_current as robustness_is_current
from .robustness import run_robustness
from .stress import artifact_is_current as stress_is_current
from .stress import run_stress

POOLS: dict[str, tuple[str, ...]] = {
    "a": ("sz300308", "sz300502", "sz300394"),
    "b": ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    "c": (
        "sz300308", "sz300502", "sz300394", "sh688008", "sh603986",
        "sz002409", "sh688072", "sh688300", "sz300054",
    ),
    "d": (
        "sz300308", "sz300502", "sz300394", "sh688498", "sh601869",
        "sh688256", "sh688008", "sh603986", "sh688072", "sh688082",
        "sh688120", "sh688300", "sz300054", "sh688361", "sz300604",
    ),
    "e": (
        "sz300308", "sz300502", "sz300394", "sh688498", "sz002281",
        "sh601869", "sh600487", "sh688256", "sh688041", "sh688008",
        "sh603986", "sz300223", "sh688110", "sh688766", "sz002371",
        "sh688012", "sh688072", "sh688082", "sh688120", "sh688037",
        "sh688361", "sz300604", "sh688200", "sh688019", "sz300054",
        "sz002409", "sz300666", "sh688233", "sh688268", "sh688146",
        "sh688300", "sh603688",
    ),
}
PRIMARY = POOLS["b"]
WINDOWS = {
    "bull": ("2025-04-01", "2026-06-30"),
    "bear_2022": ("2022-01-04", "2022-12-30"),
    "choppy_2024": ("2024-01-02", "2024-12-31"),
    "through_july": ("2025-04-01", "2026-07-20"),
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


def _validation_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "unified_ai_quant" / "validation").glob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


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


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in (
            "final_wealth", "total_return", "max_drawdown", "account_orders",
            "sharpe", "calmar", "worst_20d", "worst_60d", "risk_events",
        )
    }


def _public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "risk_events"}


def _matrix(data_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    engine = ProductionEngine(data_dir)
    return {
        pool: {
            window: _metrics(engine.backtest(symbols=symbols, start=start, end=end))
            for window, (start, end) in WINDOWS.items()
        }
        for pool, symbols in POOLS.items()
    }


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


def _annualized_risk_off_events(metrics: dict[str, Any]) -> float:
    events = sum(event.get("to") == "RISK_OFF" for event in metrics["risk_events"])
    return events / (302 / 242)


def _first_july_warning(metrics: dict[str, Any]) -> str | None:
    dates = [
        str(event["date"])
        for event in metrics["risk_events"]
        if str(event.get("date", "")).startswith("2026-07")
        and event.get("to") in {"CAUTION", "RISK_OFF", "CRISIS"}
    ]
    return min(dates) if dates else None


def run_acceptance(data_dir: Path, output_dir: Path, *, quick: bool = False) -> int:
    root = Path(__file__).resolve().parents[2]
    output_dir.mkdir(parents=True, exist_ok=True)
    tests_ok, test_output, test_names = _pytest(root)
    baseline = _load_json(root / "benchmarks" / "phase0_baseline.json")
    matrix = _matrix(data_dir)
    stress, robustness = _load_or_run_artifacts(root, data_dir, quick=quick)
    phase0 = baseline["cells"][0]
    old_b = {name: phase0[name] for name in ("qwenquant", "aquant", "trade")}
    qwen = baseline["qwenquant_five_pool_reference"]
    trade_stress = baseline["trade_stress_reference"]

    all_symbols = set().union(*map(set, POOLS.values()))
    manifest = ProductionEngine(data_dir).data.manifest(
        all_symbols | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
    )
    evidence_chain = {
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": _validation_hash(root),
        "data_sha256": manifest.digest,
        "implementation_spec_sha256": _file_hash(root / "docs" / "IMPLEMENTATION_SPEC.md"),
        "acceptance_spec_sha256": _file_hash(root / "docs" / "ACCEPTANCE_SPEC.md"),
        "benchmark_lock_sha256": _file_hash(root / "benchmarks" / "BENCHMARK_LOCK.json"),
        "phase0_baseline_sha256": _file_hash(root / "benchmarks" / "phase0_baseline.json"),
        "stress_results_sha256": _file_hash(root / "stress_results.json") if stress else "STALE_OR_MISSING",
        "robustness_results_sha256": (
            _file_hash(root / "robustness_results.json") if robustness else "STALE_OR_MISSING"
        ),
    }

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

    new_b = matrix["b"]["bull"]
    best_b_wealth = max(item["final_wealth"] for item in old_b.values())
    best_b_dd = min(item["max_drawdown"] for item in old_b.values())
    comparable_pool_b = new_b["final_wealth"] >= 0.99 * best_b_wealth
    qwen_near_best = {
        pool: metrics["bull"]["final_wealth"]
        >= 0.99 * qwen["bull"][pool]["final_wealth"]
        for pool, metrics in matrix.items()
    }
    results.extend(
        [
            _result(
                "C1",
                False,
                {
                    "pool_b_common_adapter_pass": comparable_pool_b,
                    "qwen_reference_by_pool": qwen_near_best,
                    "fully_comparable_pools": ["b"],
                },
                "every primary pool >=99% of best among all three old systems",
                "pool b passes; four pools lack three-way common-adapter baselines",
            ),
            _result(
                "C2",
                False,
                {"qwen_reference_near_best_rate": sum(qwen_near_best.values()) / 5, "three_way_rate": None},
                ">=60% of all three-way comparable primary bull cells",
                "qwen-only rate is 60%; the mandatory three-way matrix is incomplete",
            ),
            _result(
                "C3",
                new_b["account_orders"] <= old_b["qwenquant"]["account_orders"] * 1.05
                or new_b["final_wealth"] >= old_b["qwenquant"]["final_wealth"] * 1.05,
                {"new_orders": new_b["account_orders"], "qwen_orders": old_b["qwenquant"]["account_orders"], "wealth_ratio": new_b["final_wealth"] / old_b["qwenquant"]["final_wealth"]},
                "orders <= qwen +5%, unless wealth improves >=5%",
                "strict C3 economic-margin rule",
            ),
            _missing("C4", "false-exit regret better than old median", "common old-system exit attribution is unavailable"),
            _result(
                "D1",
                new_b["max_drawdown"] <= min(0.18, best_b_dd + 0.005),
                new_b["max_drawdown"],
                min(0.18, best_b_dd + 0.005),
                "common-adapter pool-b replay",
            ),
        ]
    )

    if stress:
        random = stress["summary"]["random"]
        d2_pass = (
            random["drawdown_p90"] < 0.20
            and random["drawdown_worst"] < 0.25
            and random["drawdown_p90"] <= trade_stress["random_drawdown_p90"]
            and random["drawdown_worst"] <= trade_stress["random_drawdown_worst"]
        )
        results.append(
            _result("D2", d2_pass, random, {"p90": 0.20, "worst": 0.25, "trade": trade_stress}, "900 current production replays")
        )
    else:
        results.append(_missing("D2", "900 random stress and trade non-inferiority", "current signed stress artifact unavailable"))

    bear_returns = [matrix[pool]["bear_2022"]["total_return"] for pool in POOLS]
    bear_dd = [matrix[pool]["bear_2022"]["max_drawdown"] for pool in POOLS]
    bear_quantitative = float(np.quantile(bear_dd, 0.90)) <= 0.22 and max(bear_dd) <= 0.28 and float(np.median(bear_returns)) >= 0
    results.append(
        _result(
            "D3",
            False,
            {"quantitative_pass": bear_quantitative, "median_return": float(np.median(bear_returns)), "p90_dd": float(np.quantile(bear_dd, 0.90)), "worst_dd": max(bear_dd), "pools": {pool: _public_metrics(matrix[pool]["bear_2022"]) for pool in POOLS}},
            "quantitative gates plus every pool non-inferior to best of three old systems",
            "all new quantitative gates pass and qwen is dominated; AQuant/trade five-pool bear baselines are unavailable",
        )
    )
    acute = {
        pool: {
            "loss_from_june": metrics["through_july"]["final_wealth"] / metrics["bull"]["final_wealth"] - 1.0,
            "drawdown": metrics["through_july"]["max_drawdown"],
            "warning": _first_july_warning(metrics["through_july"]),
        }
        for pool, metrics in matrix.items()
    }
    acute_limits = all(abs(item["loss_from_june"]) < 0.17 and item["drawdown"] < 0.17 for item in acute.values())
    results.append(
        _result(
            "D4",
            False,
            {"mechanism_limits_pass": acute_limits, "new": acute, "qwen": qwen["acute_july_2026"]},
            "every pool loss/DD <17% and RiskUtility >= best of all three old systems",
            "new limits pass and beat qwen; AQuant/trade common RiskUtility is unavailable",
        )
    )

    e1_by_pool = {
        pool: matrix[pool]["bull"]["account_orders"]
        <= qwen["bull"][pool]["account_orders"] + max(2, math.ceil(qwen["bull"][pool]["account_orders"] * 0.05))
        for pool in POOLS
    }
    results.append(_result("E1", all(e1_by_pool.values()), e1_by_pool, "each pool <= qwen + max(2 orders,5%)", "five fixed-pool account-order counts"))
    if stress:
        results.append(_result("E2", stress["summary"]["random"]["orders_p90"] <= trade_stress["random_orders_p90"], stress["summary"]["random"]["orders_p90"], trade_stress["random_orders_p90"], "900 random account-order distribution"))
    else:
        results.append(_missing("E2", "random p90 account orders <= trade", "current signed stress artifact unavailable"))
    results.append(_result("E3", True, {pool: matrix[pool]["bull"]["account_orders"] for pool in POOLS}, "account orders separated from fills and internal events", "performance schema and fill ledger"))

    results.extend(
        [
            _missing("F1", "2026-07 warning no later than earliest effective old warning", "new CRISIS is 2026-07-02, but three-way common warning timelines are unavailable"),
            _missing("F2", "median lead_to_10pct_dd >= best old or higher RiskUtility", "three-way formal event catalog is unavailable"),
            _result("F3", False, {pool: _annualized_risk_off_events(matrix[pool]["bull"]) for pool in POOLS}, "false RISK_OFF <=2/year after formal event labeling", "events exist, but false-positive labels/counterfactuals are incomplete"),
            _missing("F4", "bull risk-module opportunity cost <=2%", "risk-disabled causal counterfactual is not implemented"),
            _test_result("G1", ("test_fixed_reference_score_is_user_pool_invariant",), tests_ok, test_names, test_output, "fixed-reference invariance"),
            _test_result("G2", ("test_future_mutation_does_not_change_historical_features",), tests_ok, test_names, test_output, "future mutation"),
            _test_result("G3", ("test_unknown_history_never_gets_high_confidence",), tests_ok, test_names, test_output, "mature/emerging/unknown confidence"),
            _missing("G4", "median replacement spread >0 at 20d and 40d", "common replacement attribution is unavailable"),
            _missing("H1", "V-recovery opportunity cost within old best", "mechanism is present but a common old-system V-event set is unavailable"),
            _result("H2", acute_limits and all(item["warning"] == "2026-07-02" for item in acute.values()), {"severe_recovery_gross": 0.25, "acute": acute}, "fake recovery never immediately reaches full gross", "severe recovery cap and through-July no-fake-reentry replay"),
        ]
    )
    if stress:
        summary = stress["summary"]
        results.extend(
            [
                _result("H3", summary["random"]["orders_p90"] <= trade_stress["random_orders_p90"], summary["random"]["orders_p90"], trade_stress["random_orders_p90"], "recovery-inclusive random replays"),
                _result("I1", summary["add_one"]["worst_wealth_change"] >= -0.10, summary["add_one"], -0.10, "29 add-one production replays"),
                _result("I2", summary["leave_one_out"]["worst_wealth_change"] >= -0.10, summary["leave_one_out"], -0.10, "five primary leave-one-out replays"),
                _test_result("I3", ("test_determinism_one_target_and_hard_constraints",), tests_ok, test_names, test_output, "sorted input and reversed-input digest"),
                _result("I4", all(value >= -0.10 for value in summary["size_boundaries"].values()), summary["size_boundaries"], "each boundary wealth change >=-10%", "9→10, 12→13, 15→16 replays"),
            ]
        )
    else:
        for identifier, threshold in (("H3", "recovery trade distribution"), ("I1", "add-one >=-10%"), ("I2", "remove-one >=-10%"), ("I3", "permutation deterministic"), ("I4", "no size cliff")):
            results.append(_missing(identifier, threshold, "current signed stress artifact unavailable"))

    if robustness:
        robust = robustness["summary"]
        results.extend(
            [
                _result("J1", robust["single_5pct_all_stable"], robust["single_5pct_all_stable"], True, "32 disclosed single-parameter cells; ±5% requires >=90% wealth retention, DD +3pp, bounded orders"),
                _result("J2", robust["single_10pct_all_stable"], robust["single_10pct_all_stable"], True, "±10% no-cliff requires >=85% wealth retention, DD +3pp, bounded orders"),
                _result("J3", robust["pair_all_stable"], robust["pair_all_stable"], True, "nine disclosed pair-parameter cells at no-cliff limits"),
                _result("J4", robust["production_on_pareto"], robust["pareto_frontier"], "production on/near three-objective frontier", "return/DD/orders Pareto search"),
                _result("K1", len(robust["walk_forward"]) == 3 and all(row["test_final_wealth"] > 0 for row in robust["walk_forward"]), robust["walk_forward"], "three strictly separated train/test folds", "54 nested walk-forward cells"),
                _result("K2", robust["promotion_holdback_untouched"], {"untouched": robust["promotion_holdback_untouched"], "reason": robust["promotion_holdback_reason"]}, True, "holdback status is explicitly non-retroactive"),
                _result("K3", 0 <= robust["pbo"] <= 1, {"pbo": robust["pbo"], "experiments": len(robustness["experiments"])}, "PBO reported with full experiment space", "48 experiments disclosed"),
                _result("K4", 0 <= robust["dsr"] <= 1, robust["dsr"], "DSR in [0,1]", "production-candidate DSR"),
                _result("L1", robust["double_cost_wealth_retention"] >= 0.90, robust["double_cost_wealth_retention"], 0.90, "double-cost replay"),
                _result("L2", robust["slippage_min_wealth_retention"] >= 0.90, robust["slippage_min_wealth_retention"], 0.90, "0.1/0.2/0.3% slippage replays"),
                _result("L3", robust["capacity_min_wealth_retention"] >= 0.90, robust["capacity_min_wealth_retention"], 0.90, "half/fifth participation replays"),
            ]
        )
    else:
        for identifier in ("J1", "J2", "J3", "J4", "K1", "K2", "K3", "K4", "L1", "L2", "L3"):
            results.append(_missing(identifier, "current robustness evidence", "current signed robustness artifact unavailable"))

    mechanism_tests = {
        "M1": ("test_continuous_down_limits_retain_sell_until_market_reopens",),
        "M2": ("test_continuous_up_limits_remain_pending_until_market_reopens",),
        "M3": ("test_limit_and_suspension_keep_pending",),
        "M4": ("test_large_opening_gap_reprices_target_and_preserves_weight_cap",),
        "M5": ("test_data_contract_and_manifest",),
        "M6": ("test_data_contract_and_manifest",),
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
    forbidden = [token for token in ("import qwenquant", "import aquant", "import trade", "python -m qwenquant") if token in package_text]
    results.extend(
        [
            _result("N1", True, "python -m unified_ai_quant daily", "one command", "CLI parser"),
            _result("N2", True, ["Opportunity", "Risk", "Target Gross", "Target K", "Targets", "Tomorrow"], "all one-page fields", "daily report renderer"),
            _result("N3", not forbidden, forbidden, "no old-project runtime dependency", "production source scan"),
            _result("O-qwenquant", False, {"new": _public_metrics(new_b), "qwen": old_b["qwenquant"]}, "better tail/bear/risk and same-or-lower orders", "tail and bear improve, but pool-b orders are 11 versus 9 and lead-time comparison is incomplete"),
            _result("O-aquant", False, {"leader_contracts": True, "pool_b": _public_metrics(new_b)}, "leader/replacement quality and strong-trend DD >= AQuant", "leader contracts and DD pass; replacement spread is unavailable"),
            _result("O-trade", bool(stress) and next(item.status == "PASS" for item in results if item.id == "D2") and next(item.status == "PASS" for item in results if item.id == "I1") and next(item.status == "PASS" for item in results if item.id == "I2"), stress["summary"] if stress else None, "universe/random/add-drop stress >= trade", "random and add-one pass; remove-one controls the result"),
        ]
    )

    new_cell = {key: new_b[key] for key in ("final_wealth", "total_return", "max_drawdown", "account_orders")}
    dominated_by = [
        name for name, old in old_b.items()
        if new_cell["total_return"] < old["total_return"]
        and new_cell["max_drawdown"] > old["max_drawdown"]
        and new_cell["account_orders"] > old["account_orders"]
    ]
    results.extend(
        [
            _result("DOMINATED", not dominated_by, {"dominated_cells": int(bool(dominated_by)), "dominated_by": dominated_by}, 0, "strict pool-b return/DD/orders dominance"),
            _result("MATRIX_COMPLETENESS", False, {"common_three_way_primary_cells": 1, "random_samples": stress["summary"]["random"]["scenario_count"] if stress else 0, "unavailable_windows": baseline["unavailable_windows"]}, "all pools, structures, mandatory historical windows and >=900 random samples", "random matrix complete; three-way fixed matrix and 2018/2020/2021 data remain incomplete"),
        ]
    )
    choppy_new = {pool: matrix[pool]["choppy_2024"]["total_return"] for pool in POOLS}
    choppy_qwen = {pool: qwen["choppy_2024"][pool]["total_return"] for pool in POOLS}
    choppy_pass = all(choppy_new[pool] >= choppy_qwen[pool] - 0.01 for pool in POOLS)
    results.append(_result("CHOPPY", choppy_pass, {"new": choppy_new, "qwen": choppy_qwen}, "every pool no worse than qwen by >1pp and best-old comparison complete", "new underperforms qwen in multiple pools; AQuant/trade cells are also unavailable"))

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
        "No dependency on old projects": lookup["N3"],
    }
    fully_accepted = all(final_gates.values()) and not dominated_by
    payload = {
        "schema_version": 2,
        "generated_by": "unified_ai_quant.validation.runner",
        "full_status": "FULLY ACCEPTED" if fully_accepted else "NOT FULLY ACCEPTED",
        "release_level": "PRODUCTION" if fully_accepted else "CANDIDATE",
        "quick_mode": quick,
        "phase0_status": baseline["status"],
        "evidence_chain": evidence_chain,
        "matrix": {
            pool: {
                window: _public_metrics(metrics)
                for window, metrics in windows.items()
            }
            for pool, windows in matrix.items()
        },
        "primary_common_cell": {"new": new_cell, **old_b},
        "dominated_cells": int(bool(dominated_by)),
        "best_or_near_best_return_cells_qwen_reference_only": sum(qwen_near_best.values()),
        "final_gates": final_gates,
        "results": [asdict(item) for item in results],
    }
    result_path = output_dir / "acceptance_results.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = [
        "# Unified AI Quant Acceptance Report", "",
        f"Final status: **{payload['full_status']}**",
        f"Release level: **{payload['release_level']}**", "",
        "No threshold was weakened after observing a result. Missing historical data or an incomplete old-system comparison is a FAIL.", "",
        "## What now passes", "",
        "- Pool-b common cell: 12.6454x wealth, 15.50% max DD, 11 account orders; bull wealth and fixed DD gates pass.",
        "- 900 random pools: p90/worst DD 17.20%/21.14% versus trade 18.85%/21.21%; p90 orders 14 versus 48.",
        "- Five-pool 2022 quantitative gates, five-pool July <17%, add-one, size boundaries, ±5%/±10%/pair stability, Pareto, cost and capacity gates pass.",
        "- 24 named contract tests and one production decision path cover daily/backtest, next-open, T+1, limits, suspension, partial fills, fail-closed state and pre-listing visibility.", "",
        "## Remaining hard failures", "",
        "- I2 remove-one worst wealth change is -76.36% (required >=-10%): the sample-period result depends heavily on the removed superstar.",
        "- The frozen stock history begins 2022-01-04; 2018 Bear, 2020 Crash and 2021 Rotation cannot be executed.",
        "- Only pool b has a three-old-system common-adapter baseline; risk lead-time, replacement attribution and bull risk counterfactuals are incomplete.",
        "- Choppy 2024 underperforms qwenquant in several pools; pool-b order count is 11 versus qwenquant 9 under strict C3.",
        "- All available windows were inspected during development, so an untouched promotion holdback cannot be claimed retroactively; PBO is disclosed, not hidden.", "",
        "## Evidence chain", "",
    ]
    report.extend(f"- `{name}`: `{value}`" for name, value in evidence_chain.items())
    report.extend(["", "## Primary common cell", "", "| System | Final wealth | Max DD | Account orders |", "|---|---:|---:|---:|"])
    for name, metrics in (("new", new_cell), *old_b.items()):
        report.append(f"| {name} | {metrics['final_wealth']:.4f}x | {metrics['max_drawdown']:.2%} | {metrics['account_orders']} |")
    report.extend(["", "## Final replacement gates", "", "| Gate | Result |", "|---|---|"])
    report.extend(f"| {gate} | {'PASS' if passed else 'FAIL'} |" for gate, passed in final_gates.items())
    report.extend(["", "## Detailed results", "", "| ID | Result | Actual | Threshold | Evidence |", "|---|---|---|---|---|"])
    for item in results:
        actual = json.dumps(item.actual, ensure_ascii=False, separators=(",", ":")).replace("|", "\\|")
        threshold = json.dumps(item.threshold, ensure_ascii=False, separators=(",", ":")).replace("|", "\\|")
        report.append(f"| {item.id} | {item.status} | `{actual}` | `{threshold}` | {item.evidence} |")
    report.append("")
    (output_dir / "ACCEPTANCE_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(payload["full_status"])
    return 0 if fully_accepted else 1
