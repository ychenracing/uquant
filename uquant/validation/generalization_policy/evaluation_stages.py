"""Ordered validation stages for the frozen generalization policy artifact."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...attribution import validate_attribution_against_engine_result
from ...config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from ...config_governance import (
    GovernedConfigMigration,
    validate_governed_config_migration,
)
from ...engine import code_fingerprint
from ..control_plane import validate_engine_control_plane
from ..replay_evidence import VerifiedMarketData
from . import projection, schema
from .cell_policy import (
    evaluate_relative_cell_non_regression,
    violates_effective_floor,
)
from .schema import (
    GeneralizationBaseline,
    GeneralizationPolicy,
)
from .tail_evaluation import TailEvaluationContext, evaluate_random_tails


@dataclass(slots=True)
class _EvaluationState:
    artifact: Mapping[str, Any]
    baseline: GeneralizationBaseline
    policy: GeneralizationPolicy
    require_exact_equality: bool
    data_dir: str | Path | None
    expected_config: SystemConfig | None
    head_and_source: Callable[[Path], tuple[str, str]]
    schema_version: object
    v2_projection_valid: bool
    expected_cell_fields: Set[str]
    failures: list[str] = field(default_factory=list)
    equality_differences: list[str] = field(default_factory=list)
    raw_cells: list[Any] = field(default_factory=list)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    config_migration: GovernedConfigMigration | None = None
    market: VerifiedMarketData | None = None
    trusted_config: SystemConfig | None = None
    observed: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    invalid_cells: set[str] = field(default_factory=set)
    economic_valid: int = 0
    replay_errors: int = 0
    intrinsic_results: list[dict[str, Any]] = field(default_factory=list)
    random_groups: dict[
        tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]
    ] = field(default_factory=lambda: defaultdict(list))
    reference_random_groups: dict[
        tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]
    ] = field(default_factory=lambda: defaultdict(list))
    tail_results: list[dict[str, Any]] = field(default_factory=list)


def evaluate_policy_stages(
    artifact: Mapping[str, Any],
    *,
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    require_exact_equality: bool = False,
    data_dir: str | Path | None = None,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
    head_and_source: Callable[[Path], tuple[str, str]],
) -> dict[str, Any]:
    """Evaluate the artifact through fixed-order identity, cell, and tail stages."""
    if policy.baseline_sha256 != baseline.sha256:
        raise ValueError("generalization policy and baseline identities differ")
    schema_version = artifact.get("schema_version")
    if schema_version == 2 and data_dir is None:
        raise ValueError("schema-v2 evaluation requires an explicit frozen data directory")
    state = _EvaluationState(
        artifact=artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=require_exact_equality,
        data_dir=data_dir,
        expected_config=expected_config,
        head_and_source=head_and_source,
        schema_version=schema_version,
        v2_projection_valid=schema_version == 2,
        expected_cell_fields=(
            schema.CELL_FIELDS_V2 if schema_version == 2 else schema.CELL_FIELDS_V1
        ),
    )
    _validate_artifact_identity(state)
    _validate_provenance(state)
    if artifact.get("aggregates") != baseline.aggregates:
        state.equality_differences.append("aggregate evidence")
    _collect_candidate_cells(state)
    _collect_reference_random_groups(state)
    _evaluate_common_cells(state)
    evaluate_random_tails(
        TailEvaluationContext(
            baseline=state.baseline,
            policy=state.policy,
            random_groups=state.random_groups,
            reference_random_groups=state.reference_random_groups,
            failures=state.failures,
            results=state.tail_results,
        )
    )
    return _finalize_evaluation(state)


def _validate_artifact_identity(state: _EvaluationState) -> None:
    artifact = state.artifact
    expected_fields = (
        schema.ARTIFACT_FIELDS_V2
        if state.schema_version == 2
        else schema.ARTIFACT_FIELDS_V1
    )
    schema_failures = schema.schema_failures(
        artifact, expected_fields, label="generalization candidate artifact"
    )
    state.failures.extend(schema_failures)
    state.equality_differences.extend(schema_failures)
    if schema_failures:
        state.v2_projection_valid = False
    if state.schema_version not in {1, 2}:
        state.failures.append("generalization candidate schema version is malformed")
        state.equality_differences.append("schema version")
    if state.schema_version == 2 and artifact.get(
        "attribution_definition"
    ) != schema.ATTRIBUTION_DEFINITION:
        state.failures.append("generalization candidate attribution definition is malformed")
        state.equality_differences.append("attribution definition")
    if artifact.get("gate") != "ai-era-generalization":
        state.failures.append("generalization candidate gate identity is malformed")
        state.equality_differences.append("gate identity")
    if not isinstance(artifact.get("passed"), bool):
        state.failures.append("generalization candidate passed state is malformed")
        state.equality_differences.append("passed state")
    advertised_failures = artifact.get("failures")
    if not isinstance(advertised_failures, list) or any(
        not isinstance(item, str) for item in advertised_failures
    ):
        state.failures.append("generalization candidate failure state is malformed")
        state.equality_differences.append("failure state")
    if not isinstance(artifact.get("concentration_definition"), Mapping):
        state.failures.append("generalization candidate concentration definition is malformed")
        state.equality_differences.append("concentration definition")
    if not isinstance(artifact.get("aggregates"), Mapping):
        state.failures.append("generalization candidate aggregates are malformed")
        state.equality_differences.append("aggregate schema")
    _capture_artifact_collections(state)


def _capture_artifact_collections(state: _EvaluationState) -> None:
    raw_cells_value = state.artifact.get("cells")
    provenance_value = state.artifact.get("provenance")
    if not isinstance(raw_cells_value, list):
        state.failures.append("generalization candidate cell collection is malformed")
        state.equality_differences.append("cell collection is malformed")
    else:
        state.raw_cells = raw_cells_value
    provenance_failures = schema.provenance_schema_failures(provenance_value)
    state.failures.extend(provenance_failures)
    state.equality_differences.extend(provenance_failures)
    state.equality_differences.extend(provenance_failures)
    if provenance_failures:
        state.v2_projection_valid = False
    if isinstance(provenance_value, Mapping):
        state.provenance = provenance_value


def _validate_provenance(state: _EvaluationState) -> None:
    _identify_config_migration(state)
    provenance_fields = (
        "effective_config_sha256",
        "data",
        "runtime",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
        "lookback_sessions",
    )
    mismatches = tuple(
        name
        for name in provenance_fields
        if state.provenance.get(name) != state.baseline.provenance.get(name)
        and not (
            name == "effective_config_sha256" and state.config_migration is not None
        )
    )
    state.failures.extend(
        f"candidate provenance differs from champion inputs: {name}" for name in mismatches
    )
    state.equality_differences.extend(f"provenance {name}" for name in mismatches)
    if mismatches:
        state.v2_projection_valid = False
    if state.schema_version == 2:
        _validate_v2_provenance(state)


def _identify_config_migration(state: _EvaluationState) -> None:
    if state.schema_version != 2 or not isinstance(state.expected_config, SystemConfig):
        return
    current_config_sha256 = config_fingerprint(state.expected_config)
    if state.provenance.get(
        "effective_config_sha256"
    ) != current_config_sha256 or current_config_sha256 == state.baseline.provenance.get(
        "effective_config_sha256"
    ):
        return
    try:
        candidate_migration = validate_governed_config_migration(state.expected_config)
    except (RuntimeError, ValueError):
        return
    if candidate_migration.champion_config_sha256 == state.baseline.provenance.get(
        "effective_config_sha256"
    ):
        state.config_migration = candidate_migration


def _validate_v2_provenance(state: _EvaluationState) -> None:
    if not isinstance(state.expected_config, SystemConfig):
        raise ValueError("schema-v2 evaluation requires a trusted effective config")
    state.trusted_config = state.expected_config
    current_config_sha256 = config_fingerprint(state.expected_config)
    if state.provenance.get("effective_config_sha256") != current_config_sha256:
        message = "candidate effective config differs from compiled production config"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False
    _validate_v2_source_binding(state)
    _validate_v2_data_binding(state)


def _validate_v2_source_binding(state: _EvaluationState) -> None:
    try:
        current_head, current_source = state.head_and_source(schema.ROOT)
    except RuntimeError as exc:
        message = f"candidate source binding cannot be verified: {exc}"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False
        return
    if state.provenance.get("head") != current_head or state.provenance.get(
        "source_sha256"
    ) != current_source:
        message = "candidate source binding differs from exact current HEAD"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False


def _validate_v2_data_binding(state: _EvaluationState) -> None:
    if state.data_dir is None:
        message = "candidate v2 replay validation requires an explicit frozen data directory"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False
        return
    data_binding = state.provenance.get("data")
    if not isinstance(data_binding, Mapping):
        message = "candidate frozen data binding is malformed"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False
        return
    try:
        state.market = VerifiedMarketData(state.data_dir, expected_manifest=data_binding)
    except (RuntimeError, ValueError) as exc:
        message = f"candidate frozen data binding cannot be verified: {exc}"
        state.failures.append(message)
        state.equality_differences.append(message)
        state.v2_projection_valid = False


def _collect_candidate_cells(state: _EvaluationState) -> None:
    for raw in state.raw_cells:
        if not isinstance(raw, Mapping):
            state.failures.append("candidate cell is malformed")
            state.equality_differences.append("malformed cell record")
            continue
        window = raw.get("window")
        scenario = raw.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario, str):
            state.failures.append("candidate cell identity is malformed")
            state.equality_differences.append("malformed cell identity")
            continue
        identifier = f"{window}/{scenario}"
        _validate_candidate_cell_schema(state, identifier, raw)
        if identifier in state.observed:
            state.failures.append(f"candidate contains duplicate cell: {identifier}")
            state.equality_differences.append(f"duplicate cell {identifier}")
        state.observed[identifier] = raw
    _validate_cell_coverage(state)


def _validate_candidate_cell_schema(
    state: _EvaluationState,
    identifier: str,
    raw: Mapping[str, Any],
) -> None:
    cell_failures = schema.schema_failures(
        raw, state.expected_cell_fields, label=f"candidate cell {identifier}"
    )
    evidence_failures = schema.schema_failures(
        raw.get("evidence"),
        schema.EVIDENCE_FIELDS,
        label=f"candidate cell evidence {identifier}",
    )
    if not cell_failures and not evidence_failures:
        return
    state.failures.extend(cell_failures)
    state.failures.extend(evidence_failures)
    state.equality_differences.extend(cell_failures)
    state.equality_differences.extend(evidence_failures)
    state.invalid_cells.add(identifier)
    if state.schema_version == 2:
        state.v2_projection_valid = False


def _validate_cell_coverage(state: _EvaluationState) -> None:
    missing = sorted(set(state.baseline.cells) - set(state.observed))
    unexpected = sorted(set(state.observed) - set(state.baseline.cells))
    if missing:
        state.failures.append(f"candidate missing baseline cells: {missing}")
        state.equality_differences.extend(
            f"missing cell {identifier}" for identifier in missing
        )
    if unexpected:
        state.failures.append(f"candidate has unexpected cells: {unexpected}")
        state.equality_differences.extend(
            f"unexpected cell {identifier}" for identifier in unexpected
        )


def _collect_reference_random_groups(state: _EvaluationState) -> None:
    for reference in state.baseline.cells.values():
        if reference.family == "random" and reference.pool_size is not None:
            state.reference_random_groups[(reference.window, reference.pool_size)].append(
                (
                    reference.identifier,
                    reference.metrics,
                    reference.replay_error is not None,
                )
            )


def _evaluate_common_cells(state: _EvaluationState) -> None:
    identifiers = sorted(set(state.baseline.cells) & set(state.observed))
    for identifier in identifiers:
        _evaluate_common_cell(state, identifier)


def _evaluate_common_cell(state: _EvaluationState, identifier: str) -> None:
    reference = state.baseline.cells[identifier]
    candidate = state.observed[identifier]
    if identifier in state.invalid_cells:
        return
    try:
        candidate_contract_sha256 = projection.candidate_contract_sha256(candidate)
    except ValueError as exc:
        state.failures.append(
            f"candidate cell contract is malformed: {identifier}: {exc}"
        )
        state.equality_differences.append(f"malformed cell contract {identifier}")
        return
    if candidate_contract_sha256 != reference.contract_sha256:
        state.failures.append(f"candidate cell contract differs from baseline: {identifier}")
        state.equality_differences.append(f"cell contract {identifier}")
        return
    _evaluate_replay_evidence(state, identifier, reference, candidate)


def _evaluate_replay_evidence(
    state: _EvaluationState,
    identifier: str,
    reference: Any,
    candidate: Mapping[str, Any],
) -> None:
    metrics = candidate.get("metrics")
    error_raw = candidate.get("replay_error")
    attribution_status = candidate.get("attribution_status")
    attribution = candidate.get("attribution")
    concentration = candidate.get("concentration")
    try:
        error = schema.replay_error(error_raw, identifier=identifier)
    except ValueError as exc:
        state.failures.append(f"candidate replay error is malformed: {identifier}: {exc}")
        state.equality_differences.append(f"malformed replay error {identifier}")
        return
    if not reference.economic:
        _evaluate_insufficient_sample(
            state, identifier, candidate, metrics, error, attribution_status, attribution, concentration
        )
        return
    if error is not None:
        _evaluate_replay_error(
            state, identifier, reference, candidate, metrics, error, attribution_status, attribution, concentration
        )
    elif not _evaluate_valid_economics(
        state, identifier, reference, candidate, metrics, attribution_status, attribution, concentration
    ):
        return
    if reference.family == "random" and reference.pool_size is not None:
        state.random_groups[(reference.window, reference.pool_size)].append(
            (identifier, metrics if isinstance(metrics, Mapping) else None, error is not None)
        )


def _evaluate_insufficient_sample(
    state: _EvaluationState,
    identifier: str,
    candidate: Mapping[str, Any],
    metrics: object,
    error: object,
    attribution_status: object,
    attribution: object,
    concentration: object,
) -> None:
    if metrics is not None or error is not None or candidate.get("raw") is not None:
        state.failures.append(
            f"candidate insufficient sample has economic evidence: {identifier}"
        )
        state.equality_differences.append(f"insufficient-sample evidence {identifier}")
    if state.schema_version == 2 and (
        attribution_status != "INSUFFICIENT_SAMPLE"
        or attribution is not None
        or concentration is not None
    ):
        state.failures.append(
            f"candidate insufficient sample attribution state differs: {identifier}"
        )
        state.equality_differences.append(
            f"insufficient-sample attribution {identifier}"
        )


def _evaluate_replay_error(
    state: _EvaluationState,
    identifier: str,
    reference: Any,
    candidate: Mapping[str, Any],
    metrics: object,
    error: Any,
    attribution_status: object,
    attribution: object,
    concentration: object,
) -> None:
    state.replay_errors += 1
    identical = bool(
        state.policy.identical_baseline_replay_error_passes
        and reference.replay_error == error
    )
    if not identical:
        state.failures.append(
            f"cell replay failed: {identifier}: {error.exception_type}: {error.message}"
        )
    if metrics is not None or candidate.get("raw") is not None:
        state.failures.append(
            f"candidate replay error contains fabricated metrics: {identifier}"
        )
        state.equality_differences.append(
            f"fabricated replay-error evidence {identifier}"
        )
    if state.schema_version == 2 and (
        attribution_status != "ERROR" or attribution is not None or concentration is not None
    ):
        state.failures.append(
            f"candidate replay error attribution state differs: {identifier}"
        )
        state.equality_differences.append(f"replay-error attribution {identifier}")
    if reference.replay_error != error:
        state.equality_differences.append(f"replay error {identifier}")
    if reference.metrics is not None:
        state.failures.append(
            f"candidate lacks finite metrics required by reference: {identifier}"
        )


def _evaluate_valid_economics(
    state: _EvaluationState,
    identifier: str,
    reference: Any,
    candidate: Mapping[str, Any],
    metrics: object,
    attribution_status: object,
    attribution: object,
    concentration: object,
) -> bool:
    try:
        candidate_metrics = schema.metric_payload(metrics, identifier=identifier)
    except ValueError as exc:
        state.failures.append(
            f"candidate economic metrics are malformed: {identifier}: {exc}"
        )
        state.equality_differences.append(f"malformed metrics {identifier}")
        return False
    candidate_raw = candidate.get("raw")
    if candidate_metrics is None or not isinstance(candidate_raw, Mapping):
        state.failures.append(f"candidate economic metrics are missing: {identifier}")
        state.equality_differences.append(f"economic evidence {identifier}")
        return False
    if state.schema_version == 2 and not _validate_economic_attribution(
        state,
        identifier,
        candidate,
        candidate_raw,
        attribution_status,
        attribution,
        concentration,
    ):
        return False
    try:
        reconciled_metrics = schema.metrics_reconciled_from_raw(
            candidate_raw, identifier=identifier
        )
    except ValueError as exc:
        state.failures.append(
            f"candidate raw economic evidence is malformed: {identifier}: {exc}"
        )
        state.equality_differences.append(f"malformed raw evidence {identifier}")
        return False
    if dict(candidate_metrics) != dict(reconciled_metrics):
        state.failures.append(
            f"candidate metrics do not reconcile to raw evidence: {identifier}"
        )
        state.equality_differences.append(f"raw evidence reconciliation {identifier}")
        return False
    _record_valid_economics(state, identifier, reference, candidate_metrics)
    return True


def _validate_economic_attribution(
    state: _EvaluationState,
    identifier: str,
    candidate: Mapping[str, Any],
    candidate_raw: Mapping[str, Any],
    attribution_status: object,
    attribution: object,
    concentration: object,
) -> bool:
    if attribution_status != "VALID" or not isinstance(attribution, Mapping):
        state.failures.append(f"candidate economic attribution is missing: {identifier}")
        state.equality_differences.append(f"economic attribution {identifier}")
        state.v2_projection_valid = False
        return False
    try:
        canonical_attribution = _validated_attribution(
            state, candidate, candidate_raw, attribution
        )
    except (TypeError, ValueError) as exc:
        state.failures.append(
            f"candidate economic attribution is malformed: {identifier}: {exc}"
        )
        state.equality_differences.append(f"malformed attribution {identifier}")
        state.v2_projection_valid = False
        return False
    if concentration != canonical_attribution["symbol_concentration"]:
        state.failures.append(
            f"candidate concentration differs from economic attribution: {identifier}"
        )
        state.equality_differences.append(f"detached concentration {identifier}")
        state.v2_projection_valid = False
        return False
    return True


def _validated_attribution(
    state: _EvaluationState,
    candidate: Mapping[str, Any],
    candidate_raw: Mapping[str, Any],
    attribution: Mapping[str, Any],
) -> dict[str, Any]:
    start = str(candidate.get("start"))
    end = str(candidate.get("end"))
    trusted_sessions = None if state.market is None else state.market.sessions(start, end)
    if state.market is not None:
        if state.trusted_config is None:
            raise ValueError("schema-v2 evaluation requires a trusted effective config")
        validate_engine_control_plane(
            candidate_raw,
            economic_start=start,
            economic_end=end,
            expected_sessions=trusted_sessions or (),
            expected_config=state.trusted_config,
            expected_code_sha256=code_fingerprint(),
            attribution=attribution,
        )
    return validate_attribution_against_engine_result(
        candidate_raw,
        economic_start=start,
        economic_end=end,
        attribution=attribution,
        trusted_sessions=trusted_sessions,
        trusted_close=None if state.market is None else state.market.close,
        require_daily_replay_evidence=True,
    )


def _record_valid_economics(
    state: _EvaluationState,
    identifier: str,
    reference: Any,
    candidate_metrics: Mapping[str, Any],
) -> None:
    state.economic_valid += 1
    if reference.metrics is not None:
        state.failures.extend(
            f"cell non-regression failed: {identifier}: {reason}"
            for reason in evaluate_relative_cell_non_regression(
                candidate_metrics, reference.metrics, policy=state.policy
            )
        )
        if dict(candidate_metrics) != dict(reference.metrics):
            state.equality_differences.append(f"metrics {identifier}")
    else:
        state.equality_differences.append(f"replay recovered {identifier}")
    _record_intrinsic_result(state, identifier, reference, candidate_metrics)


def _record_intrinsic_result(
    state: _EvaluationState,
    identifier: str,
    reference: Any,
    candidate_metrics: Mapping[str, Any],
) -> None:
    wealth = float(candidate_metrics["final_wealth"])
    drawdown = float(candidate_metrics["max_drawdown"])
    reference_wealth = (
        wealth if reference.metrics is None else float(reference.metrics["final_wealth"])
    )
    reference_drawdown = (
        drawdown if reference.metrics is None else float(reference.metrics["max_drawdown"])
    )
    reasons: list[str] = []
    if reference.family in {"remove_all_core", "tradable_no_optical"}:
        _evaluate_directional_intrinsic(
            state, wealth, drawdown, reference_wealth, reference_drawdown, reasons
        )
    elif reference.family == "remove_one":
        _evaluate_remove_one_intrinsic(
            state, wealth, drawdown, reference_wealth, reference_drawdown, reasons
        )
    if reference.family not in {"remove_all_core", "tradable_no_optical", "remove_one"}:
        return
    state.intrinsic_results.append(
        {
            "identifier": identifier,
            "family": reference.family,
            "final_wealth": wealth,
            "max_drawdown": drawdown,
            "passed": not reasons,
            "failures": reasons,
        }
    )
    state.failures.extend(
        f"intrinsic directional failed: {identifier}: {reason}" for reason in reasons
    )


def _evaluate_directional_intrinsic(
    state: _EvaluationState,
    wealth: float,
    drawdown: float,
    reference_wealth: float,
    reference_drawdown: float,
    reasons: list[str],
) -> None:
    wealth_failed, wealth_floor = violates_effective_floor(
        wealth,
        literal=state.policy.directional_final_wealth_strict_min,
        baseline=reference_wealth,
        strict=True,
    )
    drawdown_ceiling = max(state.policy.directional_max_drawdown, reference_drawdown)
    if wealth_failed:
        reasons.append(f"final_wealth {wealth:g} violates effective minimum {wealth_floor:g}")
    if drawdown > drawdown_ceiling:
        reasons.append(
            f"max_drawdown {drawdown:g} exceeds effective maximum {drawdown_ceiling:g}"
        )


def _evaluate_remove_one_intrinsic(
    state: _EvaluationState,
    wealth: float,
    drawdown: float,
    reference_wealth: float,
    reference_drawdown: float,
    reasons: list[str],
) -> None:
    wealth_failed, wealth_floor = violates_effective_floor(
        wealth,
        literal=state.policy.remove_one_final_wealth_min,
        baseline=reference_wealth,
    )
    drawdown_ceiling = max(state.policy.remove_one_max_drawdown, reference_drawdown)
    if wealth_failed:
        reasons.append(f"final_wealth {wealth:g} is below effective minimum {wealth_floor:g}")
    if drawdown > drawdown_ceiling:
        reasons.append(
            f"max_drawdown {drawdown:g} exceeds effective maximum {drawdown_ceiling:g}"
        )


def _finalize_evaluation(state: _EvaluationState) -> dict[str, Any]:
    expected_economic = sum(cell.economic for cell in state.baseline.cells.values())
    if state.economic_valid + state.replay_errors != expected_economic:
        state.failures.append(
            "candidate economic coverage is incomplete: "
            f"expected {expected_economic}, valid {state.economic_valid}, errors {state.replay_errors}"
        )
        state.equality_differences.append("economic coverage")
    _finalize_equality(state)
    exact_equality_passed = not state.equality_differences
    if state.require_exact_equality:
        state.failures.extend(
            f"exact equality differs: {reason}" for reason in state.equality_differences
        )
    champion_accepted = bool(
        state.policy.champion_equality_passes and exact_equality_passed
    )
    return _evaluation_report(
        state, expected_economic, exact_equality_passed, champion_accepted
    )


def _finalize_equality(state: _EvaluationState) -> None:
    if state.schema_version == 2 and not state.v2_projection_valid:
        state.equality_differences.append("validated v2 control-plane evidence")
        return
    try:
        artifact_equality_sha256 = (
            projection.attribution_neutral_equality_sha256(
                state.artifact, config_migration=state.config_migration
            )
            if state.schema_version == 2
            else schema.artifact_equality_sha256(state.artifact)
        )
    except ValueError as exc:
        state.failures.append(f"generalization candidate evidence is malformed: {exc}")
        state.equality_differences.append("malformed artifact evidence")
        return
    expected_sha256 = (
        state.baseline.attribution_neutral_equality_sha256
        if state.schema_version == 2
        else state.baseline.artifact_equality_sha256
    )
    if artifact_equality_sha256 != expected_sha256:
        state.equality_differences.append("artifact evidence payload")


def _evaluation_report(
    state: _EvaluationState,
    expected_economic: int,
    exact_equality_passed: bool,
    champion_accepted: bool,
) -> dict[str, Any]:
    migration = state.config_migration
    return {
        "passed": not state.failures,
        "exact_equality_required": state.require_exact_equality,
        "exact_equality_passed": exact_equality_passed,
        "champion_equality_accepted": champion_accepted,
        "config_migration": (
            None
            if migration is None
            else {
                "champion_config_sha256": migration.champion_config_sha256,
                "candidate_config_sha256": migration.candidate_config_sha256,
                "removed_fields": list(migration.removed_fields),
                "governance_sha256": migration.governance_sha256,
                "carrier_sha256": migration.carrier_sha256,
            }
        ),
        "economic_cells_expected": expected_economic,
        "economic_cells_valid": state.economic_valid,
        "replay_error_cells": state.replay_errors,
        "intrinsic_results": state.intrinsic_results,
        "random_tail_results": state.tail_results,
        "failures": state.failures,
    }
