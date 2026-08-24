"""Fail-closed validation stages for generalization matrix artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ..attribution import (
    validate_attribution_against_engine_result,
    validate_economic_attribution,
)
from ..config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from ..engine import code_fingerprint
from .control_plane import validate_engine_control_plane
from .generalization import GeneralizationObservation
from .generalization_contract import ContractScenario
from .generalization_matrix_evidence import (
    ARTIFACT_FIELDS,
    ATTRIBUTION_DEFINITION,
    CELL_EVIDENCE_FIELDS,
    CONCENTRATION_DEFINITION,
    METRIC_FIELDS,
    SCHEMA_VERSION,
    aggregate_matrix_observations,
    canonical_json_copy,
    metrics_from_raw,
    scenario_cell,
    validate_matrix_provenance,
)
from .replay_evidence import VerifiedMarketData

CellEvaluator = Callable[[Mapping[str, Any], Mapping[str, Any]], Sequence[str]]


def _cell_id(window: str, scenario: str) -> str:
    return f"{window}/{scenario}"


def _exact_equality(
    candidate: Mapping[str, Any],
    champion: Mapping[str, Any],
) -> Sequence[str]:
    return () if dict(candidate) == dict(champion) else ("metrics differ",)


def _artifact_header(
    artifact: Mapping[str, Any],
) -> tuple[list[str], object, object]:
    failures: list[str] = []
    if set(artifact) != ARTIFACT_FIELDS:
        failures.append("schema fields differ from the exact matrix artifact contract")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        failures.append("schema version differs from the exact matrix artifact contract")
    if artifact.get("gate") != "ai-era-generalization":
        failures.append("gate identity differs from the AI-era generalization contract")
    if artifact.get("concentration_definition") != CONCENTRATION_DEFINITION:
        failures.append("concentration definition differs from the exact accounting contract")
    if artifact.get("attribution_definition") != ATTRIBUTION_DEFINITION:
        failures.append("attribution definition differs from the exact economic contract")
    advertised_passed = artifact.get("passed")
    advertised_failures = artifact.get("failures")
    if not isinstance(advertised_passed, bool):
        failures.append("gate state passed flag is malformed")
    if not isinstance(advertised_failures, list) or any(
        not isinstance(item, str) for item in advertised_failures
    ):
        failures.append("gate state failures are malformed")
    return failures, advertised_passed, advertised_failures


@dataclass(slots=True)
class _MatrixValidationState:
    scenarios: Sequence[ContractScenario]
    expected_config: SystemConfig
    market: VerifiedMarketData
    failures: list[str]
    expected_by_id: dict[str, ContractScenario] = field(init=False)
    observed: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    validated_metrics: list[dict[str, float | int]] = field(default_factory=list)
    validated_observations: list[GeneralizationObservation] = field(default_factory=list)
    by_window_metrics: dict[str, list[dict[str, float | int]]] = field(default_factory=dict)
    by_window_observations: dict[str, list[GeneralizationObservation]] = field(default_factory=dict)
    replay_error_cells: int = 0
    by_window_replay_errors: dict[str, int] = field(default_factory=dict)
    duplicate_ids: set[str] = field(default_factory=set)
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.expected_by_id = {
            _cell_id(scenario.window.name, scenario.name): scenario for scenario in self.scenarios
        }


def _collect_cells(state: _MatrixValidationState, cells: list[Any]) -> None:
    for raw_cell in cells:
        if not isinstance(raw_cell, Mapping):
            state.failures.append("cell record is malformed")
            continue
        window = raw_cell.get("window")
        scenario_name = raw_cell.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario_name, str):
            state.failures.append("cell identity is malformed")
            continue
        identifier = _cell_id(window, scenario_name)
        if identifier in state.observed:
            state.duplicate_ids.add(identifier)
        state.observed[identifier] = raw_cell
    if state.duplicate_ids:
        state.failures.append(f"duplicate cell records: {sorted(state.duplicate_ids)}")
    state.missing = sorted(set(state.expected_by_id) - set(state.observed))
    state.unexpected = sorted(set(state.observed) - set(state.expected_by_id))
    if state.missing:
        state.failures.append(f"missing cell records: {state.missing}")
    if state.unexpected:
        state.failures.append(f"unexpected cell records: {state.unexpected}")


def _cell_contract_valid(
    state: _MatrixValidationState,
    *,
    identifier: str,
    scenario: ContractScenario,
    cell: Mapping[str, Any],
) -> bool:
    expected_contract = scenario_cell(scenario)
    if set(cell) != set(expected_contract) | CELL_EVIDENCE_FIELDS:
        state.failures.append(f"cell attribution/evidence fields differ from the exact schema: {identifier}")
        return False
    if any(cell.get(name) != value for name, value in expected_contract.items()):
        state.failures.append(f"cell contract differs: {identifier}")
        return False
    if "replay_error" not in cell:
        state.failures.append(f"cell replay error state is missing: {identifier}")
        return False
    return True


def _insufficient_cell_valid(
    state: _MatrixValidationState,
    *,
    identifier: str,
    cell: Mapping[str, Any],
) -> bool:
    valid = not (
        cell.get("raw") is not None
        or cell.get("metrics") is not None
        or cell.get("replay_error") is not None
        or cell.get("attribution_status") != "INSUFFICIENT_SAMPLE"
        or cell.get("attribution") is not None
        or cell.get("concentration") is not None
    )
    if not valid:
        state.failures.append(f"cell insufficient sample contains economic evidence: {identifier}")
    return valid


def _record_replay_error(
    state: _MatrixValidationState,
    *,
    identifier: str,
    scenario: ContractScenario,
    cell: Mapping[str, Any],
    replay_error: object,
) -> None:
    if (
        cell.get("raw") is not None
        or cell.get("metrics") is not None
        or cell.get("attribution_status") != "ERROR"
        or cell.get("attribution") is not None
        or cell.get("concentration") is not None
    ):
        state.failures.append(f"cell replay error contains fabricated metrics: {identifier}")
        return
    if (
        not isinstance(replay_error, Mapping)
        or set(replay_error) != {"exception_type", "message"}
        or not isinstance(replay_error.get("exception_type"), str)
        or not replay_error["exception_type"]
        or not isinstance(replay_error.get("message"), str)
        or not replay_error["message"]
        or " ".join(replay_error["message"].split()) != replay_error["message"]
    ):
        state.failures.append(f"cell replay error is malformed: {identifier}")
        return
    state.replay_error_cells += 1
    window = scenario.window.name
    state.by_window_replay_errors[window] = state.by_window_replay_errors.get(window, 0) + 1
    state.failures.append(
        f"cell replay failed: {identifier}: {replay_error['exception_type']}: {replay_error['message']}"
    )


def _validated_economic_evidence(
    state: _MatrixValidationState,
    *,
    identifier: str,
    scenario: ContractScenario,
    cell: Mapping[str, Any],
) -> tuple[dict[str, float | int], GeneralizationObservation] | None:
    raw = cell.get("raw")
    metrics = cell.get("metrics")
    attribution = cell.get("attribution")
    if not isinstance(raw, Mapping) or not isinstance(metrics, Mapping):
        state.failures.append(f"cell economic evidence is missing: {identifier}")
        return None
    if cell.get("attribution_status") != "VALID" or not isinstance(attribution, Mapping):
        state.failures.append(f"cell attribution is missing: {identifier}")
        return None
    try:
        canonical_raw = canonical_json_copy(raw)
        extracted, observation = metrics_from_raw(scenario, canonical_raw)
        canonical_attribution = validate_economic_attribution(
            attribution,
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
        )
        trusted_sessions = state.market.sessions(
            scenario.window.start,
            scenario.window.end,
        )
        validate_engine_control_plane(
            canonical_raw,
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            expected_sessions=trusted_sessions,
            expected_config=state.expected_config,
            expected_code_sha256=code_fingerprint(),
            attribution=canonical_attribution,
        )
        raw_attribution = validate_attribution_against_engine_result(
            canonical_raw,
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            attribution=canonical_attribution,
            trusted_sessions=trusted_sessions,
            trusted_close=state.market.close,
            require_daily_replay_evidence=True,
        )
    except (TypeError, ValueError) as exc:
        state.failures.append(f"cell nonfinite or invalid attribution: {identifier}: {exc}")
        return None
    if canonical_attribution != raw_attribution:
        state.failures.append(f"cell attribution differs from raw economic evidence: {identifier}")
        return None
    if cell.get("concentration") != canonical_attribution["symbol_concentration"]:
        state.failures.append(f"cell concentration differs from validated attribution: {identifier}")
        return None
    if set(metrics) != METRIC_FIELDS or dict(metrics) != extracted:
        state.failures.append(f"cell metrics do not match raw evidence: {identifier}")
        return None
    return extracted, observation


def _record_economic_evidence(
    state: _MatrixValidationState,
    *,
    scenario: ContractScenario,
    evidence: tuple[dict[str, float | int], GeneralizationObservation],
) -> None:
    extracted, observation = evidence
    state.validated_metrics.append(extracted)
    state.validated_observations.append(observation)
    state.by_window_metrics.setdefault(scenario.window.name, []).append(extracted)
    state.by_window_observations.setdefault(scenario.window.name, []).append(observation)


def _validate_cells(state: _MatrixValidationState) -> None:
    for identifier in sorted(set(state.expected_by_id) & set(state.observed)):
        scenario = state.expected_by_id[identifier]
        cell = state.observed[identifier]
        if not _cell_contract_valid(
            state,
            identifier=identifier,
            scenario=scenario,
            cell=cell,
        ):
            continue
        if not scenario.economic:
            _insufficient_cell_valid(state, identifier=identifier, cell=cell)
            continue
        replay_error = cell.get("replay_error")
        if replay_error is not None:
            _record_replay_error(
                state,
                identifier=identifier,
                scenario=scenario,
                cell=cell,
                replay_error=replay_error,
            )
            continue
        evidence = _validated_economic_evidence(
            state,
            identifier=identifier,
            scenario=scenario,
            cell=cell,
        )
        if evidence is not None:
            _record_economic_evidence(state, scenario=scenario, evidence=evidence)


def _expected_aggregates(state: _MatrixValidationState) -> dict[str, Any] | None:
    expected_economic = sum(scenario.economic for scenario in state.scenarios)
    if not (
        len(state.validated_metrics) + state.replay_error_cells == expected_economic
        and state.validated_metrics
        and not state.duplicate_ids
        and not state.missing
        and not state.unexpected
    ):
        return None
    expected_by_window = {
        name: sum(scenario.economic for scenario in state.scenarios if scenario.window.name == name)
        for name in {scenario.window.name for scenario in state.scenarios}
    }
    return {
        "all": aggregate_matrix_observations(
            state.validated_metrics,
            state.validated_observations,
            expected_cells=expected_economic,
            replay_error_cells=state.replay_error_cells,
        ),
        "by_window": {
            name: aggregate_matrix_observations(
                state.by_window_metrics[name],
                state.by_window_observations[name],
                expected_cells=expected_by_window[name],
                replay_error_cells=state.by_window_replay_errors.get(name, 0),
            )
            for name in state.by_window_metrics
        },
    }


def _validate_aggregates(
    state: _MatrixValidationState,
    artifact: Mapping[str, Any],
) -> None:
    expected = _expected_aggregates(state)
    if expected is None:
        return
    raw_aggregates = artifact.get("aggregates")
    if not isinstance(raw_aggregates, Mapping):
        state.failures.append("aggregate evidence is missing")
        return
    try:
        canonical = canonical_json_copy({"aggregates": raw_aggregates})["aggregates"]
    except ValueError as exc:
        state.failures.append(f"aggregate evidence is nonfinite or malformed: {exc}")
        return
    if canonical != expected:
        state.failures.append("aggregate evidence does not recompute from raw economic cells")


def _validate_matrix_champion(
    state: _MatrixValidationState,
    *,
    champion_cells: Mapping[str, Mapping[str, Any]] | None,
    cell_evaluator: CellEvaluator | None,
) -> None:
    if champion_cells is None:
        return
    economic_ids = {identifier for identifier, scenario in state.expected_by_id.items() if scenario.economic}
    if set(champion_cells) != economic_ids:
        state.failures.append("champion equality coverage differs from economic matrix")
    evaluator = _exact_equality if cell_evaluator is None else cell_evaluator
    for identifier in sorted(economic_ids & set(champion_cells) & set(state.observed)):
        metrics = state.observed[identifier].get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        reasons = tuple(evaluator(metrics, champion_cells[identifier]))
        if reasons:
            state.failures.append(f"champion equality failed: {identifier}: {list(reasons)}")


def validate_matrix_artifact_owner(
    artifact: Mapping[str, Any],
    *,
    scenarios: Sequence[ContractScenario],
    expected_provenance: Mapping[str, Any],
    data_dir: str | Path,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
    champion_cells: Mapping[str, Mapping[str, Any]] | None = None,
    cell_evaluator: CellEvaluator | None = None,
    verify_gate_state: bool = True,
    verified_market: VerifiedMarketData | None = None,
) -> tuple[str, ...]:
    """Validate coverage, finite evidence, provenance, and champion equality."""
    failures, advertised_passed, advertised_failures = _artifact_header(artifact)
    try:
        expected = validate_matrix_provenance(expected_provenance)
    except ValueError as exc:
        return (f"stale provenance expectation: {exc}",)
    if not isinstance(expected_config, SystemConfig):
        return tuple([*failures, "trusted effective config is missing or malformed"])
    if expected["effective_config_sha256"] != config_fingerprint(expected_config):
        return tuple([*failures, "trusted effective config differs from matrix provenance"])
    try:
        market = (
            VerifiedMarketData(
                data_dir,
                expected_manifest=cast(Mapping[str, Any], expected["data"]),
            )
            if verified_market is None
            else verified_market
        )
    except (RuntimeError, ValueError) as exc:
        return tuple([*failures, f"verified daily replay market is invalid: {exc}"])
    provenance = artifact.get("provenance")
    if not isinstance(provenance, Mapping) or dict(provenance) != expected:
        failures.append("stale provenance differs from exact matrix inputs")
    cells = artifact.get("cells")
    if not isinstance(cells, list):
        return tuple([*failures, "cell collection is missing"])
    state = _MatrixValidationState(
        scenarios=scenarios,
        expected_config=expected_config,
        market=market,
        failures=failures,
    )
    _collect_cells(state, cells)
    _validate_cells(state)
    _validate_aggregates(state, artifact)
    _validate_matrix_champion(
        state,
        champion_cells=champion_cells,
        cell_evaluator=cell_evaluator,
    )
    computed_failures = tuple(failures)
    if verify_gate_state and (
        advertised_passed != (not computed_failures) or advertised_failures != list(computed_failures)
    ):
        failures.append("gate state passed/failures do not match recomputed validation")
    return tuple(failures)
