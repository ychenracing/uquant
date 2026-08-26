#!/usr/bin/env python3
"""Build the status-preserving four-current-HEAD comparison matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise
from multiprocessing import get_context
from pathlib import Path
from statistics import stdev
from typing import Any, Literal

import numpy as np
import pandas as pd

from research.current_heads import (
    MATRIX_STATUSES,
    REQUIRED_METRICS,
    canonical_sha256,
    load_comparison_contract,
    load_source_registry,
)
from uquant.atomic_io import atomic_write_text

_MARKET_COLUMNS = ("date", "open", "high", "low", "close", "volume", "amount")
_SHA40 = 40
_SHA256 = 64


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """One exact system/axis/window/symbol replay request."""

    system: str
    axis: str
    name: str
    family: str
    window: str
    start: str
    end: str
    acute_start: str
    acute_end: str
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.axis not in {"official_pool", "generalization"}:
            raise ValueError("replay request axis is invalid")
        if not self.system or not self.name or not self.window or not self.family:
            raise ValueError("replay request identity is incomplete")
        if not self.symbols or len(self.symbols) != len(set(self.symbols)):
            raise ValueError("replay request symbols are empty or duplicated")
        bounds = tuple(date.fromisoformat(item) for item in (self.start, self.end))
        acute = tuple(date.fromisoformat(item) for item in (self.acute_start, self.acute_end))
        if bounds[0] > acute[0] or acute[0] > acute[1] or acute[1] > bounds[1]:
            raise ValueError("replay request acute interval is outside its window")

    @property
    def cell_id(self) -> str:
        """Return the stable matrix identity."""

        return f"{self.axis}/{self.system}/{self.window}/{self.name}"


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def stage_bounded_market_data(source: Path, target: Path, *, through: str) -> dict[str, Any]:
    """Copy strict canonical CSV rows through an inclusive causal cutoff."""

    cutoff = date.fromisoformat(through)
    source_resolved = source.resolve()
    target_resolved = target.resolve(strict=False)
    if source_resolved == target_resolved or source_resolved in target_resolved.parents:
        raise ValueError("bounded market-data output overlaps its source")
    if target.exists() and (target.is_symlink() or any(target.iterdir())):
        raise ValueError("bounded market-data output must be a new empty directory")
    target.mkdir(parents=True, exist_ok=True)
    paths = sorted(source.glob("*.csv"), key=lambda item: item.name)
    if not paths:
        raise ValueError("bounded market-data source contains no CSV files")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"market-data input must be a regular file: {path.name}")
        with path.open("r", encoding="utf-8", newline="") as reader:
            csv_reader = csv.DictReader(reader)
            if csv_reader.fieldnames is None or not set(_MARKET_COLUMNS) <= set(
                csv_reader.fieldnames
            ):
                raise ValueError(f"market-data CSV lacks required columns: {path.name}")
            rows: list[dict[str, str]] = []
            previous: date | None = None
            for row in csv_reader:
                current = date.fromisoformat(str(row["date"]))
                if previous is not None and current <= previous:
                    raise ValueError(f"market-data dates are not strictly increasing: {path.name}")
                previous = current
                for field in _MARKET_COLUMNS[1:6]:
                    value = float(row[field])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError(f"market-data value is invalid: {path.name}/{field}")
                if row["amount"] not in {None, ""}:
                    amount = float(row["amount"])
                    if not math.isfinite(amount) or amount < 0:
                        raise ValueError(f"market-data value is invalid: {path.name}/amount")
                if current <= cutoff:
                    rows.append({field: str(row[field]) for field in csv_reader.fieldnames})
        destination = target / path.name
        with destination.open("w", encoding="utf-8", newline="") as writer:
            csv_writer = csv.DictWriter(writer, fieldnames=csv_reader.fieldnames)
            csv_writer.writeheader()
            csv_writer.writerows(rows)
    digest = hashlib.sha256()
    for path in sorted(target.glob("*.csv"), key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return {"through": through, "files": len(paths), "sha256": digest.hexdigest()}


def visible_symbols(data_dir: Path, symbols: Sequence[str], *, as_of: str) -> tuple[str, ...]:
    """Return only symbols with at least one observable row by ``as_of``."""

    cutoff = date.fromisoformat(as_of)
    visible: list[str] = []
    for symbol in symbols:
        path = data_dir / f"{symbol}.csv"
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", newline="") as reader:
            rows = csv.DictReader(reader)
            first = next(rows, None)
        if first is not None and date.fromisoformat(str(first["date"])) <= cutoff:
            visible.append(symbol)
    return tuple(visible)


def observable_symbols_in_window(
    data_dir: Path,
    symbols: Sequence[str],
    *,
    start: str,
    end: str,
) -> tuple[str, ...]:
    """Apply one shared at-start membership and observable-window contract."""

    visible: list[str] = []
    for symbol in symbols:
        path = data_dir / f"{symbol}.csv"
        if not path.is_file():
            continue
        first = ""
        observed = False
        with path.open("r", encoding="utf-8", newline="") as reader:
            for row in csv.DictReader(reader):
                current = str(row["date"])
                if not first:
                    first = current
                if start <= current <= end:
                    observed = True
                    break
        if first and first <= start and observed:
            visible.append(symbol)
    return tuple(visible)


def _resolve_data_symbol(data_dir: Path, symbol: str) -> str:
    direct = data_dir / f"{symbol}.csv"
    if direct.is_file():
        return symbol
    if len(symbol) == 6 and symbol.isdigit():
        matches = [prefix + symbol for prefix in ("sh", "sz") if (data_dir / f"{prefix}{symbol}.csv").is_file()]
        if len(matches) == 1:
            return matches[0]
    raise ValueError(f"final mark data is missing: {symbol}")


def _close_at(data_dir: Path, symbol: str, target: str) -> float:
    canonical = _resolve_data_symbol(data_dir, symbol)
    path = data_dir / f"{canonical}.csv"
    observed: float | None = None
    with path.open("r", encoding="utf-8", newline="") as reader:
        for row in csv.DictReader(reader):
            if str(row["date"]) > target:
                break
            observed = float(row["close"])
    if observed is None or not math.isfinite(observed) or observed <= 0:
        raise ValueError(f"final mark is invalid: {symbol}")
    return observed


def _concentration_from_fills(
    fills: Sequence[Any], *, data_dir: Path, end: str
) -> dict[str, float]:
    pnl: dict[str, float] = {}
    shares: dict[str, int] = {}
    for raw in fills:
        if not isinstance(raw, dict):
            raise ValueError("replay fill is malformed")
        symbol = str(raw.get("symbol", ""))
        side = str(raw.get("side", "")).upper()
        price = _finite(raw.get("price"), label="fill price")
        raw_shares = raw.get("shares")
        if (
            not symbol
            or side not in {"BUY", "SELL"}
            or isinstance(raw_shares, bool)
            or not isinstance(raw_shares, int)
            or raw_shares <= 0
            or price <= 0
        ):
            raise ValueError("replay fill is malformed")
        symbol = _resolve_data_symbol(data_dir, symbol)
        fill_date = str(raw.get("fill_date", raw.get("date", "")))
        if not fill_date or fill_date > end:
            raise ValueError("replay fill is outside its requested window")
        gross = price * raw_shares
        commission = max(gross * 0.00025, 5.0)
        transfer = gross * 0.00001
        stamp = gross * 0.0005 if side == "SELL" else 0.0
        cash_flow = -(gross + commission + transfer) if side == "BUY" else gross - commission - transfer - stamp
        pnl[symbol] = pnl.get(symbol, 0.0) + cash_flow
        shares[symbol] = shares.get(symbol, 0) + (raw_shares if side == "BUY" else -raw_shares)
        if shares[symbol] < 0:
            raise ValueError("replay fills sell more shares than bought")
    for symbol, amount in shares.items():
        pnl[symbol] = pnl.get(symbol, 0.0) + amount * _close_at(data_dir, symbol, end)
    absolute = sorted((abs(value) for value in pnl.values() if value != 0.0), reverse=True)
    mass = sum(absolute)
    if mass == 0.0:
        return {"top1_concentration": 0.0, "top3_concentration": 0.0, "pnl_hhi": 0.0}
    weights = [value / mass for value in absolute]
    return {
        "top1_concentration": weights[0],
        "top3_concentration": sum(weights[:3]),
        "pnl_hhi": sum(weight * weight for weight in weights),
    }


def normalize_replay_row(
    request: ReplayRequest,
    raw: Any,
    *,
    data_dir: Path,
    execution_symbols: Sequence[str] | None = None,
) -> dict[str, float | int]:
    """Normalize one production replay without hiding malformed evidence."""

    if not isinstance(raw, dict) or not raw:
        raise ValueError("worker returned an empty replay result")
    if raw.get("system") != request.system:
        raise ValueError("worker system identity mismatch")
    if (raw.get("start"), raw.get("end")) != (request.start, request.end):
        raise ValueError("worker window identity mismatch")
    expected_symbols = tuple(request.symbols if execution_symbols is None else execution_symbols)
    if raw.get("requested_symbols") != list(expected_symbols):
        raise ValueError("worker requested symbol contract mismatch")
    effective = raw.get("effective_symbols")
    if not isinstance(effective, list) or not set(effective) <= set(expected_symbols):
        raise ValueError("worker effective symbol contract mismatch")
    curve = raw.get("equity_curve")
    if not isinstance(curve, list) or not curve:
        raise ValueError("worker equity curve is empty")
    points: dict[str, float] = {}
    ordered_dates: list[str] = []
    for item in curve:
        if not isinstance(item, dict):
            raise ValueError("worker equity curve is malformed")
        point_date = str(item.get("date", ""))
        if point_date in points:
            raise ValueError("worker equity curve has duplicate dates")
        if not request.start <= point_date <= request.end:
            raise ValueError("worker equity curve is outside its requested window")
        points[point_date] = _finite(item.get("equity"), label="equity")
        if points[point_date] <= 0:
            raise ValueError("worker equity curve must be positive")
        ordered_dates.append(point_date)
    if ordered_dates != sorted(ordered_dates):
        raise ValueError("worker equity curve is not ordered")
    if request.acute_start not in points or request.acute_end not in points:
        raise ValueError("worker equity curve omits the acute interval")
    wealth = _finite(raw.get("final_wealth"), label="final_wealth")
    drawdown = _finite(raw.get("max_drawdown"), label="max_drawdown")
    turnover = _finite(raw.get("turnover"), label="gross_turnover")
    orders = raw.get("account_orders")
    if (
        wealth <= 0
        or not 0 <= drawdown <= 1
        or turnover < 0
        or isinstance(orders, bool)
        or not isinstance(orders, int)
        or orders < 0
    ):
        raise ValueError("worker replay metrics are outside valid ranges")
    daily_returns = [
        points[current] / points[previous] - 1.0
        for previous, current in pairwise(ordered_dates)
    ]
    mean_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
    volatility = stdev(daily_returns) if len(daily_returns) >= 2 else 0.0
    sharpe = mean_return / volatility * math.sqrt(252.0) if volatility > 0 else 0.0
    years = max((date.fromisoformat(request.end) - date.fromisoformat(request.start)).days / 365.2425, 1 / 365.2425)
    cagr = wealth ** (1.0 / years) - 1.0
    calmar = cagr / max(drawdown, 1e-12)
    fills = raw.get("fills")
    if not isinstance(fills, list):
        raise ValueError("worker replay fills are malformed")
    concentration = _concentration_from_fills(fills, data_dir=data_dir, end=request.end)
    result: dict[str, float | int] = {
        "final_wealth": wealth,
        "total_return": wealth - 1.0,
        "cagr": cagr,
        "sharpe": sharpe,
        "calmar": calmar,
        "max_drawdown": drawdown,
        "account_orders": orders,
        "gross_turnover": turnover,
        "annual_turnover": turnover / years,
        "acute_return": points[request.acute_end] / points[request.acute_start] - 1.0,
        **concentration,
    }
    if tuple(result) != REQUIRED_METRICS or any(
        isinstance(value, bool) or not math.isfinite(float(value)) for value in result.values()
    ):
        raise ValueError("normalized replay metrics are incomplete or non-finite")
    return result


def build_matrix_cell(
    request: ReplayRequest,
    *,
    status: str,
    metrics: dict[str, float | int] | None,
    error: dict[str, str] | None,
    provenance: dict[str, str],
    effective_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build one mutually exclusive SUCCESS/error/sample matrix record."""

    if status not in MATRIX_STATUSES:
        raise ValueError("matrix status is unknown")
    if status == "SUCCESS":
        if (
            metrics is None
            or error is not None
            or set(metrics) != set(REQUIRED_METRICS)
        ):
            raise ValueError("SUCCESS requires metrics and no error")
    elif metrics is not None or not isinstance(error, dict) or set(error) != {"class", "message"}:
        raise ValueError(f"{status} requires an explicit error and no metrics")
    if set(provenance) != {
        "system_commit",
        "data_sha256",
        "config_sha256",
        "runtime_sha256",
        "evidence_sha256",
    }:
        raise ValueError("matrix cell provenance is incomplete")
    if len(provenance["system_commit"]) != _SHA40 or any(
        len(provenance[field]) != _SHA256
        for field in ("data_sha256", "config_sha256", "runtime_sha256", "evidence_sha256")
    ):
        raise ValueError("matrix cell provenance hashes are malformed")
    return {
        "cell_id": request.cell_id,
        "axis": request.axis,
        "system": request.system,
        "window": request.window,
        "start": request.start,
        "end": request.end,
        "name": request.name,
        "family": request.family,
        "symbols": list(request.symbols),
        "effective_symbols": list(
            request.symbols if effective_symbols is None else effective_symbols
        ),
        "status": status,
        "metrics": metrics,
        "error": error,
        "provenance": provenance,
    }


def _request_from_payload(value: Any) -> ReplayRequest:
    if not isinstance(value, dict):
        raise ValueError("replay request payload must be an object")
    expected = {
        "system",
        "axis",
        "name",
        "family",
        "window",
        "start",
        "end",
        "acute_start",
        "acute_end",
        "symbols",
    }
    if (
        set(value) != expected
        or isinstance(value["symbols"], (str, bytes))
        or not isinstance(value["symbols"], Sequence)
    ):
        raise ValueError("replay request payload fields are malformed")
    return ReplayRequest(
        system=str(value["system"]),
        axis=str(value["axis"]),
        name=str(value["name"]),
        family=str(value["family"]),
        window=str(value["window"]),
        start=str(value["start"]),
        end=str(value["end"]),
        acute_start=str(value["acute_start"]),
        acute_end=str(value["acute_end"]),
        symbols=tuple(str(item) for item in value["symbols"]),
    )


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _scenario_requests(
    contract: dict[str, Any], generalization_baseline: dict[str, Any], *, system: str
) -> tuple[list[ReplayRequest], list[ReplayRequest]]:
    windows = contract["windows"]
    ready: list[ReplayRequest] = []
    insufficient: list[ReplayRequest] = []
    cells = generalization_baseline.get("cells")
    if not isinstance(cells, list) or len(cells) != 234:
        raise ValueError("uquant generalization baseline must contain 234 scenario records")
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("uquant generalization baseline contains a malformed cell")
        window = str(cell.get("window"))
        if window not in windows:
            raise ValueError("uquant generalization baseline contains an unknown window")
        bounds = windows[window]
        request = ReplayRequest(
            system=system,
            axis="generalization",
            name=str(cell.get("scenario")),
            family=str(cell.get("family")),
            window=window,
            start=str(cell.get("start")),
            end=str(cell.get("end")),
            acute_start=bounds["acute_start"],
            acute_end=bounds["acute_end"],
            symbols=tuple(str(item) for item in cell.get("symbols", [])),
        )
        if bool(cell.get("economic")) and cell.get("status") == "READY":
            ready.append(request)
        elif not bool(cell.get("economic")) and cell.get("status") == "INSUFFICIENT_SAMPLE":
            insufficient.append(request)
        else:
            raise ValueError("uquant generalization scenario status is inconsistent")
    if len(ready) != 192 or len(insufficient) != 42:
        raise ValueError("uquant generalization scenario dimensions changed")
    return ready, insufficient


def build_replay_requests(
    contract: dict[str, Any], generalization_baseline: dict[str, Any], *, system: str
) -> tuple[list[ReplayRequest], list[ReplayRequest]]:
    """Build all 30 official and 234 generalization rows for one system."""

    ready: list[ReplayRequest] = []
    for pool, symbols in contract["official_pools"].items():
        for window, bounds in contract["windows"].items():
            ready.append(
                ReplayRequest(
                    system=system,
                    axis="official_pool",
                    name=pool,
                    family="official_pool",
                    window=window,
                    start=bounds["start"],
                    end=bounds["end"],
                    acute_start=bounds["acute_start"],
                    acute_end=bounds["acute_end"],
                    symbols=tuple(symbols),
                )
            )
    scenario_ready, insufficient = _scenario_requests(contract, generalization_baseline, system=system)
    ready.extend(scenario_ready)
    if len(ready) != 222 or len(insufficient) != 42:
        raise RuntimeError("current-head per-system request dimensions changed")
    return ready, insufficient


def _stage_trade_view(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    staged = 0
    for path in sorted(source.glob("*.csv"), key=lambda item: item.name):
        if len(path.stem) != 8 or path.stem[:2] not in {"sh", "sz"}:
            continue
        (target / f"{path.stem[2:]}.csv").symlink_to(path.resolve())
        staged += 1
    if staged == 0:
        raise ValueError("cannot stage the trade six-digit market-data view")


def prepare_runtime(
    *,
    contract_path: Path,
    registry_path: Path,
    generalization_baseline_path: Path,
    data_dir: Path,
    runtime_dir: Path,
) -> dict[str, Any]:
    """Stage six bounded data snapshots and immutable per-system request lists."""

    contract = load_comparison_contract(contract_path)
    registry = load_source_registry(registry_path, adapter_path=Path(__file__).resolve())
    generalization_baseline = json.loads(generalization_baseline_path.read_text(encoding="utf-8"))
    if runtime_dir.exists() and any(runtime_dir.iterdir()):
        raise ValueError("current-head runtime directory must be new or empty")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    data_root = runtime_dir / "data"
    trade_root = runtime_dir / "trade-data"
    data_root.mkdir()
    trade_root.mkdir()
    data: dict[str, Any] = {}
    for window, bounds in contract["windows"].items():
        staged = data_root / window
        data[window] = stage_bounded_market_data(data_dir, staged, through=bounds["end"])
        _stage_trade_view(staged, trade_root / window)
    requests: dict[str, Any] = {}
    insufficient: dict[str, Any] = {}
    request_root = runtime_dir / "requests"
    request_root.mkdir()
    for system in contract["systems"]:
        ready, sample = build_replay_requests(contract, generalization_baseline, system=system)
        requests[system] = [asdict(item) for item in ready]
        insufficient[system] = [asdict(item) for item in sample]
        _write_json(request_root / f"{system}.json", requests[system])
    manifest = {
        "schema_version": 1,
        "contract_sha256": contract["payload_sha256"],
        "source_registry_sha256": registry["payload_sha256"],
        "phase2_sha256": hashlib.sha256(generalization_baseline_path.read_bytes()).hexdigest(),
        "data": data,
        "ready_per_system": 222,
        "insufficient_per_system": 42,
        "insufficient": insufficient,
    }
    _write_json(runtime_dir / "manifest.json", manifest)
    return manifest


def _runtime_payload() -> dict[str, str]:
    return {
        "python_full_version": platform.python_version(),
        "python_executable": sys.executable,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    }


def _competitor_config_payload(system: str) -> dict[str, str]:
    entries = {
        "qwenquant": "BacktestEngine(preset_for_universe); close signal; next-open adapter",
        "aquant": "run_auto_daily_replay(production_policy_enabled=True)",
        "trade": "ProductionReplayEngine; automatic production route",
    }
    return {
        "adapter_contract": "current-heads-comparison-v1",
        "production_entry": entries[system],
        "system": system,
    }


def _execute_competitor_request(task: tuple[dict[str, Any], dict[str, str]]) -> dict[str, Any]:
    request = _request_from_payload(task[0])
    paths = task[1]
    data_dir = Path(paths["data_root"]) / request.window
    runtime = _runtime_payload()
    config = _competitor_config_payload(request.system)
    execution_symbols = observable_symbols_in_window(
        data_dir,
        request.symbols,
        start=request.start,
        end=request.end,
    )
    try:
        if not execution_symbols:
            raise RuntimeError("shared stock-pool contract has no observable symbol in this window")
        from research.window_competitor_adapter import Task as WindowAdapterTask
        from research.window_competitor_adapter import run_replay_task

        adapter_task = WindowAdapterTask(
            request.system,
            request.name,
            request.window,
            paths["qwen_root"],
            paths["aquant_root"],
            paths["trade_root"],
            str(data_dir),
            str(Path(paths["trade_data_root"]) / request.window),
        )
        raw = run_replay_task(
            adapter_task,
            pools={request.name: execution_symbols},
            windows={request.window: (request.start, request.end)},
            repository_root=Path(paths["repository_root"]),
        )
        metrics = normalize_replay_row(
            request,
            raw,
            data_dir=data_dir,
            execution_symbols=execution_symbols,
        )
        return {
            "request": asdict(request),
            "status": "SUCCESS",
            "metrics": metrics,
            "error": None,
            "runtime": runtime,
            "runtime_sha256": canonical_sha256(runtime),
            "config_sha256": canonical_sha256(config),
            "evidence_sha256": canonical_sha256(raw),
            "effective_symbols": list(execution_symbols),
        }
    except Exception as exc:
        error = {"class": type(exc).__name__, "message": str(exc)}
        evidence = {"request": asdict(request), "error": error}
        return {
            "request": asdict(request),
            "status": "REPLAY_ERROR",
            "metrics": None,
            "error": error,
            "runtime": runtime,
            "runtime_sha256": canonical_sha256(runtime),
            "config_sha256": canonical_sha256(config),
            "evidence_sha256": canonical_sha256(evidence),
            "effective_symbols": list(execution_symbols),
        }


def competitor_executor_policy(
    system: str,
) -> tuple[Literal["fork", "spawn"], int | None]:
    """Return the process lifecycle required by each read-only competitor.

    Aquant binds ``AQUANT_DATA_DIR`` and static calendars when its modules are
    imported.  Reusing a process across window-bounded cells therefore leaks
    the first cell's data root into later cells.  A fresh spawned process per
    cell preserves the source unchanged while making every replay independent.
    """

    if system == "aquant":
        return "spawn", 1
    if system in {"qwenquant", "trade"}:
        return "fork", None
    raise ValueError("unknown competitor executor policy")


def run_competitor_batch(
    *,
    system: str,
    request_path: Path,
    output_path: Path,
    repository_root: Path,
    runtime_dir: Path,
    qwen_root: Path,
    aquant_root: Path,
    trade_root: Path,
    workers: int,
) -> dict[str, Any]:
    """Run one competitor in its own interpreter and preserve every error row."""

    if system not in {"aquant", "qwenquant", "trade"} or workers < 1:
        raise ValueError("competitor batch system or worker count is invalid")
    raw_requests = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(raw_requests, list) or len(raw_requests) != 222:
        raise ValueError("competitor batch requires exactly 222 ready requests")
    requests = [_request_from_payload(item) for item in raw_requests]
    if any(item.system != system for item in requests):
        raise ValueError("competitor batch request system mismatch")
    paths = {
        "repository_root": str(repository_root),
        "data_root": str(runtime_dir / "data"),
        "trade_data_root": str(runtime_dir / "trade-data"),
        "qwen_root": str(qwen_root),
        "aquant_root": str(aquant_root),
        "trade_root": str(trade_root),
    }
    tasks = [(asdict(item), paths) for item in requests]
    rows: list[dict[str, Any]] = []
    start_method, max_tasks_per_child = competitor_executor_policy(system)
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context(start_method),
        max_tasks_per_child=max_tasks_per_child,
    ) as executor:
        futures = {executor.submit(_execute_competitor_request, task): task[0] for task in tasks}
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            latest = futures[future]
            print(
                f"current-head {system}: {index}/222 "
                f"latest={latest['axis']}/{latest['window']}/{latest['name']}",
                flush=True,
            )
    order = {item.cell_id: index for index, item in enumerate(requests)}
    rows.sort(key=lambda item: order[_request_from_payload(item["request"]).cell_id])
    payload = {
        "schema_version": 1,
        "system": system,
        "runtime": _runtime_payload(),
        "rows": rows,
        "summary": {
            "cells": len(rows),
            "success": sum(item["status"] == "SUCCESS" for item in rows),
            "replay_error": sum(item["status"] == "REPLAY_ERROR" for item in rows),
        },
    }
    _write_json(output_path, payload)
    return payload


def _curve_acute_return(
    curve: Any, *, start: str, end: str, window_start: str, window_end: str
) -> float:
    if not isinstance(curve, list) or not curve:
        raise ValueError("uquant equity curve is empty")
    points: dict[str, float] = {}
    previous = ""
    for item in curve:
        if not isinstance(item, dict):
            raise ValueError("uquant equity curve is malformed")
        point_date = str(item.get("date", ""))
        if point_date in points or point_date <= previous:
            raise ValueError("uquant equity curve dates are not canonical")
        if not window_start <= point_date <= window_end:
            raise ValueError("uquant equity curve is outside its requested window")
        points[point_date] = _finite(item.get("equity"), label="uquant equity")
        previous = point_date
    if start not in points or end not in points:
        raise ValueError("uquant equity curve omits the acute interval")
    return points[end] / points[start] - 1.0


def _symbol_concentration(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("uquant symbol PnL is malformed")
    absolute = sorted(
        (
            abs(_finite(item, label=f"symbol PnL {symbol}"))
            for symbol, item in value.items()
            if float(item) != 0.0
        ),
        reverse=True,
    )
    mass = sum(absolute)
    if mass == 0.0:
        return {"top1_concentration": 0.0, "top3_concentration": 0.0, "pnl_hhi": 0.0}
    weights = [item / mass for item in absolute]
    return {
        "top1_concentration": weights[0],
        "top3_concentration": sum(weights[:3]),
        "pnl_hhi": sum(item * item for item in weights),
    }


def _normalize_uquant_raw(request: ReplayRequest, raw: Any) -> dict[str, float | int]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("uquant replay result is empty")
    orders = raw.get("account_orders")
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise ValueError("uquant account order count is malformed")
    metrics: dict[str, float | int] = {
        "final_wealth": _finite(raw.get("final_wealth"), label="uquant final_wealth"),
        "total_return": _finite(raw.get("total_return"), label="uquant total_return"),
        "cagr": _finite(raw.get("cagr"), label="uquant cagr"),
        "sharpe": _finite(raw.get("sharpe"), label="uquant sharpe"),
        "calmar": _finite(raw.get("calmar"), label="uquant calmar"),
        "max_drawdown": _finite(raw.get("max_drawdown"), label="uquant max_drawdown"),
        "account_orders": orders,
        "gross_turnover": _finite(raw.get("gross_turnover"), label="uquant gross_turnover"),
        "annual_turnover": _finite(
            raw.get("annual_turnover"), label="uquant annual_turnover"
        ),
        "acute_return": _curve_acute_return(
            raw.get("equity_curve"),
            start=request.acute_start,
            end=request.acute_end,
            window_start=request.start,
            window_end=request.end,
        ),
        **_symbol_concentration(raw.get("symbol_pnl")),
    }
    if tuple(metrics) != REQUIRED_METRICS:
        raise RuntimeError("uquant normalized metric order changed")
    return metrics


def _execute_uquant_official(task: tuple[dict[str, Any], str]) -> dict[str, Any]:
    request = _request_from_payload(task[0])
    data_dir = Path(task[1]) / request.window
    runtime = _runtime_payload()
    execution_symbols = observable_symbols_in_window(
        data_dir,
        request.symbols,
        start=request.start,
        end=request.end,
    )
    try:
        from uquant.engine import ProductionEngine
        from uquant.validation.generalization import symbol_pnl_from_result

        if not execution_symbols:
            raise RuntimeError("shared stock-pool contract has no observable symbol in this window")
        raw = ProductionEngine(data_dir).backtest(
            symbols=execution_symbols,
            start=request.start,
            end=request.end,
        )
        if not isinstance(raw.get("symbol_pnl"), dict):
            raw["symbol_pnl"] = symbol_pnl_from_result(
                raw,
                {
                    symbol: _close_at(data_dir, symbol, request.end)
                    for symbol in execution_symbols
                },
            )
        metrics = _normalize_uquant_raw(request, raw)
        config_sha256 = str(raw.get("effective_config_sha256", ""))
        if len(config_sha256) != 64:
            raise ValueError("uquant effective configuration identity is missing")
        return {
            "request": asdict(request),
            "status": "SUCCESS",
            "metrics": metrics,
            "error": None,
            "runtime": runtime,
            "runtime_sha256": canonical_sha256(runtime),
            "config_sha256": config_sha256,
            "evidence_sha256": canonical_sha256(raw),
            "effective_symbols": list(execution_symbols),
        }
    except Exception as exc:
        error = {"class": type(exc).__name__, "message": str(exc)}
        return {
            "request": asdict(request),
            "status": "REPLAY_ERROR",
            "metrics": None,
            "error": error,
            "runtime": runtime,
            "runtime_sha256": canonical_sha256(runtime),
            "config_sha256": canonical_sha256(
                {"system": "uquant", "production_entry": "ProductionEngine.backtest"}
            ),
            "evidence_sha256": canonical_sha256({"request": asdict(request), "error": error}),
            "effective_symbols": list(execution_symbols),
        }


def run_uquant_official_batch(
    requests: Sequence[ReplayRequest], *, data_root: Path, workers: int
) -> list[dict[str, Any]]:
    official = [item for item in requests if item.axis == "official_pool"]
    if len(official) != 30 or workers < 1:
        raise ValueError("uquant official batch dimensions are invalid")
    tasks = [(asdict(item), str(data_root)) for item in official]
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("fork")) as executor:
        futures = {executor.submit(_execute_uquant_official, task): task[0] for task in tasks}
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            latest = futures[future]
            print(
                f"current-head uquant: {index}/30 latest={latest['window']}/{latest['name']}",
                flush=True,
            )
    order = {item.cell_id: index for index, item in enumerate(official)}
    rows.sort(key=lambda item: order[_request_from_payload(item["request"]).cell_id])
    return rows


def _uquant_generalization_rows(
    requests: Sequence[ReplayRequest], generalization_matrix: dict[str, Any], *, data_root: Path
) -> list[dict[str, Any]]:
    by_id = {
        f"generalization/uquant/{cell['window']}/{cell['scenario']}": cell
        for cell in generalization_matrix.get("cells", [])
        if isinstance(cell, dict) and bool(cell.get("economic"))
    }
    generalization = [item for item in requests if item.axis == "generalization"]
    if len(generalization) != 192 or len(by_id) != 192:
        raise ValueError("raw uquant generalization economic matrix must contain 192 cells")
    runtime_raw = generalization_matrix.get("provenance", {}).get("runtime")
    if not isinstance(runtime_raw, dict):
        raise ValueError("raw uquant generalization runtime provenance is missing")
    rows: list[dict[str, Any]] = []
    for request in generalization:
        cell = by_id.get(request.cell_id)
        if cell is None or not isinstance(cell.get("raw"), dict):
            raise ValueError(f"raw uquant generalization cell is missing: {request.cell_id}")
        raw = cell["raw"]
        effective_symbols = observable_symbols_in_window(
            data_root / request.window,
            request.symbols,
            start=request.start,
            end=request.end,
        )
        rows.append(
            {
                "request": asdict(request),
                "status": "SUCCESS",
                "metrics": _normalize_uquant_raw(request, raw),
                "error": None,
                "runtime": runtime_raw,
                "runtime_sha256": canonical_sha256(runtime_raw),
                "config_sha256": str(raw["effective_config_sha256"]),
                "evidence_sha256": canonical_sha256(cell),
                "effective_symbols": list(effective_symbols),
            }
        )
    return rows


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("matrix aggregate cannot use an empty metric set")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _matrix_aggregates(cells: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for system in ("uquant", "aquant", "qwenquant", "trade"):
        result[system] = {}
        for axis in ("official_pool", "generalization"):
            group = [
                item for item in cells if item["system"] == system and item["axis"] == axis
            ]
            success = [item for item in group if item["status"] == "SUCCESS"]
            metrics: dict[str, Any] = {}
            for field in REQUIRED_METRICS:
                values = [float(item["metrics"][field]) for item in success]
                if values:
                    metrics[field] = {
                        "min": min(values),
                        "p10": _quantile(values, 0.10),
                        "median": _quantile(values, 0.50),
                        "p90": _quantile(values, 0.90),
                        "max": max(values),
                    }
            result[system][axis] = {
                "cells": len(group),
                "success": len(success),
                "replay_error": sum(item["status"] == "REPLAY_ERROR" for item in group),
                "insufficient_sample": sum(
                    item["status"] == "INSUFFICIENT_SAMPLE" for item in group
                ),
                "metrics": metrics,
            }
    return result


def assemble_matrix(
    *,
    contract_path: Path,
    registry_path: Path,
    generalization_summary_path: Path,
    generalization_matrix_path: Path,
    runtime_dir: Path,
    output_path: Path,
    uquant_workers: int,
) -> dict[str, Any]:
    """Assemble all 1,056 rows and fail if any preregistered identity is absent."""

    contract = load_comparison_contract(contract_path)
    registry = load_source_registry(registry_path, adapter_path=Path(__file__).resolve())
    compact = json.loads(generalization_summary_path.read_text(encoding="utf-8"))
    generalization_matrix = json.loads(generalization_matrix_path.read_text(encoding="utf-8"))
    runtime_manifest = json.loads((runtime_dir / "manifest.json").read_text(encoding="utf-8"))
    ready_by_system: dict[str, list[ReplayRequest]] = {}
    insufficient_by_system: dict[str, list[ReplayRequest]] = {}
    for system in contract["systems"]:
        ready, insufficient = build_replay_requests(contract, compact, system=system)
        ready_by_system[system] = ready
        insufficient_by_system[system] = insufficient

    raw_rows: dict[str, list[dict[str, Any]]] = {
        "uquant": [
            *run_uquant_official_batch(
                ready_by_system["uquant"],
                data_root=runtime_dir / "data",
                workers=uquant_workers,
            ),
            *_uquant_generalization_rows(
                ready_by_system["uquant"],
                generalization_matrix,
                data_root=runtime_dir / "data",
            ),
        ]
    }
    generalization_runtime = generalization_matrix.get("provenance", {}).get("runtime")
    if not isinstance(generalization_runtime, dict):
        raise ValueError("raw generalization runtime provenance is missing")
    runtimes: dict[str, Any] = {
        "uquant": {
            "official": raw_rows["uquant"][0]["runtime"],
            "generalization": generalization_runtime,
        },
    }
    for system in ("aquant", "qwenquant", "trade"):
        payload = json.loads((runtime_dir / f"{system}.json").read_text(encoding="utf-8"))
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) != 222:
            raise ValueError(f"{system} worker output is incomplete")
        raw_rows[system] = rows
        runtimes[system] = payload["runtime"]

    cells: list[dict[str, Any]] = []
    expected_ids: set[str] = set()
    data_snapshot = generalization_matrix.get("provenance", {}).get("data")
    if not isinstance(data_snapshot, dict):
        raise ValueError("raw generalization data provenance is missing")
    for system in contract["systems"]:
        source = registry["repositories"][system]
        rows_by_id = {
            _request_from_payload(item["request"]).cell_id: item for item in raw_rows[system]
        }
        for request in ready_by_system[system]:
            expected_ids.add(request.cell_id)
            row = rows_by_id.get(request.cell_id)
            if row is None:
                raise ValueError(f"worker omitted preregistered cell: {request.cell_id}")
            bounded = runtime_manifest["data"][request.window]["sha256"]
            data_sha256 = canonical_sha256(
                {"snapshot": data_snapshot, "window": request.window, "bounded_sha256": bounded}
            )
            cells.append(
                build_matrix_cell(
                    request,
                    status=row["status"],
                    metrics=row["metrics"],
                    error=row["error"],
                    provenance={
                        "system_commit": source["commit"],
                        "data_sha256": data_sha256,
                        "config_sha256": row["config_sha256"],
                        "runtime_sha256": row["runtime_sha256"],
                        "evidence_sha256": row["evidence_sha256"],
                    },
                    effective_symbols=row.get("effective_symbols"),
                )
            )
        for request in insufficient_by_system[system]:
            expected_ids.add(request.cell_id)
            bounded = runtime_manifest["data"][request.window]["sha256"]
            data_sha256 = canonical_sha256(
                {"snapshot": data_snapshot, "window": request.window, "bounded_sha256": bounded}
            )
            error = {
                "class": "InsufficientSample",
                "message": f"pre-registered scenario has fewer than 2 members: {request.name}",
            }
            config_sha256 = (
                str(generalization_matrix["provenance"]["effective_config_sha256"])
                if system == "uquant"
                else canonical_sha256(_competitor_config_payload(system))
            )
            cells.append(
                build_matrix_cell(
                    request,
                    status="INSUFFICIENT_SAMPLE",
                    metrics=None,
                    error=error,
                    provenance={
                        "system_commit": source["commit"],
                        "data_sha256": data_sha256,
                        "config_sha256": config_sha256,
                        "runtime_sha256": canonical_sha256(
                            runtimes[system]["generalization"]
                            if system == "uquant"
                            else runtimes[system]
                        ),
                        "evidence_sha256": canonical_sha256(
                            {"request": asdict(request), "error": error}
                        ),
                    },
                    effective_symbols=observable_symbols_in_window(
                        runtime_dir / "data" / request.window,
                        request.symbols,
                        start=request.start,
                        end=request.end,
                    ),
                )
            )
    if len(cells) != 1056 or len(expected_ids) != 1056:
        raise ValueError("current-head matrix does not contain 1,056 preregistered cells")
    observed_ids = [item["cell_id"] for item in cells]
    if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_ids:
        raise ValueError("current-head matrix cell identities are duplicated or incomplete")
    system_order = {name: index for index, name in enumerate(contract["systems"])}
    window_order = {name: index for index, name in enumerate(contract["windows"])}
    cells.sort(
        key=lambda item: (
            system_order[item["system"]],
            0 if item["axis"] == "official_pool" else 1,
            window_order[item["window"]],
            item["name"],
        )
    )
    summary = {
        "cells": len(cells),
        "success": sum(item["status"] == "SUCCESS" for item in cells),
        "replay_error": sum(item["status"] == "REPLAY_ERROR" for item in cells),
        "insufficient_sample": sum(
            item["status"] == "INSUFFICIENT_SAMPLE" for item in cells
        ),
        "official_pool_cells": sum(item["axis"] == "official_pool" for item in cells),
        "generalization_cells": sum(item["axis"] == "generalization" for item in cells),
    }
    payload = {
        "schema_version": 1,
        "contract_sha256": contract["payload_sha256"],
        "source_registry_sha256": registry["payload_sha256"],
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "data": {
            "snapshot": data_snapshot,
            "bounded_windows": runtime_manifest["data"],
        },
        "runtimes": runtimes,
        "summary": summary,
        "aggregates": _matrix_aggregates(cells),
        "legacy_source_diagnostic": {
            "evidence_class": "diagnostic_only_not_current_HEAD",
            "aquant": "3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
            "qwenquant": "0b3681e10b75425ad8600e75835677a6a125ed13",
            "trade": "cee1620f40af3af8f839e15db188a9e388a78dd0",
        },
        "cells": cells,
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    _write_json(output_path, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Prepare bounded inputs, run one isolated system, or validate contracts."""

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-contracts")
    prepare = subparsers.add_parser("prepare")
    worker = subparsers.add_parser("worker")
    assemble = subparsers.add_parser("assemble")
    for command in (validate, prepare, assemble):
        command.add_argument(
            "--contract",
            type=Path,
            default=root / "benchmarks/current_heads_comparison_contract.json",
        )
        command.add_argument(
            "--source-registry",
            type=Path,
            default=root / "benchmarks/current_heads_source_registry.json",
        )
    prepare.add_argument(
        "--generalization-baseline",
        type=Path,
        default=root / "artifacts/current_heads/baseline/uquant_phase2.json",
    )
    prepare.add_argument("--data-dir", type=Path, default=root / "data/frozen")
    prepare.add_argument("--runtime-dir", type=Path, required=True)
    worker.add_argument("--system", choices=("aquant", "qwenquant", "trade"), required=True)
    worker.add_argument("--requests", type=Path, required=True)
    worker.add_argument("--output", type=Path, required=True)
    worker.add_argument("--runtime-dir", type=Path, required=True)
    worker.add_argument("--workers", type=int, default=4)
    competitor_root = root.parents[0] / "competitors"
    worker.add_argument("--qwen-root", type=Path, default=competitor_root / "qwenquant")
    worker.add_argument("--aquant-root", type=Path, default=competitor_root / "aquant")
    worker.add_argument("--trade-root", type=Path, default=competitor_root / "trade")
    assemble.add_argument(
        "--generalization-summary",
        type=Path,
        default=root / "artifacts/current_heads/baseline/uquant_phase2.json",
    )
    assemble.add_argument("--generalization-matrix", type=Path, required=True)
    assemble.add_argument("--runtime-dir", type=Path, required=True)
    assemble.add_argument(
        "--output",
        type=Path,
        default=root / "benchmarks/current_heads_competitor_matrix.json",
    )
    assemble.add_argument("--uquant-workers", type=int, default=6)
    args = parser.parse_args(argv)
    if args.command == "validate-contracts":
        load_comparison_contract(args.contract)
        load_source_registry(args.source_registry, adapter_path=Path(__file__).resolve())
    elif args.command == "prepare":
        manifest = prepare_runtime(
            contract_path=args.contract,
            registry_path=args.source_registry,
            generalization_baseline_path=args.generalization_baseline,
            data_dir=args.data_dir,
            runtime_dir=args.runtime_dir,
        )
        print(json.dumps({key: manifest[key] for key in ("ready_per_system", "insufficient_per_system")}))
    elif args.command == "worker":
        run_competitor_batch(
            system=args.system,
            request_path=args.requests,
            output_path=args.output,
            repository_root=root,
            runtime_dir=args.runtime_dir,
            qwen_root=args.qwen_root,
            aquant_root=args.aquant_root,
            trade_root=args.trade_root,
            workers=args.workers,
        )
    elif args.command == "assemble":
        payload = assemble_matrix(
            contract_path=args.contract,
            registry_path=args.source_registry,
            generalization_summary_path=args.generalization_summary,
            generalization_matrix_path=args.generalization_matrix,
            runtime_dir=args.runtime_dir,
            output_path=args.output,
            uquant_workers=args.uquant_workers,
        )
        print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
