"""Execution, aggregation, and fail-closed validation of the AI-era matrix."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pandas as pd

import uquant.validation.generalization_matrix_evidence as _matrix_evidence

from ..attribution import validate_attribution_against_engine_result
from ..config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from ..engine import ProductionEngine, code_fingerprint
from .ai_era import runtime_environment_provenance
from .control_plane import validate_engine_control_plane
from .generalization import (
    GeneralizationObservation,
    compute_pre_window_evidence,
    symbol_pnl_from_result,
)
from .generalization_contract import (
    ContractScenario,
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from .generalization_matrix_validation import (
    CellEvaluator,
    validate_matrix_artifact_owner,
)
from .manifest import verify_data_manifest
from .replay_evidence import VerifiedMarketData
from .universe import AIUniverse, load_ai_universe

_ARTIFACT_FIELDS = _matrix_evidence.ARTIFACT_FIELDS
_ATTRIBUTION_DEFINITION = _matrix_evidence.ATTRIBUTION_DEFINITION
_CELL_EVIDENCE_FIELDS = _matrix_evidence.CELL_EVIDENCE_FIELDS
_COMMIT = _matrix_evidence.COMMIT_PATTERN
_CONCENTRATION_DEFINITION = _matrix_evidence.CONCENTRATION_DEFINITION
_DATA_FIELDS = _matrix_evidence.DATA_FIELDS
_FIXED_SOURCE_PATHS = _matrix_evidence.FIXED_SOURCE_PATHS
_METRIC_FIELDS = _matrix_evidence.METRIC_FIELDS
_PROVENANCE_FIELDS = _matrix_evidence.PROVENANCE_FIELDS
_RUNTIME_FIELDS = _matrix_evidence.RUNTIME_FIELDS
_SCHEMA_VERSION = _matrix_evidence.SCHEMA_VERSION
_SHA256 = _matrix_evidence.SHA256_PATTERN
_aggregate = _matrix_evidence.aggregate_matrix_observations
_canonical_json_copy = _matrix_evidence.canonical_json_copy
_canonical_replay_error = _matrix_evidence.canonical_replay_error
_hash_json = _matrix_evidence.hash_matrix_json
_metrics_from_raw = _matrix_evidence.metrics_from_raw
_scenario_cell = _matrix_evidence.scenario_cell
_validate_provenance = _matrix_evidence.validate_matrix_provenance


def window_contract_fingerprint(scenarios: Sequence[ContractScenario]) -> str:
    """Hash the exact distinct official windows represented by a shard."""
    return _matrix_evidence.matrix_window_fingerprint(scenarios)


def evidence_contract_fingerprint(scenarios: Sequence[ContractScenario]) -> str:
    """Hash each window's causal evidence and configured lookback."""
    return _matrix_evidence.matrix_evidence_fingerprint(scenarios)


def validate_matrix_artifact(
    artifact: Mapping[str, Any],
    *,
    scenarios: Sequence[ContractScenario],
    expected_provenance: Mapping[str, Any],
    data_dir: str | Path,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
    _verify_gate_state: bool = True,
    _verified_market: VerifiedMarketData | None = None,
) -> tuple[str, ...]:
    """Validate coverage, finite evidence, provenance, and optional champion equality."""
    return validate_matrix_artifact_owner(
        artifact,
        scenarios=scenarios,
        expected_provenance=expected_provenance,
        data_dir=data_dir,
        expected_config=expected_config,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
        verify_gate_state=_verify_gate_state,
        verified_market=_verified_market,
    )


def _matrix_execution_context(
    *,
    scenarios: Sequence[ContractScenario],
    provenance: Mapping[str, Any],
    data_dir: str | Path,
    expected_config: SystemConfig | None,
) -> tuple[
    tuple[ContractScenario, ...],
    dict[str, Any],
    SystemConfig,
    VerifiedMarketData,
]:
    scenario_tuple = tuple(scenarios)
    if not scenario_tuple:
        raise ValueError("generalization matrix requires scenarios")
    normalized_provenance = _validate_provenance(provenance)
    if not isinstance(expected_config, SystemConfig):
        raise ValueError("matrix execution requires a trusted effective config")
    if normalized_provenance["effective_config_sha256"] != config_fingerprint(expected_config):
        raise ValueError("matrix provenance differs from trusted effective config")
    market = VerifiedMarketData(
        data_dir,
        expected_manifest=cast(Mapping[str, Any], normalized_provenance["data"]),
    )
    if normalized_provenance["window_fingerprint"] != window_contract_fingerprint(scenario_tuple):
        raise ValueError("matrix provenance window fingerprint is stale")
    if normalized_provenance["scenario_fingerprint"] != scenario_contract_fingerprint(scenario_tuple):
        raise ValueError("matrix provenance scenario fingerprint is stale")
    if normalized_provenance["evidence_fingerprint"] != evidence_contract_fingerprint(scenario_tuple):
        raise ValueError("matrix provenance evidence fingerprint is stale")
    lookbacks = {scenario.lookback_sessions for scenario in scenario_tuple}
    if lookbacks != {normalized_provenance["lookback_sessions"]}:
        raise ValueError("matrix provenance lookback configuration is stale")
    return scenario_tuple, normalized_provenance, expected_config, market


@dataclass(slots=True)
class _MatrixExecutionEvidence:
    cells: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, float | int]] = field(default_factory=list)
    observations: list[GeneralizationObservation] = field(default_factory=list)
    by_window_metrics: dict[str, list[dict[str, float | int]]] = field(default_factory=dict)
    by_window_observations: dict[str, list[GeneralizationObservation]] = field(default_factory=dict)
    replay_error_cells: int = 0
    by_window_replay_errors: dict[str, int] = field(default_factory=dict)
    expected_by_window: dict[str, int] = field(default_factory=dict)


def _execute_matrix_scenario(
    evidence: _MatrixExecutionEvidence,
    *,
    scenario: ContractScenario,
    runner: Callable[[ContractScenario], Mapping[str, Any]],
    expected_config: SystemConfig,
    market: VerifiedMarketData,
) -> None:
    cell = _scenario_cell(scenario)
    if not scenario.economic:
        cell.update(
            raw=None,
            metrics=None,
            replay_error=None,
            attribution_status="INSUFFICIENT_SAMPLE",
            attribution=None,
            concentration=None,
        )
        evidence.cells.append(cell)
        return
    window = scenario.window.name
    evidence.expected_by_window[window] = evidence.expected_by_window.get(window, 0) + 1
    try:
        raw = _canonical_json_copy(runner(scenario))
        compact, observation = _metrics_from_raw(scenario, raw)
        trusted_sessions = market.sessions(
            scenario.window.start,
            scenario.window.end,
        )
        attribution = raw.get("attribution")
        if not isinstance(attribution, Mapping):
            raise ValueError("engine result economic attribution is missing")
        validate_engine_control_plane(
            raw,
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            expected_sessions=trusted_sessions,
            expected_config=expected_config,
            expected_code_sha256=code_fingerprint(),
            attribution=attribution,
        )
        canonical_attribution = validate_attribution_against_engine_result(
            raw,
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            attribution=attribution,
            trusted_sessions=trusted_sessions,
            trusted_close=market.close,
            require_daily_replay_evidence=True,
        )
    except Exception as exc:
        cell.update(
            raw=None,
            metrics=None,
            replay_error=_canonical_replay_error(exc),
            attribution_status="ERROR",
            attribution=None,
            concentration=None,
        )
        evidence.cells.append(cell)
        evidence.replay_error_cells += 1
        evidence.by_window_replay_errors[window] = evidence.by_window_replay_errors.get(window, 0) + 1
        return
    stored_raw = dict(raw)
    stored_raw.pop("attribution", None)
    cell.update(
        raw=stored_raw,
        metrics=compact,
        replay_error=None,
        attribution_status="VALID",
        attribution=canonical_attribution,
        concentration=canonical_attribution["symbol_concentration"],
    )
    evidence.cells.append(cell)
    evidence.metrics.append(compact)
    evidence.observations.append(observation)
    evidence.by_window_metrics.setdefault(window, []).append(compact)
    evidence.by_window_observations.setdefault(window, []).append(observation)


def _matrix_artifact(
    evidence: _MatrixExecutionEvidence,
    *,
    normalized_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "gate": "ai-era-generalization",
        "passed": True,
        "failures": [],
        "provenance": normalized_provenance,
        "concentration_definition": dict(_CONCENTRATION_DEFINITION),
        "attribution_definition": dict(_ATTRIBUTION_DEFINITION),
        "aggregates": {
            "all": _aggregate(
                evidence.metrics,
                evidence.observations,
                expected_cells=len(evidence.metrics) + evidence.replay_error_cells,
                replay_error_cells=evidence.replay_error_cells,
            ),
            "by_window": {
                name: _aggregate(
                    evidence.by_window_metrics[name],
                    evidence.by_window_observations[name],
                    expected_cells=evidence.expected_by_window[name],
                    replay_error_cells=evidence.by_window_replay_errors.get(name, 0),
                )
                for name in evidence.by_window_metrics
            },
        },
        "cells": evidence.cells,
    }


def execute_generalization_matrix(
    *,
    scenarios: Sequence[ContractScenario],
    runner: Callable[[ContractScenario], Mapping[str, Any]],
    provenance: Mapping[str, Any],
    data_dir: str | Path,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
) -> dict[str, Any]:
    """Execute every economic scenario once and retain every raw result."""
    scenario_tuple, normalized, trusted_config, market = _matrix_execution_context(
        scenarios=scenarios,
        provenance=provenance,
        data_dir=data_dir,
        expected_config=expected_config,
    )
    evidence = _MatrixExecutionEvidence()
    for scenario in scenario_tuple:
        _execute_matrix_scenario(
            evidence,
            scenario=scenario,
            runner=runner,
            expected_config=trusted_config,
            market=market,
        )
    artifact = _matrix_artifact(evidence, normalized_provenance=normalized)
    failures = validate_matrix_artifact(
        artifact,
        scenarios=scenario_tuple,
        expected_provenance=normalized,
        data_dir=data_dir,
        expected_config=trusted_config,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
        _verify_gate_state=False,
        _verified_market=market,
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


def _source_paths(root: Path) -> tuple[Path, ...]:
    fixed = [root / relative for relative in _FIXED_SOURCE_PATHS]
    python_sources = sorted((root / "uquant").rglob("*.py"))
    package_resources = sorted((root / "uquant" / "validation" / "resources").glob("*.json"))
    paths = tuple(sorted({*fixed, *python_sources, *package_resources}))
    if any(not path.is_file() for path in paths) or not package_resources:
        raise RuntimeError("cannot resolve exact matrix source and package resources")
    return paths


def _source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _source_paths(root):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git(root: Path, arguments: Sequence[str]) -> str:
    from .generalization import git_stdout as _git_stdout

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
            *_FIXED_SOURCE_PATHS,
        ),
    )
    if status.strip():
        raise RuntimeError("matrix provenance requires committed source and lockfile")
    head = _git(root, ("rev-parse", "HEAD")).strip()
    if not _COMMIT.fullmatch(head):
        raise RuntimeError("cannot resolve exact matrix HEAD")
    source = _source_fingerprint(root)
    tracked_paths = [path.relative_to(root).as_posix() for path in _source_paths(root)]
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
    expected_config: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Build non-self-signable provenance from exact repository and runtime inputs."""
    canonical = load_ai_universe() if universe is None else universe
    lookbacks = {scenario.lookback_sessions for scenario in scenarios}
    if len(lookbacks) != 1:
        raise ValueError("matrix provenance requires one exact lookback configuration")
    root = Path(__file__).resolve().parents[2]
    head, source = _head_and_source(root)
    return _validate_provenance(
        {
            "head": head,
            "source_sha256": source,
            "effective_config_sha256": config_fingerprint(expected_config),
            "data": verify_data_manifest(data_dir),
            "runtime": runtime_environment_provenance(root),
            "universe_sha256": canonical.sha256,
            "industry_sha256": _industry_sha256(canonical),
            "window_fingerprint": window_contract_fingerprint(scenarios),
            "scenario_fingerprint": scenario_contract_fingerprint(tuple(scenarios)),
            "evidence_fingerprint": evidence_contract_fingerprint(scenarios),
            "lookback_sessions": next(iter(lookbacks)),
        }
    )


def run_generalization_matrix(
    *,
    data_dir: str | Path,
    window_names: tuple[str, ...] | None = None,
    lookback_sessions: int = 120,
    expected_config: SystemConfig = DEFAULT_CONFIG,
    runner: Callable[[ContractScenario], Mapping[str, Any]] | None = None,
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
) -> dict[str, Any]:
    """Run a full or exact-window shard through the production reference context."""
    windows = official_windows(window_names)
    universe = load_ai_universe()
    engine = ProductionEngine(data_dir, cfg=expected_config)
    engine.workspace.load(universe.symbols)
    histories = {symbol: engine.workspace.raw_frame(symbol)["close"] for symbol in universe.symbols}
    scenario_rows: list[ContractScenario] = []
    for window in windows:
        causal_cutoff = (pd.Timestamp(window.start) - pd.Timedelta(days=1)).date().isoformat()
        candidate_symbols = universe.symbols_as_of(causal_cutoff)
        evidence = compute_pre_window_evidence(
            histories,
            candidate_symbols,
            window_start=window.start,
            lookback_sessions=lookback_sessions,
        )
        pit_symbols = universe.symbols_as_of(evidence.as_of)
        if pit_symbols != candidate_symbols:
            evidence = compute_pre_window_evidence(
                histories,
                pit_symbols,
                window_start=window.start,
                lookback_sessions=lookback_sessions,
            )
        scenario_rows.extend(
            build_official_scenarios(
                window=window,
                evidence=evidence,
                universe=universe,
                lookback_sessions=lookback_sessions,
            )
        )
    scenarios = tuple(scenario_rows)
    provenance_before = build_matrix_provenance(
        data_dir=data_dir,
        scenarios=scenarios,
        universe=universe,
        expected_config=expected_config,
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
            str(symbol): engine.workspace.price(str(symbol), final_date)
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
        data_dir=data_dir,
        expected_config=expected_config,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
    )
    provenance_after = build_matrix_provenance(
        data_dir=data_dir,
        scenarios=scenarios,
        universe=universe,
        expected_config=expected_config,
    )
    if provenance_after != provenance_before:
        raise RuntimeError("matrix engine, source, config, data, or runtime changed during replay")
    return artifact


aggregate_matrix_observations = _aggregate
hash_matrix_json = _hash_json
head_and_source = _head_and_source
industry_sha256 = _industry_sha256

_quantile = _matrix_evidence.matrix_quantile
