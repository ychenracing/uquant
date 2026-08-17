#!/usr/bin/env python3
"""Build and strictly evaluate the exact 2025-2026 four-system matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uquant.atomic_io import atomic_write_text, validate_atomic_output_boundary
from uquant.engine import ProductionEngine

TARGET_START = "2025-01-02"
TARGET_END = "2026-07-31"
ACUTE_START = "2026-06-30"
ACUTE_END = TARGET_END
SYSTEMS = ("uquant", "aquant", "qwenquant", "trade")
COMPETITORS = SYSTEMS[1:]
POOLS = ("a", "b", "c", "d", "e")
METRICS = ("final_wealth", "max_drawdown", "account_orders", "acute_return")


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _acute_return(curve: Sequence[Mapping[str, Any]]) -> float:
    points: dict[str, float] = {}
    for item in curve:
        date = str(item.get("date", ""))
        if date in points:
            raise RuntimeError(f"duplicate acute equity date: {date}")
        try:
            equity = float(item["equity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("acute equity curve is malformed") from exc
        if not math.isfinite(equity) or equity <= 0:
            raise RuntimeError("acute equity curve is malformed")
        points[date] = equity
    if ACUTE_START not in points or ACUTE_END not in points:
        raise RuntimeError("acute interval boundaries are absent")
    return points[ACUTE_END] / points[ACUTE_START] - 1.0


def _finite(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"outperformance metric is malformed: {field}")
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"outperformance metric is malformed: {field}")
    return result


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Validate the complete target-window matrix and return uniquely keyed rows."""

    expected = {(system, pool) for system in SYSTEMS for pool in POOLS}
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row.get("system", "")), str(row.get("pool", "")))
        if key in indexed:
            raise RuntimeError(f"duplicate outperformance cell: {key[0]}/{key[1]}")
        indexed[key] = row
        if row.get("start") != TARGET_START or row.get("end") != TARGET_END:
            raise RuntimeError(f"outperformance target interval mismatch: {key[0]}/{key[1]}")
        wealth = _finite(row, "final_wealth")
        drawdown = _finite(row, "max_drawdown")
        acute = _finite(row, "acute_return")
        turnover = _finite(row, "gross_turnover")
        orders = row.get("account_orders")
        digest = row.get("evidence_sha256")
        if wealth <= 0 or not 0 <= drawdown <= 1 or acute <= -1 or turnover < 0:
            raise RuntimeError(f"outperformance cell metrics are invalid: {key[0]}/{key[1]}")
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise RuntimeError(f"outperformance account orders are invalid: {key[0]}/{key[1]}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise RuntimeError(f"outperformance evidence hash is invalid: {key[0]}/{key[1]}")
    missing = sorted(expected - set(indexed))
    unexpected = sorted(set(indexed) - expected)
    if missing:
        raise RuntimeError(f"missing outperformance cells: {missing}")
    if unexpected:
        raise RuntimeError(f"unexpected outperformance cells: {unexpected}")
    return indexed


def evaluate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require uquant to Pareto-dominate every competitor in every pool."""
    indexed = _validate_rows(rows)
    comparisons: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for pool in POOLS:
        candidate = indexed[("uquant", pool)]
        for competitor in COMPETITORS:
            reference = indexed[(competitor, pool)]
            predicates = {
                "final_wealth": _finite(candidate, "final_wealth")
                >= _finite(reference, "final_wealth"),
                "max_drawdown": _finite(candidate, "max_drawdown")
                <= _finite(reference, "max_drawdown"),
                "account_orders": int(candidate["account_orders"])
                <= int(reference["account_orders"]),
                "acute_return": _finite(candidate, "acute_return")
                >= _finite(reference, "acute_return"),
            }
            strictly_better = any(
                (
                    _finite(candidate, field) > _finite(reference, field)
                    if field in {"final_wealth", "acute_return"}
                    else _finite(candidate, field) < _finite(reference, field)
                )
                for field in METRICS
            )
            predicates["strictly_better"] = strictly_better
            name = f"{pool}/{competitor}"
            for predicate, passed in predicates.items():
                if not passed:
                    failures.append(f"{name}:{predicate}")
            comparisons[name] = {
                "passed": all(predicates.values()),
                "predicates": predicates,
                "uquant": {field: candidate[field] for field in METRICS},
                "competitor": {field: reference[field] for field in METRICS},
            }
    return {
        "passed": not failures,
        "failures": failures,
        "summary": {"cells": len(indexed), "pairwise_comparisons": len(comparisons)},
        "comparisons": comparisons,
    }


def _promotion_pools(repository_root: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(
        (repository_root / "benchmarks" / "promotion_baseline.json").read_text(encoding="utf-8")
    )
    raw = payload.get("pools")
    if not isinstance(raw, dict) or set(raw) != set(POOLS):
        raise RuntimeError("promotion baseline must contain exactly pools A-E")
    return {pool: tuple(str(item) for item in raw[pool]) for pool in POOLS}


def _compact_competitor_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize the validated frozen artifact into comparable matrix rows."""

    if payload.get("schema_version") != 2:
        raise RuntimeError("unsupported window competitor artifact schema")
    if tuple(payload.get("systems", ())) != COMPETITORS:
        raise RuntimeError("window competitor systems are incomplete or reordered")
    if tuple(payload.get("pools", ())) != POOLS or payload.get("windows") != ["target"]:
        raise RuntimeError("window competitor pools or target window are incomplete")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise RuntimeError("window competitor rows are malformed")
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise RuntimeError("window competitor row is malformed")
        rows.append(
            {
                "system": str(raw.get("system")),
                "pool": str(raw.get("pool")),
                "start": raw.get("start"),
                "end": raw.get("end"),
                "final_wealth": _finite(raw, "final_wealth"),
                "max_drawdown": _finite(raw, "max_drawdown"),
                "account_orders": raw.get("account_orders"),
                "gross_turnover": _finite(raw, "turnover"),
                "acute_return": _acute_return(raw.get("equity_curve", [])),
                "evidence_sha256": _canonical_hash(raw),
            }
        )
    expected = {(system, pool) for system in COMPETITORS for pool in POOLS}
    observed = {(row["system"], row["pool"]) for row in rows}
    if len(rows) != len(observed):
        raise RuntimeError("duplicate compact competitor cell")
    if observed != expected:
        raise RuntimeError("compact competitor matrix is incomplete")
    return rows


def _uquant_task(task: tuple[str, tuple[str, ...], str]) -> dict[str, Any]:
    pool, symbols, data_dir = task
    raw = ProductionEngine(data_dir).backtest(
        symbols=symbols,
        start=TARGET_START,
        end=TARGET_END,
    )
    evidence = {
        "decision_digests": raw["decision_digests"],
        "equity_curve": raw["equity_curve"],
        "order_ledger": raw["order_ledger"],
        "risk_events": raw["risk_events"],
    }
    return {
        "system": "uquant",
        "pool": pool,
        "start": TARGET_START,
        "end": TARGET_END,
        "final_wealth": float(raw["final_wealth"]),
        "max_drawdown": float(raw["max_drawdown"]),
        "account_orders": int(raw["account_orders"]),
        "gross_turnover": float(raw["gross_turnover"]),
        "acute_return": _acute_return(raw["equity_curve"]),
        "evidence_sha256": _canonical_hash(evidence),
    }


def _python_source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_executable() -> str:
    """Resolve Git explicitly so provenance commands never invoke a shell."""

    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for window provenance")
    return executable


def build(
    *,
    repository_root: Path,
    competitor_path: Path,
    workers: int,
) -> dict[str, Any]:
    """Build and evaluate the content-addressed target-window matrix."""

    competitor_bytes = competitor_path.read_bytes()
    competitor_payload = json.loads(competitor_bytes.decode("utf-8"))
    competitor_rows = _compact_competitor_rows(competitor_payload)
    pools = _promotion_pools(repository_root)
    tasks = [(pool, pools[pool], str(repository_root / "data" / "frozen")) for pool in POOLS]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_uquant_task, task): task[0] for task in tasks}
        uquant_rows = [future.result() for future in as_completed(futures)]
    pool_order = {pool: index for index, pool in enumerate(POOLS)}
    uquant_rows.sort(key=lambda row: pool_order[str(row["pool"])])
    rows = uquant_rows + competitor_rows
    report = evaluate(rows)
    completed = subprocess.run(
        [_git_executable(), "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    commit = completed.stdout.strip()
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "target_interval": {"start": TARGET_START, "end": TARGET_END},
        "acute_interval": {"start": ACUTE_START, "end": ACUTE_END},
        "comparison_contract": {
            "initial_cash": 2_000_000.0,
            "signal": "close_t",
            "execution": "next_tradable_open",
            "intraday_exit": False,
            "dominance_metrics": list(METRICS),
        },
        "provenance": {
            "uquant_commit": commit,
            "uquant_python_sha256": _python_source_hash(repository_root / "uquant"),
            "competitor_artifact_sha256": hashlib.sha256(competitor_bytes).hexdigest(),
            "competitor_repositories": competitor_payload.get("repositories"),
            "data": competitor_payload.get("data_provenance"),
        },
        "rows": rows,
        "evaluation": report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write one target-window report and return its gate status."""

    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competitor-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    protected_inputs = validate_atomic_output_boundary(
        args.output,
        protected_paths=(
            args.competitor_results,
            repository_root / "benchmarks" / "promotion_baseline.json",
        ),
        protected_roots=(
            repository_root / "data" / "frozen",
            repository_root / "uquant",
        ),
    )
    payload = build(
        repository_root=repository_root,
        competitor_path=args.competitor_results,
        workers=args.workers,
    )
    atomic_write_text(
        args.output,
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        protected_paths=protected_inputs,
    )
    print(json.dumps(payload["evaluation"], ensure_ascii=False, indent=2))
    return 0 if payload["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
