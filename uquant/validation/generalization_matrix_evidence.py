"""Canonical evidence structures shared by matrix execution and validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any, Final

from ..config_governance import GOVERNANCE_PATH
from .generalization import (
    GeneralizationObservation,
    aggregate_metrics,
    observation_from_result,
    symbol_pnl_concentration,
)
from .generalization_contract import (
    ContractScenario,
    evidence_contract_payload,
)
from .generalization_policy.schema import ImmutableGeneralizationDefinition

SCHEMA_VERSION: Final = 2
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40,64}$")
PROVENANCE_FIELDS: Final = frozenset(
    {
        "head",
        "source_sha256",
        "effective_config_sha256",
        "data",
        "runtime",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
        "lookback_sessions",
    }
)
DATA_FIELDS: Final = frozenset({"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"})
RUNTIME_FIELDS: Final = frozenset(
    {
        "python_full_version",
        "numpy_version",
        "pandas_version",
        "uv_version",
        "uv_lock_sha256",
    }
)
METRIC_FIELDS: Final = frozenset(
    {
        "final_wealth",
        "max_drawdown",
        "account_orders",
        "gross_turnover",
        "annual_turnover",
        "top1_concentration",
        "top3_concentration",
        "pnl_hhi",
    }
)
CONCENTRATION_DEFINITION: Final = ImmutableGeneralizationDefinition(
    {
        "basis": "absolute exact symbol PnL contribution",
        "denominator": "sum(abs(symbol_pnl))",
        "top1": "largest abs(symbol_pnl) divided by denominator",
        "top3": "sum of three largest abs(symbol_pnl) divided by denominator",
        "hhi": "sum((abs(symbol_pnl) / denominator) ** 2)",
        "zero_mass": "all concentration metrics are exactly 0.0",
    }
)
ATTRIBUTION_DEFINITION: Final = ImmutableGeneralizationDefinition(
    {
        "schema": "uquant.economic-attribution.v1",
        "interval": "cell start/end inclusive; no pre-window warmup or post-end data",
        "accounting_identity": "realized_pnl + open_pnl = final_equity - initial_cash",
        "lot_identity": "originating BUY event plus per-SELL sold_tranches",
        "concentration": "positive, signed-net, and absolute PnL denominators",
        "diagnostics": "cash drag and paired risk avoidance are not accounting PnL",
    }
)
FIXED_SOURCE_PATHS: Final = (
    "benchmarks/reference_registry.json",
    GOVERNANCE_PATH.as_posix(),
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
)
ARTIFACT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "gate",
        "passed",
        "failures",
        "provenance",
        "concentration_definition",
        "attribution_definition",
        "aggregates",
        "cells",
    }
)
CELL_EVIDENCE_FIELDS: Final = frozenset(
    {
        "raw",
        "metrics",
        "replay_error",
        "attribution_status",
        "attribution",
        "concentration",
    }
)


def hash_matrix_json(value: Any) -> str:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def matrix_window_fingerprint(scenarios: Sequence[ContractScenario]) -> str:
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
    return hash_matrix_json(windows)


def matrix_evidence_fingerprint(scenarios: Sequence[ContractScenario]) -> str:
    """Hash each window's causal evidence and configured lookback."""
    evidence_by_window: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        payload = evidence_contract_payload(scenario)
        existing = evidence_by_window.setdefault(scenario.window.name, payload)
        if existing != payload:
            raise ValueError(f"matrix window has inconsistent causal evidence: {scenario.window.name}")
    if not evidence_by_window:
        raise ValueError("matrix evidence fingerprint requires scenarios")
    return hash_matrix_json(
        [{"window": name, "evidence": evidence_by_window[name]} for name in evidence_by_window]
    )


def canonical_json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
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


def _validate_matrix_hashes(value: Mapping[str, Any]) -> None:
    for name in (
        "source_sha256",
        "effective_config_sha256",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
    ):
        if not isinstance(value[name], str) or not SHA256_PATTERN.fullmatch(value[name]):
            raise ValueError(f"matrix provenance {name} must be SHA-256")


def _validate_matrix_data(data: Mapping[str, Any]) -> None:
    if not isinstance(data["snapshot_id"], str) or not data["snapshot_id"]:
        raise ValueError("matrix data snapshot is malformed")
    if (
        isinstance(data["files_verified"], bool)
        or not isinstance(data["files_verified"], int)
        or data["files_verified"] < 1
    ):
        raise ValueError("matrix data file count is malformed")
    for name in ("manifest_sha256", "checksums_sha256"):
        if not isinstance(data[name], str) or not SHA256_PATTERN.fullmatch(data[name]):
            raise ValueError(f"matrix data {name} must be SHA-256")


def _validate_matrix_runtime(runtime: Mapping[str, Any]) -> None:
    for name in RUNTIME_FIELDS - {"uv_lock_sha256"}:
        if not isinstance(runtime[name], str) or not runtime[name]:
            raise ValueError(f"matrix runtime {name} is malformed")
    if not isinstance(runtime["uv_lock_sha256"], str) or not SHA256_PATTERN.fullmatch(
        runtime["uv_lock_sha256"]
    ):
        raise ValueError("matrix runtime uv_lock_sha256 must be SHA-256")


def validate_matrix_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the exact matrix provenance envelope."""
    if set(value) != PROVENANCE_FIELDS:
        raise ValueError("matrix provenance fields are incomplete or unexpected")
    _validate_matrix_hashes(value)
    if not isinstance(value["head"], str) or not COMMIT_PATTERN.fullmatch(value["head"]):
        raise ValueError("matrix provenance head must be an immutable commit")
    lookback = value["lookback_sessions"]
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        raise ValueError("matrix provenance lookback_sessions must be positive")
    data = value["data"]
    runtime = value["runtime"]
    if not isinstance(data, Mapping) or set(data) != DATA_FIELDS:
        raise ValueError("matrix data provenance is malformed")
    if not isinstance(runtime, Mapping) or set(runtime) != RUNTIME_FIELDS:
        raise ValueError("matrix runtime provenance is malformed")
    _validate_matrix_data(data)
    _validate_matrix_runtime(runtime)
    return canonical_json_copy(value)


def metrics_from_raw(
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


def matrix_quantile(values: Sequence[float], probability: float) -> float:
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


def aggregate_matrix_observations(
    metrics: Sequence[Mapping[str, float | int]],
    observations: Sequence[GeneralizationObservation],
    *,
    expected_cells: int | None = None,
    replay_error_cells: int = 0,
) -> dict[str, float | int]:
    if not metrics or not observations or len(metrics) != len(observations):
        raise ValueError("matrix aggregate requires matching economic evidence")
    expected = len(metrics) if expected_cells is None else expected_cells
    if expected < 1 or replay_error_cells < 0 or len(metrics) + replay_error_cells != expected:
        raise ValueError("matrix aggregate economic coverage is inconsistent")
    base = aggregate_metrics(observations)
    wealth = [float(item["final_wealth"]) for item in metrics]
    gross = [float(item["gross_turnover"]) for item in metrics]
    annual = [float(item["annual_turnover"]) for item in metrics]
    top1 = [float(item["top1_concentration"]) for item in metrics]
    top3 = [float(item["top3_concentration"]) for item in metrics]
    hhi = [float(item["pnl_hhi"]) for item in metrics]
    return {
        "economic_cells_expected": expected,
        "economic_cells_valid": len(metrics),
        "replay_error_cells": replay_error_cells,
        **base,
        "worst_wealth": min(wealth),
        "median_gross_turnover": float(median(gross)),
        "p90_gross_turnover": matrix_quantile(gross, 0.90),
        "worst_gross_turnover": max(gross),
        "median_annual_turnover": float(median(annual)),
        "p90_annual_turnover": matrix_quantile(annual, 0.90),
        "worst_annual_turnover": max(annual),
        "median_top1_concentration": float(median(top1)),
        "worst_top1_concentration": max(top1),
        "median_top3_concentration": float(median(top3)),
        "worst_top3_concentration": max(top3),
        "median_pnl_hhi": float(median(hhi)),
        "worst_pnl_hhi": max(hhi),
    }


def scenario_cell(scenario: ContractScenario) -> dict[str, Any]:
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
        "evidence": evidence_contract_payload(scenario),
    }


def canonical_replay_error(error: Exception) -> dict[str, str]:
    message = " ".join(str(error).split())
    return {
        "exception_type": type(error).__name__,
        "message": message or "exception had no message",
    }
