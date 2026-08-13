#!/usr/bin/env python3
"""Build and strictly evaluate the five-window four-system Pareto matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.window_matrix import (
    ACUTE_WINDOWS,
    COMPARISON_CONTRACT,
    INITIAL_CASH,
    LOCKED_COMPETITOR_SOURCES,
    WINDOW_SPECS,
    WINDOWS,
)
from uquant.engine import ProductionEngine

SYSTEMS = ("uquant", "aquant", "qwenquant", "trade")
COMPETITORS = SYSTEMS[1:]
POOLS = ("a", "b", "c", "d", "e")
METRICS = ("final_wealth", "max_drawdown", "account_orders", "acute_return")
TARGET_END = max(end for _, end in WINDOWS.values())


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


def _finite(row: Mapping[str, Any], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"outperformance metric is malformed: {field}")
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"outperformance metric is malformed: {field}")
    return result


def _acute_return(curve: Sequence[Mapping[str, Any]], window: str) -> float:
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
    acute_start, acute_end = ACUTE_WINDOWS[window]
    if acute_start not in points or acute_end not in points:
        raise RuntimeError(f"acute interval boundaries are absent: {window}")
    return points[acute_end] / points[acute_start] - 1.0


def _bounded_data_fingerprint(root: Path, *, end: str = TARGET_END) -> str:
    """Hash the exact canonical CSV snapshot visible through ``end``."""
    digest = hashlib.sha256()
    paths = sorted(root.glob("*.csv"), key=lambda path: path.name)
    if not paths:
        raise RuntimeError(f"no canonical market-data CSVs found in {root}")
    cutoff = end.encode("ascii")
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as reader:
            header = reader.readline()
            if not header.lower().startswith(b"date,"):
                raise RuntimeError(f"canonical CSV lacks a date header: {path}")
            digest.update(header)
            for line in reader:
                date, separator, _ = line.partition(b",")
                if not separator:
                    raise RuntimeError(f"malformed canonical CSV row: {path}")
                if date <= cutoff:
                    digest.update(line)
    return digest.hexdigest()


def _effective_symbols(
    symbols: Sequence[str],
    *,
    data_dir: Path,
    as_of: str,
) -> tuple[str, ...]:
    """Recompute point-in-time pool membership from canonical first rows."""
    boundary = as_of.encode("ascii")
    visible: list[str] = []
    for symbol in symbols:
        candidates = (data_dir / f"{symbol}.csv", data_dir / f"{symbol[2:]}.csv")
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise RuntimeError(f"canonical pool symbol is missing: {symbol}")
        with path.open("rb") as reader:
            header = reader.readline()
            if not header.lower().startswith(b"date,"):
                raise RuntimeError(f"canonical CSV lacks a date header: {path}")
            first = reader.readline()
        first_date, separator, _ = first.partition(b",")
        if separator and first_date <= boundary:
            visible.append(symbol)
    if not visible:
        raise RuntimeError("promotion pool has no visible symbols at window start")
    return tuple(visible)


def _evidence_metrics(evidence: Mapping[str, Any], *, window: str) -> dict[str, float | int]:
    """Independently recompute every gate metric from retained raw evidence."""
    curve = evidence.get("equity_curve")
    ledger = evidence.get("order_ledger")
    fills = evidence.get("fills")
    if not isinstance(curve, list) or not isinstance(ledger, list) or not isinstance(fills, list):
        raise RuntimeError("outperformance raw evidence is malformed")
    equities: list[float] = []
    for point in curve:
        if not isinstance(point, Mapping):
            raise RuntimeError("outperformance equity evidence is malformed")
        value = _finite(point, "equity")
        if value <= 0:
            raise RuntimeError("outperformance equity evidence is malformed")
        equities.append(value)
    if not equities:
        raise RuntimeError("outperformance equity evidence is empty")
    peak = equities[0]
    drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    turnover_value = 0.0
    for fill in fills:
        if not isinstance(fill, Mapping):
            raise RuntimeError("outperformance fill evidence is malformed")
        gross = fill.get("gross_value")
        if gross is None:
            gross = abs(_finite(fill, "price") * int(fill.get("shares", 0)))
        if isinstance(gross, bool) or not isinstance(gross, (int, float)):
            raise RuntimeError("outperformance fill evidence is malformed")
        turnover_value += abs(float(gross))
    return {
        "final_wealth": equities[-1] / INITIAL_CASH,
        "max_drawdown": drawdown,
        "account_orders": len(ledger),
        "gross_turnover": turnover_value / INITIAL_CASH,
        "acute_return": _acute_return(curve, window),
    }


def _validate_evidence(
    rows: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any],
) -> None:
    referenced: set[str] = set()
    for row in rows:
        digest = str(row["evidence_sha256"])
        raw = evidence.get(digest)
        if not isinstance(raw, Mapping) or _canonical_hash(raw) != digest:
            raise RuntimeError("outperformance evidence is missing or not content-addressed")
        referenced.add(digest)
        recomputed = _evidence_metrics(raw, window=str(row["window"]))
        for field in METRICS:
            observed = float(row[field])
            expected = float(recomputed[field])
            if not math.isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-10):
                raise RuntimeError(f"outperformance evidence metric mismatch: {field}")
        if not math.isclose(
            _finite(row, "gross_turnover"),
            float(recomputed["gross_turnover"]),
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise RuntimeError("outperformance evidence metric mismatch: gross_turnover")
    if set(evidence) != referenced:
        raise RuntimeError("outperformance evidence contains unreferenced blobs")


def _validate_rows(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    expected = {
        (system, pool, window)
        for system in SYSTEMS
        for pool in POOLS
        for window in WINDOWS
    }
    indexed: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("system", "")),
            str(row.get("pool", "")),
            str(row.get("window", "")),
        )
        if key in indexed:
            raise RuntimeError(
                f"duplicate outperformance cell: {key[2]}/{key[1]}/{key[0]}"
            )
        indexed[key] = row
        if key[2] not in WINDOWS or (row.get("start"), row.get("end")) != WINDOWS[key[2]]:
            raise RuntimeError(
                f"outperformance window mismatch: {key[2]}/{key[1]}/{key[0]}"
            )
        wealth = _finite(row, "final_wealth")
        drawdown = _finite(row, "max_drawdown")
        acute = _finite(row, "acute_return")
        turnover = _finite(row, "gross_turnover")
        orders = row.get("account_orders")
        digest = row.get("evidence_sha256")
        if wealth <= 0 or not 0 <= drawdown <= 1 or acute <= -1 or turnover < 0:
            raise RuntimeError("outperformance cell metrics are invalid")
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise RuntimeError("outperformance account orders are invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise RuntimeError("outperformance evidence hash is invalid")
    missing = sorted(expected - set(indexed))
    unexpected = sorted(set(indexed) - expected)
    if missing:
        raise RuntimeError(f"missing outperformance cells: {missing}")
    if unexpected:
        raise RuntimeError(f"unexpected outperformance cells: {unexpected}")
    return indexed


def evaluate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require uquant to Pareto-dominate every competitor in every cell."""
    indexed = _validate_rows(rows)
    comparisons: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for window in WINDOWS:
        for pool in POOLS:
            candidate = indexed[("uquant", pool, window)]
            for competitor in COMPETITORS:
                reference = indexed[(competitor, pool, window)]
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
                predicates["strictly_better"] = any(
                    (
                        _finite(candidate, field) > _finite(reference, field)
                        if field in {"final_wealth", "acute_return"}
                        else _finite(candidate, field) < _finite(reference, field)
                    )
                    for field in METRICS
                )
                name = f"{window}/{pool}/{competitor}"
                for predicate, predicate_passed in predicates.items():
                    if not predicate_passed:
                        failures.append(f"{name}:{predicate}")
                comparisons[name] = {
                    "passed": all(predicates.values()),
                    "predicates": predicates,
                    "uquant": {field: candidate[field] for field in METRICS},
                    "competitor": {field: reference[field] for field in METRICS},
                }
    passed_count = sum(item["passed"] for item in comparisons.values())
    return {
        "passed": not failures,
        "failures": failures,
        "summary": {
            "cells": len(indexed),
            "pairwise_comparisons": len(comparisons),
            "passed": passed_count,
        },
        "comparisons": comparisons,
    }


def _promotion_pools(repository_root: Path) -> dict[str, tuple[str, ...]]:
    payload = json.loads(
        (repository_root / "benchmarks" / "promotion_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    raw = payload.get("pools")
    if not isinstance(raw, dict) or set(raw) != set(POOLS):
        raise RuntimeError("promotion baseline must contain exactly pools A-E")
    return {pool: tuple(str(item) for item in raw[pool]) for pool in POOLS}


def _compact_competitor_rows(
    payload: Mapping[str, Any],
    *,
    repository_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if payload.get("schema_version") != 2:
        raise RuntimeError("unsupported window competitor artifact schema")
    if tuple(payload.get("systems", ())) != COMPETITORS:
        raise RuntimeError("window competitor systems are incomplete or reordered")
    if tuple(payload.get("pools", ())) != POOLS or tuple(payload.get("windows", ())) != tuple(WINDOWS):
        raise RuntimeError("five-window competitor matrix is incomplete or reordered")
    if payload.get("contract") != COMPARISON_CONTRACT:
        raise RuntimeError("window competitor execution contract mismatch")
    if payload.get("repositories") != LOCKED_COMPETITOR_SOURCES:
        raise RuntimeError("window competitor repository locks mismatch")
    expected_source_hashes = {
        system: LOCKED_COMPETITOR_SOURCES[system]["python_sha256"]
        for system in COMPETITORS
    }
    if payload.get("source_hashes") != expected_source_hashes:
        raise RuntimeError("window competitor source hashes mismatch")
    adapter_path = repository_root / "scripts" / "run_window_competitor_adapter.py"
    if payload.get("adapter_sha256") != hashlib.sha256(adapter_path.read_bytes()).hexdigest():
        raise RuntimeError("window competitor adapter hash mismatch")
    data_dir = repository_root / "data" / "frozen"
    expected_data = {
        "through": TARGET_END,
        "sha256": _bounded_data_fingerprint(data_dir),
    }
    if payload.get("data_provenance") != expected_data:
        raise RuntimeError("window competitor data provenance mismatch")
    pools = _promotion_pools(repository_root)
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise RuntimeError("window competitor rows are malformed")
    rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise RuntimeError("window competitor row is malformed")
        window = str(raw.get("window"))
        pool = str(raw.get("pool"))
        if pool not in pools or window not in WINDOWS:
            raise RuntimeError("window competitor row identity is malformed")
        requested = list(pools[pool])
        effective = list(
            _effective_symbols(
                pools[pool],
                data_dir=data_dir,
                as_of=WINDOWS[window][0],
            )
        )
        if raw.get("requested_symbols") != requested:
            raise RuntimeError("window competitor requested pool membership mismatch")
        if raw.get("effective_symbols") != effective:
            raise RuntimeError("window competitor effective pool membership mismatch")
        digest = _canonical_hash(raw)
        evidence[digest] = raw
        rows.append(
            {
                "system": str(raw.get("system")),
                "pool": pool,
                "window": window,
                "start": raw.get("start"),
                "end": raw.get("end"),
                "final_wealth": _finite(raw, "final_wealth"),
                "max_drawdown": _finite(raw, "max_drawdown"),
                "account_orders": raw.get("account_orders"),
                "gross_turnover": _finite(raw, "turnover"),
                "acute_return": _acute_return(raw.get("equity_curve", []), window),
                "evidence_sha256": digest,
            }
        )
    expected = {
        (system, pool, window)
        for system in COMPETITORS
        for pool in POOLS
        for window in WINDOWS
    }
    observed = {(row["system"], row["pool"], row["window"]) for row in rows}
    if len(rows) != len(observed):
        raise RuntimeError("duplicate compact competitor cell")
    if observed != expected:
        raise RuntimeError("compact competitor matrix is incomplete")
    _validate_evidence(rows, evidence)
    return rows, evidence


def _uquant_task(task: tuple[str, tuple[str, ...], str, str]) -> dict[str, Any]:
    pool, symbols, window, data_dir = task
    start, end = WINDOWS[window]
    raw = ProductionEngine(data_dir).backtest(symbols=symbols, start=start, end=end)
    evidence = {
        "initial_cash": INITIAL_CASH,
        "decision_digests": raw["decision_digests"],
        "equity_curve": raw["equity_curve"],
        "order_ledger": raw["order_ledger"],
        "fills": raw["final_account"]["fills"],
        "risk_events": raw["risk_events"],
    }
    return {
        "row": {
            "system": "uquant",
            "pool": pool,
            "window": window,
            "start": start,
            "end": end,
            "final_wealth": float(raw["final_wealth"]),
            "max_drawdown": float(raw["max_drawdown"]),
            "account_orders": int(raw["account_orders"]),
            "gross_turnover": float(raw["gross_turnover"]),
            "acute_return": _acute_return(raw["equity_curve"], window),
            "evidence_sha256": _canonical_hash(evidence),
        },
        "evidence": evidence,
    }


def _python_source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_identity(repository_root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise RuntimeError("five-window evidence requires a clean committed worktree")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build(*, repository_root: Path, competitor_path: Path, workers: int) -> dict[str, Any]:
    commit = _git_identity(repository_root)
    competitor_bytes = competitor_path.read_bytes()
    competitor_payload = json.loads(competitor_bytes.decode("utf-8"))
    competitor_rows, evidence = _compact_competitor_rows(
        competitor_payload,
        repository_root=repository_root,
    )
    pools = _promotion_pools(repository_root)
    tasks = [
        (pool, pools[pool], window, str(repository_root / "data" / "frozen"))
        for pool in POOLS
        for window in WINDOWS
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_uquant_task, task): task[:3] for task in tasks}
        uquant_results = [future.result() for future in as_completed(futures)]
    uquant_rows: list[dict[str, Any]] = []
    for result in uquant_results:
        row = result["row"]
        raw_evidence = result["evidence"]
        digest = str(row["evidence_sha256"])
        if _canonical_hash(raw_evidence) != digest:
            raise RuntimeError("uquant worker returned non-addressed evidence")
        evidence[digest] = raw_evidence
        uquant_rows.append(row)
    system_order = {value: index for index, value in enumerate(SYSTEMS)}
    pool_order = {value: index for index, value in enumerate(POOLS)}
    window_order = {value: index for index, value in enumerate(WINDOWS)}
    rows = uquant_rows + competitor_rows
    rows.sort(
        key=lambda row: (
            system_order[str(row["system"])],
            pool_order[str(row["pool"])],
            window_order[str(row["window"])],
        )
    )
    _validate_evidence(rows, evidence)
    report = evaluate(rows)
    runner_path = repository_root / "scripts" / "run_five_window_outperformance.py"
    definition_path = repository_root / "research" / "window_matrix.py"
    return {
        "schema_version": 2,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "windows": {name: {"start": start, "end": end} for name, (start, end) in WINDOWS.items()},
        "requested_windows": {
            spec.name: {
                "start": spec.requested_start,
                "end": spec.requested_end,
            }
            for spec in WINDOW_SPECS
        },
        "acute_windows": {
            name: {"start": start, "end": end}
            for name, (start, end) in ACUTE_WINDOWS.items()
        },
        "acute_selection": {
            "reference": "sh000682_close",
            "horizon_sessions": 22,
            "end_tie_break": "earliest",
            "inclusive_close_count": 23,
        },
        "comparison_contract": {
            **COMPARISON_CONTRACT,
            "dominance_metrics": list(METRICS),
        },
        "provenance": {
            "uquant_commit": commit,
            "uquant_python_sha256": _python_source_hash(repository_root / "uquant"),
            "runner_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
            "window_definition_sha256": hashlib.sha256(
                definition_path.read_bytes()
            ).hexdigest(),
            "competitor_artifact_sha256": hashlib.sha256(competitor_bytes).hexdigest(),
            "competitor_adapter_sha256": competitor_payload["adapter_sha256"],
            "competitor_repositories": competitor_payload["repositories"],
            "competitor_source_hashes": competitor_payload["source_hashes"],
            "data": competitor_payload["data_provenance"],
        },
        "rows": rows,
        "evidence": evidence,
        "evaluation": report,
    }


def main(argv: Sequence[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competitor-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    payload = build(
        repository_root=repository_root,
        competitor_path=args.competitor_results,
        workers=args.workers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["evaluation"], ensure_ascii=False, indent=2))
    return 0 if payload["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
