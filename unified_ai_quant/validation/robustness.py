"""Parameter, cost, capacity and walk-forward promotion experiments."""

from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import DEFAULT_CONFIG
from ..engine import ProductionEngine, code_fingerprint
from .provenance import (
    assert_replay_signature_unchanged,
    bounded_data_fingerprint,
    config_fingerprint,
    validation_fingerprint,
)
from .statistics import deflated_sharpe_ratio, probability_of_backtest_overfitting

PRIMARY = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
BULL = ("2025-04-01", "2026-06-30")
THROUGH_JULY = ("2025-04-01", "2026-07-20")
CHOPPY = ("2024-01-02", "2024-12-31")
_WORKER_DATA_DIR = "data/frozen"


def promotion_holdback_status(data_dir: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    lock_path = root / "benchmarks" / "PROMOTION_HOLDBACK.json"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    start = str(payload["window"]["start"])
    end = str(payload["window"]["end"])
    digest = hashlib.sha256()
    rows = 0
    files = sorted(data_dir.glob("*.csv"), key=lambda path: path.name.encode())
    dates_by_file: dict[str, tuple[str, ...]] = {}
    for path in files:
        digest.update(f"{path.name}\n".encode())
        window_dates: list[str] = []
        with path.open("rb") as stream:
            next(stream)
            for line in stream:
                date = line.split(b",", 1)[0].decode()
                if start <= date <= end:
                    digest.update(line)
                    window_dates.append(date)
                    rows += 1
        dates_by_file[path.name] = tuple(window_dates)
    actual_hash = digest.hexdigest()
    candidate_hashes = {
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": validation_fingerprint(root),
        "config_sha256": config_fingerprint(),
    }
    locked_candidate_hashes = {
        "production_code_sha256": payload.get("candidate_production_code_sha256"),
        "validation_code_sha256": payload.get("candidate_validation_code_sha256"),
        "config_sha256": payload.get("candidate_config_sha256"),
    }
    candidate_hash_match = candidate_hashes == locked_candidate_hashes
    expected_sessions = int(payload.get("expected_sessions", -1))
    index_sessions = dates_by_file.get("sh000300.csv", ())
    incomplete_files = sorted(
        name
        for name, dates in dates_by_file.items()
        if dates != index_sessions or len(dates) != expected_sessions
    )
    complete_coverage = (
        expected_sessions >= 2
        and len(index_sessions) == expected_sessions
        and not incomplete_files
    )
    sealed = (
        payload.get("status") == "SEALED_UNEVALUATED"
        and actual_hash == payload.get("canonical_sha256")
        and rows == int(payload.get("data_rows", -1))
        and len(files) == int(payload.get("symbol_files", -1))
        and complete_coverage
        and candidate_hash_match
    )
    return {
        "untouched": sealed,
        "lock_status": payload.get("status"),
        "canonical_hash_match": actual_hash == payload.get("canonical_sha256"),
        "canonical_sha256": actual_hash,
        "rows": rows,
        "files": len(files),
        "expected_sessions": expected_sessions,
        "complete_coverage": complete_coverage,
        "incomplete_files": incomplete_files,
        "candidate_hash_match": candidate_hash_match,
        "candidate_hashes": candidate_hashes,
        "locked_candidate_hashes": locked_candidate_hashes,
        "reason": (
            "sealed promotion data and candidate hashes match the preregistered lock"
            if sealed
            else "promotion lock is consumed, data-incomplete, byte-mismatched, or candidate-mismatched"
        ),
    }


def _init_worker(data_dir: str) -> None:
    global _WORKER_DATA_DIR
    _WORKER_DATA_DIR = data_dir


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: str
    experiment_type: str
    changes: dict[str, float | int]


@dataclass(frozen=True, slots=True)
class WindowTask:
    experiment_id: str
    changes: dict[str, float | int]
    window_id: str
    start: str
    end: str


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in (
            "final_wealth",
            "total_return",
            "max_drawdown",
            "account_orders",
            "sharpe",
            "calmar",
            "worst_20d",
            "worst_60d",
            "pending_orders",
        )
    }


def _run_experiment(experiment: Experiment) -> dict[str, Any]:
    engine = ProductionEngine(_WORKER_DATA_DIR, DEFAULT_CONFIG.override(**experiment.changes))
    bull = engine.backtest(symbols=PRIMARY, start=BULL[0], end=BULL[1])
    through_july = engine.backtest(symbols=PRIMARY, start=THROUGH_JULY[0], end=THROUGH_JULY[1])
    choppy = engine.backtest(symbols=PRIMARY, start=CHOPPY[0], end=CHOPPY[1])
    return {
        "experiment_id": experiment.experiment_id,
        "experiment_type": experiment.experiment_type,
        "changes": experiment.changes,
        "bull": _metrics(bull),
        "through_july": _metrics(through_july),
        "choppy": _metrics(choppy),
    }


def _run_window(task: WindowTask) -> dict[str, Any]:
    result = ProductionEngine(
        _WORKER_DATA_DIR, DEFAULT_CONFIG.override(**task.changes)
    ).backtest(symbols=PRIMARY, start=task.start, end=task.end)
    return {
        "experiment_id": task.experiment_id,
        "window_id": task.window_id,
        **_metrics(result),
    }


def build_experiments() -> list[Experiment]:
    experiments = [Experiment("production", "production", {})]
    critical = (
        "recovery_target_gross",
        "tactical_rebound_weight",
        "tactical_rebound_take_profit",
        "recovery_crash_drawdown",
        "recovery_breadth_min",
        "concentrated_break_dd",
        "concentrated_break_ratio",
        "severe_shock_ret5",
        "minimum_median_amount",
        "min_trade_weight",
        "add_tranche_cooldown_sessions",
        "add_index_chase_ret5",
        "portfolio_break_dd",
        "recovery_cohort_graduation_days",
        "recovery_weak_market_min_index_ret60",
        "leader_cycle_min_market_ret120",
        "strategic_cohort_min_ret240",
        "strategic_cohort_confirm_days",
        "strategic_reversal_max_ret240",
        "strategic_reversal_min_ret5",
        "strategic_reversal_min_median_ret20",
        "strategic_reversal_max_tech_ret120",
        "strategic_reversal_confirm_days",
        "strategic_cohort_profit_arm",
        "strategic_cohort_trail_atr",
        "strategic_cohort_trail_spacing",
        "strategic_cohort_exit_step",
        "strategic_cohort_disaster_stop",
        "strategic_cohort_tail_line",
        "narrow_anchor_guard_gross",
        "narrow_anchor_divergence",
        "recovery_reserve_min_score",
        "recovery_reserve_min_ret60",
        "recovery_reserve_min_ret120",
        "recovery_substitution_edge",
        "recovery_substitution_shock_window",
        "incomplete_universe_tail_dd",
        "unbacked_universe_tail_dd",
        "unbacked_recovery_anchor_min_days",
        "incomplete_universe_crisis_gross",
        "incomplete_universe_rearm_days",
    )
    integer_parameters = {
        "recovery_cohort_graduation_days",
        "add_tranche_cooldown_sessions",
        "strategic_cohort_confirm_days",
        "strategic_reversal_confirm_days",
        "recovery_substitution_shock_window",
        "unbacked_recovery_anchor_min_days",
        "incomplete_universe_rearm_days",
    }
    for parameter in critical:
        base = float(getattr(DEFAULT_CONFIG, parameter))
        for percent in (-10, -5, 5, 10):
            value = base * (1.0 + percent / 100.0)
            if parameter in integer_parameters:
                value = (
                    math.floor(value)
                    if percent < 0
                    else math.ceil(value)
                )
            if parameter == "recovery_target_gross":
                value = min(DEFAULT_CONFIG.max_gross, value)
            experiments.append(
                Experiment(
                    f"single-{parameter}-{percent:+d}",
                    "single_parameter",
                    {parameter: value},
                )
            )
    for gross_percent in (-10, 0, 10):
        for shock_percent in (-10, 0, 10):
            experiments.append(
                Experiment(
                    f"pair-gross-{gross_percent:+d}-shock-{shock_percent:+d}",
                    "pair_parameter",
                    {
                        "recovery_target_gross": min(
                            DEFAULT_CONFIG.max_gross,
                            DEFAULT_CONFIG.recovery_target_gross
                            * (1.0 + gross_percent / 100.0),
                        ),
                        "severe_shock_ret5": DEFAULT_CONFIG.severe_shock_ret5
                        * (1.0 + shock_percent / 100.0),
                    },
                )
            )
    experiments.extend(
        [
            Experiment(
                "cost-double",
                "cost",
                {
                    "commission_rate": DEFAULT_CONFIG.commission_rate * 2,
                    "stamp_duty": DEFAULT_CONFIG.stamp_duty * 2,
                    "transfer_fee": DEFAULT_CONFIG.transfer_fee * 2,
                },
            ),
            Experiment("slippage-0.1pct", "cost", {"slippage": 0.001}),
            Experiment("slippage-0.2pct", "cost", {"slippage": 0.002}),
            Experiment("slippage-0.3pct", "cost", {"slippage": 0.003}),
            Experiment("capacity-half", "capacity", {"max_volume_participation": 0.0025}),
            Experiment("capacity-fifth", "capacity", {"max_volume_participation": 0.001}),
        ]
    )
    return experiments


def _signature(data_dir: Path, experiments: list[Experiment]) -> dict[str, str]:
    data_hash = bounded_data_fingerprint(data_dir, end=THROUGH_JULY[1])
    return {
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": validation_fingerprint(),
        "data_sha256": data_hash,
        "config_sha256": config_fingerprint(),
        "experiments_sha256": hashlib.sha256(
            json.dumps(
                [
                    {
                        "id": item.experiment_id,
                        "type": item.experiment_type,
                        "changes": item.changes,
                    }
                    for item in experiments
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }


def artifact_is_current(path: Path, data_dir: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("signature") == _signature(data_dir, build_experiments())


def _stable(
    row: dict[str, Any], base: dict[str, Any], *, minimum_wealth_retention: float
) -> bool:
    return all(
        row[window]["final_wealth"]
        >= minimum_wealth_retention * base[window]["final_wealth"]
        and row[window]["max_drawdown"]
        <= base[window]["max_drawdown"] + 0.03
        and row[window]["account_orders"]
        <= base[window]["account_orders"]
        + max(2, math.ceil(0.20 * base[window]["account_orders"]))
        for window in ("bull", "through_july", "choppy")
    )


def _pareto(rows: list[dict[str, Any]]) -> list[str]:
    frontier: list[str] = []
    for candidate in rows:
        dominated = any(
            all(
                other[window]["final_wealth"]
                >= candidate[window]["final_wealth"]
                and other[window]["max_drawdown"]
                <= candidate[window]["max_drawdown"]
                and other[window]["account_orders"]
                <= candidate[window]["account_orders"]
                for window in ("bull", "through_july", "choppy")
            )
            and any(
                other[window]["final_wealth"]
                > candidate[window]["final_wealth"]
                or other[window]["max_drawdown"]
                < candidate[window]["max_drawdown"]
                or other[window]["account_orders"]
                < candidate[window]["account_orders"]
                for window in ("bull", "through_july", "choppy")
            )
            for other in rows
            if other is not candidate
        )
        if not dominated:
            frontier.append(candidate["experiment_id"])
    return sorted(frontier)


def run_robustness(
    data_dir: Path, output_path: Path, *, workers: int | None = None
) -> dict[str, Any]:
    experiments = build_experiments()
    initial_signature = _signature(data_dir, experiments)
    worker_count = workers or min(4, max(1, os.cpu_count() or 1))
    print(f"robustness: executing {len(experiments)} parameter/cost experiments", flush=True)
    with ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_init_worker,
        initargs=(str(data_dir),),
    ) as pool:
        rows = list(pool.map(_run_experiment, experiments, chunksize=1))
    rows.sort(key=lambda row: row["experiment_id"])
    base = next(row for row in rows if row["experiment_id"] == "production")
    singles = [row for row in rows if row["experiment_type"] == "single_parameter"]
    pairs = [row for row in rows if row["experiment_type"] == "pair_parameter"]
    costs = [row for row in rows if row["experiment_type"] == "cost"]
    capacity = [row for row in rows if row["experiment_type"] == "capacity"]

    variants = [item for item in experiments if item.experiment_type == "pair_parameter"]
    folds = (
        ("fold1_train", "2022-01-04", "2023-12-29"),
        ("fold1_test", "2024-01-02", "2024-12-31"),
        ("fold2_train", "2023-01-03", "2024-12-31"),
        ("fold2_test", "2025-01-02", "2025-06-30"),
        ("fold3_train", "2024-01-02", "2025-06-30"),
        ("fold3_test", "2025-07-01", "2025-12-31"),
    )
    tasks = [
        WindowTask(variant.experiment_id, variant.changes, window_id, start, end)
        for variant in variants
        for window_id, start, end in folds
    ]
    print(f"robustness: executing {len(tasks)} nested walk-forward cells", flush=True)
    with ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_init_worker,
        initargs=(str(data_dir),),
    ) as pool:
        window_rows = list(pool.map(_run_window, tasks, chunksize=1))
    lookup = {
        (row["experiment_id"], row["window_id"]): row for row in window_rows
    }
    train_matrix: list[list[float]] = []
    test_matrix: list[list[float]] = []
    walk_forward: list[dict[str, Any]] = []
    for fold in range(1, 4):
        train_id = f"fold{fold}_train"
        test_id = f"fold{fold}_test"
        train_scores = [
            lookup[(variant.experiment_id, train_id)]["sharpe"] for variant in variants
        ]
        test_scores = [
            lookup[(variant.experiment_id, test_id)]["sharpe"] for variant in variants
        ]
        winner = int(np.nanargmax(train_scores))
        train_matrix.append(train_scores)
        test_matrix.append(test_scores)
        walk_forward.append(
            {
                "fold": fold,
                "selected_experiment": variants[winner].experiment_id,
                "train_sharpe": train_scores[winner],
                "test_sharpe": test_scores[winner],
                "test_final_wealth": lookup[(variants[winner].experiment_id, test_id)][
                    "final_wealth"
                ],
            }
        )
    pbo = probability_of_backtest_overfitting(
        np.asarray(train_matrix, dtype=float), np.asarray(test_matrix, dtype=float)
    )
    dsr = deflated_sharpe_ratio(
        float(base["bull"]["sharpe"]),
        trials=len(experiments),
        observations=302,
    )
    candidate_rows = [row for row in rows if row["experiment_type"] in {"production", "single_parameter", "pair_parameter"}]
    frontier = _pareto(candidate_rows)
    holdback = promotion_holdback_status(data_dir)
    current_signature = _signature(data_dir, experiments)
    assert_replay_signature_unchanged(
        initial_signature,
        current_signature,
        replay="robustness",
    )
    payload = {
        "schema_version": 1,
        "signature": initial_signature,
        "summary": {
            "single_5pct_all_stable": all(
                _stable(row, base, minimum_wealth_retention=0.90)
                for row in singles
                if row["experiment_id"].endswith(("-5", "+5"))
            ),
            "single_10pct_all_stable": all(
                _stable(row, base, minimum_wealth_retention=0.85)
                for row in singles
                if row["experiment_id"].endswith(("-10", "+10"))
            ),
            "pair_all_stable": all(
                _stable(row, base, minimum_wealth_retention=0.85) for row in pairs
            ),
            "pareto_frontier": frontier,
            "production_on_pareto": "production" in frontier,
            "double_cost_wealth_retention": next(
                row for row in costs if row["experiment_id"] == "cost-double"
            )["bull"]["final_wealth"]
            / base["bull"]["final_wealth"],
            "slippage_min_wealth_retention": min(
                row["bull"]["final_wealth"] / base["bull"]["final_wealth"]
                for row in costs
                if row["experiment_id"].startswith("slippage")
            ),
            "capacity_min_wealth_retention": min(
                row["bull"]["final_wealth"] / base["bull"]["final_wealth"]
                for row in capacity
            ),
            "pbo": pbo,
            "dsr": dsr,
            "walk_forward": walk_forward,
            "promotion_holdback_untouched": holdback["untouched"],
            "promotion_holdback_reason": holdback["reason"],
            "promotion_holdback_evidence": holdback,
        },
        "experiments": rows,
        "walk_forward_cells": window_rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
