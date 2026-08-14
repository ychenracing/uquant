"""Execution, aggregation, and fail-closed validation of the AI-era matrix."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any, cast

import pandas as pd

from ..config import config_fingerprint
from ..engine import ProductionEngine
from .ai_era import runtime_environment_provenance
from .generalization import (
    GeneralizationObservation,
    aggregate_metrics,
    compute_pre_window_evidence,
    observation_from_result,
    symbol_pnl_concentration,
    symbol_pnl_from_result,
)
from .generalization_contract import (
    ContractScenario,
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from .manifest import verify_data_manifest
from .universe import AIUniverse, load_ai_universe

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_PROVENANCE_FIELDS = {
    "head",
    "source_sha256",
    "effective_config_sha256",
    "data",
    "runtime",
    "universe_sha256",
    "industry_sha256",
    "window_fingerprint",
    "scenario_fingerprint",
}
_DATA_FIELDS = {"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"}
_RUNTIME_FIELDS = {
    "python_full_version",
    "numpy_version",
    "pandas_version",
    "uv_version",
    "uv_lock_sha256",
}
_METRIC_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "annual_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
}
_CONCENTRATION_DEFINITION = {
    "basis": "absolute exact symbol PnL contribution",
    "denominator": "sum(abs(symbol_pnl))",
    "top1": "largest abs(symbol_pnl) divided by denominator",
    "top3": "sum of three largest abs(symbol_pnl) divided by denominator",
    "hhi": "sum((abs(symbol_pnl) / denominator) ** 2)",
    "zero_mass": "all concentration metrics are exactly 0.0",
}

CellEvaluator = Callable[[Mapping[str, Any], Mapping[str, Any]], Sequence[str]]


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def window_contract_fingerprint(scenarios: Sequence[ContractScenario]) -> str:
    """Hash the exact distinct official windows represented by a shard."""
    windows: list[dict[str, str]] = []
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.window.name in seen:
            continue
        seen.add(scenario.window.name)
        windows.append(
            {
                "name": scenario.window.name,
                "start": scenario.window.start,
                "end": scenario.window.end,
            }
        )
    if not windows:
        raise ValueError("matrix window fingerprint requires scenarios")
    return _hash_json(windows)


def _canonical_json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        result = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("matrix raw cell is not finite canonical JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("matrix raw cell must be an object")
    return result


def _metrics_from_raw(
    scenario: ContractScenario,
    raw: Mapping[str, Any],
) -> tuple[dict[str, float | int], GeneralizationObservation]:
    if scenario.raw_scenario is None:
        raise ValueError("cannot extract economic metrics from an insufficient sample")
    observation = observation_from_result(scenario.raw_scenario, raw)
    try:
        gross_turnover = float(raw["gross_turnover"])
        annual_turnover = float(raw["annual_turnover"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"scenario result is missing turnover: {scenario.name}") from exc
    if (
        not math.isfinite(gross_turnover)
        or not math.isfinite(annual_turnover)
        or gross_turnover < 0
        or annual_turnover < 0
    ):
        raise ValueError(f"scenario result has invalid turnover: {scenario.name}")
    return (
        {
            "final_wealth": observation.final_wealth,
            "max_drawdown": observation.max_drawdown,
            "account_orders": observation.account_orders,
            "gross_turnover": gross_turnover,
            "annual_turnover": annual_turnover,
            **symbol_pnl_concentration(observation.pnl_map()),
        },
        observation,
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("matrix aggregate requires economic cells")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _aggregate(
    metrics: Sequence[Mapping[str, float | int]],
    observations: Sequence[GeneralizationObservation],
) -> dict[str, float]:
    if not metrics or not observations or len(metrics) != len(observations):
        raise ValueError("matrix aggregate requires matching economic evidence")
    base = aggregate_metrics(observations)
    wealth = [float(item["final_wealth"]) for item in metrics]
    gross = [float(item["gross_turnover"]) for item in metrics]
    annual = [float(item["annual_turnover"]) for item in metrics]
    top1 = [float(item["top1_concentration"]) for item in metrics]
    top3 = [float(item["top3_concentration"]) for item in metrics]
    hhi = [float(item["pnl_hhi"]) for item in metrics]
    return {
        **base,
        "worst_wealth": min(wealth),
        "median_gross_turnover": float(median(gross)),
        "p90_gross_turnover": _quantile(gross, 0.90),
        "worst_gross_turnover": max(gross),
        "median_annual_turnover": float(median(annual)),
        "p90_annual_turnover": _quantile(annual, 0.90),
        "worst_annual_turnover": max(annual),
        "median_top1_concentration": float(median(top1)),
        "worst_top1_concentration": max(top1),
        "median_top3_concentration": float(median(top3)),
        "worst_top3_concentration": max(top3),
        "median_pnl_hhi": float(median(hhi)),
        "worst_pnl_hhi": max(hhi),
    }


def _scenario_cell(scenario: ContractScenario) -> dict[str, Any]:
    return {
        "window": scenario.window.name,
        "start": scenario.window.start,
        "end": scenario.window.end,
        "scenario": scenario.name,
        "family": scenario.family,
        "status": scenario.status.value,
        "economic": scenario.economic,
        "symbols": list(scenario.symbols),
        "reference_symbols": list(scenario.reference_symbols),
        "removed_symbols": list(scenario.removed_symbols),
        "industry": scenario.industry,
        "pool_size": scenario.pool_size,
        "seed_index": scenario.seed_index,
        "derived_seed": scenario.derived_seed,
    }


def _validate_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _PROVENANCE_FIELDS:
        raise ValueError("matrix provenance fields are incomplete or unexpected")
    for name in (
        "source_sha256",
        "effective_config_sha256",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
    ):
        if not isinstance(value[name], str) or not _SHA256.fullmatch(value[name]):
            raise ValueError(f"matrix provenance {name} must be SHA-256")
    if not isinstance(value["head"], str) or not _COMMIT.fullmatch(value["head"]):
        raise ValueError("matrix provenance head must be an immutable commit")
    data = value["data"]
    runtime = value["runtime"]
    if not isinstance(data, Mapping) or set(data) != _DATA_FIELDS:
        raise ValueError("matrix data provenance is malformed")
    if not isinstance(runtime, Mapping) or set(runtime) != _RUNTIME_FIELDS:
        raise ValueError("matrix runtime provenance is malformed")
    if not isinstance(data["snapshot_id"], str) or not data["snapshot_id"]:
        raise ValueError("matrix data snapshot is malformed")
    if (
        isinstance(data["files_verified"], bool)
        or not isinstance(data["files_verified"], int)
        or data["files_verified"] < 1
    ):
        raise ValueError("matrix data file count is malformed")
    for name in ("manifest_sha256", "checksums_sha256"):
        if not isinstance(data[name], str) or not _SHA256.fullmatch(data[name]):
            raise ValueError(f"matrix data {name} must be SHA-256")
    for name in _RUNTIME_FIELDS - {"uv_lock_sha256"}:
        if not isinstance(runtime[name], str) or not runtime[name]:
            raise ValueError(f"matrix runtime {name} is malformed")
    if not isinstance(runtime["uv_lock_sha256"], str) or not _SHA256.fullmatch(
        runtime["uv_lock_sha256"]
    ):
        raise ValueError("matrix runtime uv_lock_sha256 must be SHA-256")
    return _canonical_json_copy(value)


def _cell_id(window: str, scenario: str) -> str:
    return f"{window}/{scenario}"


def _exact_equality(
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
) -> Sequence[str]:
    return () if dict(candidate) == dict(champion) else ("metrics differ",)


def validate_matrix_artifact(
    artifact: Mapping[str, Any],
    *,
    scenarios: Sequence[ContractScenario],
    expected_provenance: Mapping[str, Any],
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
) -> tuple[str, ...]:
    """Validate coverage, finite evidence, provenance, and optional champion equality."""
    failures: list[str] = []
    try:
        expected = _validate_provenance(expected_provenance)
    except ValueError as exc:
        return (f"stale provenance expectation: {exc}",)
    provenance = artifact.get("provenance")
    if not isinstance(provenance, Mapping) or dict(provenance) != expected:
        failures.append("stale provenance differs from exact matrix inputs")
    cells = artifact.get("cells")
    if not isinstance(cells, list):
        return tuple([*failures, "cell collection is missing"])
    expected_by_id = {
        _cell_id(scenario.window.name, scenario.name): scenario for scenario in scenarios
    }
    observed: dict[str, Mapping[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for raw_cell in cells:
        if not isinstance(raw_cell, Mapping):
            failures.append("cell record is malformed")
            continue
        window = raw_cell.get("window")
        scenario_name = raw_cell.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario_name, str):
            failures.append("cell identity is malformed")
            continue
        identifier = _cell_id(window, scenario_name)
        if identifier in observed:
            duplicate_ids.add(identifier)
        observed[identifier] = raw_cell
    if duplicate_ids:
        failures.append(f"duplicate cell records: {sorted(duplicate_ids)}")
    missing = sorted(set(expected_by_id) - set(observed))
    unexpected = sorted(set(observed) - set(expected_by_id))
    if missing:
        failures.append(f"missing cell records: {missing}")
    if unexpected:
        failures.append(f"unexpected cell records: {unexpected}")

    for identifier in sorted(set(expected_by_id) & set(observed)):
        scenario = expected_by_id[identifier]
        cell = observed[identifier]
        expected_contract = _scenario_cell(scenario)
        if any(cell.get(name) != value for name, value in expected_contract.items()):
            failures.append(f"cell contract differs: {identifier}")
            continue
        raw = cell.get("raw")
        metrics = cell.get("metrics")
        if not scenario.economic:
            if raw is not None or metrics is not None:
                failures.append(f"cell insufficient sample contains economic evidence: {identifier}")
            continue
        if not isinstance(raw, Mapping) or not isinstance(metrics, Mapping):
            failures.append(f"cell economic evidence is missing: {identifier}")
            continue
        try:
            canonical_raw = _canonical_json_copy(raw)
            extracted, _ = _metrics_from_raw(scenario, canonical_raw)
        except (TypeError, ValueError) as exc:
            failures.append(f"cell nonfinite or invalid: {identifier}: {exc}")
            continue
        if set(metrics) != _METRIC_FIELDS or dict(metrics) != extracted:
            failures.append(f"cell metrics do not match raw evidence: {identifier}")

    if champion_cells is not None:
        economic_ids = {
            identifier for identifier, scenario in expected_by_id.items() if scenario.economic
        }
        if set(champion_cells) != economic_ids:
            failures.append("champion equality coverage differs from economic matrix")
        evaluator = _exact_equality if cell_evaluator is None else cell_evaluator
        for identifier in sorted(economic_ids & set(champion_cells) & set(observed)):
            metrics = observed[identifier].get("metrics")
            if not isinstance(metrics, Mapping):
                continue
            reasons = tuple(evaluator(metrics, champion_cells[identifier]))
            if reasons:
                failures.append(f"champion equality failed: {identifier}: {list(reasons)}")
    return tuple(failures)


def execute_generalization_matrix(
    *,
    scenarios: Sequence[ContractScenario],
    runner: Callable[[ContractScenario], Mapping[str, Any]],
    provenance: Mapping[str, Any],
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
) -> dict[str, Any]:
    """Execute every economic scenario once and retain every raw result."""
    scenario_tuple = tuple(scenarios)
    if not scenario_tuple:
        raise ValueError("generalization matrix requires scenarios")
    normalized_provenance = _validate_provenance(provenance)
    if normalized_provenance["window_fingerprint"] != window_contract_fingerprint(scenario_tuple):
        raise ValueError("matrix provenance window fingerprint is stale")
    if normalized_provenance["scenario_fingerprint"] != scenario_contract_fingerprint(
        scenario_tuple
    ):
        raise ValueError("matrix provenance scenario fingerprint is stale")

    cells: list[dict[str, Any]] = []
    metrics: list[dict[str, float | int]] = []
    observations: list[GeneralizationObservation] = []
    by_window_metrics: dict[str, list[dict[str, float | int]]] = {}
    by_window_observations: dict[str, list[GeneralizationObservation]] = {}
    for scenario in scenario_tuple:
        cell = _scenario_cell(scenario)
        if not scenario.economic:
            cell.update(raw=None, metrics=None)
            cells.append(cell)
            continue
        raw = _canonical_json_copy(runner(scenario))
        compact, observation = _metrics_from_raw(scenario, raw)
        cell.update(raw=raw, metrics=compact)
        cells.append(cell)
        metrics.append(compact)
        observations.append(observation)
        by_window_metrics.setdefault(scenario.window.name, []).append(compact)
        by_window_observations.setdefault(scenario.window.name, []).append(observation)
    artifact: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "gate": "ai-era-generalization",
        "passed": True,
        "failures": [],
        "provenance": normalized_provenance,
        "concentration_definition": dict(_CONCENTRATION_DEFINITION),
        "aggregates": {
            "all": _aggregate(metrics, observations),
            "by_window": {
                name: _aggregate(by_window_metrics[name], by_window_observations[name])
                for name in by_window_metrics
            },
        },
        "cells": cells,
    }
    failures = validate_matrix_artifact(
        artifact,
        scenarios=scenario_tuple,
        expected_provenance=normalized_provenance,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
    )
    artifact["failures"] = list(failures)
    artifact["passed"] = not failures
    return artifact


def _industry_sha256(universe: AIUniverse) -> str:
    payload = [
        {
            "symbol": member.symbol,
            "industry": member.industry,
            "effective_from": member.effective_from.isoformat(),
            "effective_to": member.effective_to.isoformat() if member.effective_to else None,
        }
        for member in universe.members
    ]
    return _hash_json(payload)


def _source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / "pyproject.toml", *sorted((root / "uquant").rglob("*.py"))]
    if any(not path.is_file() for path in paths):
        raise RuntimeError("cannot fingerprint exact matrix source")
    for path in paths:
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git(root: Path, arguments: Sequence[str]) -> str:
    from .generalization import _git_stdout

    return _git_stdout(root, list(arguments), label="cannot resolve exact matrix HEAD")


def _head_and_source(root: Path) -> tuple[str, str]:
    status = _git(
        root,
        (
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "uquant",
            "pyproject.toml",
            "uv.lock",
        ),
    )
    if status.strip():
        raise RuntimeError("matrix provenance requires committed source and lockfile")
    head = _git(root, ("rev-parse", "HEAD")).strip()
    if not _COMMIT.fullmatch(head):
        raise RuntimeError("cannot resolve exact matrix HEAD")
    source = _source_fingerprint(root)
    tracked_paths = ["pyproject.toml", *[path.relative_to(root).as_posix() for path in sorted((root / "uquant").rglob("*.py"))]]
    digest = hashlib.sha256()
    for relative_text in tracked_paths:
        relative = relative_text.encode()
        content = _git(root, ("show", f"{head}:{relative_text}")).encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    if digest.hexdigest() != source:
        raise RuntimeError("matrix source does not match exact checked-out HEAD")
    return head, source


def build_matrix_provenance(
    *,
    data_dir: str | Path,
    scenarios: Sequence[ContractScenario],
    universe: AIUniverse | None = None,
) -> dict[str, Any]:
    """Build non-self-signable provenance from exact repository and runtime inputs."""
    canonical = load_ai_universe() if universe is None else universe
    root = Path(__file__).resolve().parents[2]
    head, source = _head_and_source(root)
    return _validate_provenance(
        {
            "head": head,
            "source_sha256": source,
            "effective_config_sha256": config_fingerprint(),
            "data": verify_data_manifest(data_dir),
            "runtime": runtime_environment_provenance(root),
            "universe_sha256": canonical.sha256,
            "industry_sha256": _industry_sha256(canonical),
            "window_fingerprint": window_contract_fingerprint(scenarios),
            "scenario_fingerprint": scenario_contract_fingerprint(tuple(scenarios)),
        }
    )


def run_generalization_matrix(
    *,
    data_dir: str | Path,
    window_names: tuple[str, ...] | None = None,
    lookback_sessions: int = 120,
    runner: Callable[[ContractScenario], Mapping[str, Any]] | None = None,
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
) -> dict[str, Any]:
    """Run a full or exact-window shard through the production reference context."""
    windows = official_windows(window_names)
    universe = load_ai_universe()
    engine = ProductionEngine(data_dir)
    engine._load(universe.symbols)
    histories = {symbol: engine._raw[symbol]["close"] for symbol in universe.symbols}
    scenarios = tuple(
        scenario
        for window in windows
        for scenario in build_official_scenarios(
            window=window,
            evidence=compute_pre_window_evidence(
                histories,
                universe.symbols,
                window_start=window.start,
                lookback_sessions=lookback_sessions,
            ),
            universe=universe,
        )
    )
    provenance_before = build_matrix_provenance(
        data_dir=data_dir,
        scenarios=scenarios,
        universe=universe,
    )

    def production_runner(scenario: ContractScenario) -> Mapping[str, Any]:
        raw = engine.backtest(
            symbols=scenario.symbols,
            start=scenario.window.start,
            end=scenario.window.end,
        )
        if raw.get("effective_config_sha256") != provenance_before["effective_config_sha256"]:
            raise RuntimeError(f"matrix effective config drifted during replay: {scenario.name}")
        account = raw.get("final_account")
        if not isinstance(account, Mapping):
            raise RuntimeError(f"matrix replay has no final account: {scenario.name}")
        positions = account.get("positions")
        if not isinstance(positions, Mapping):
            raise RuntimeError(f"matrix replay has invalid final positions: {scenario.name}")
        final_date = pd.Timestamp(cast(str, raw["end"]))
        final_prices = {
            str(symbol): engine._price(str(symbol), final_date)
            for symbol, position in positions.items()
            if isinstance(position, Mapping) and int(position.get("shares", 0)) > 0
        }
        enriched = dict(raw)
        enriched["symbol_pnl"] = symbol_pnl_from_result(raw, final_prices)
        return enriched

    selected_runner = production_runner if runner is None else runner
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=selected_runner,
        provenance=provenance_before,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
    )
    provenance_after = build_matrix_provenance(
        data_dir=data_dir,
        scenarios=scenarios,
        universe=universe,
    )
    if provenance_after != provenance_before:
        raise RuntimeError("matrix engine, source, config, data, or runtime changed during replay")
    return artifact
