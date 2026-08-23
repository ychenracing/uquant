"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from ..generalization_matrix import _head_and_source
from ..replay_evidence import VerifiedMarketData
from .projection import (
    _attribution_neutral_equality_sha256,
    _candidate_contract_sha256,
)
from .schema import (
    _ARTIFACT_FIELDS_V1,
    _ARTIFACT_FIELDS_V2,
    _ATTRIBUTION_DEFINITION,
    _CELL_FIELDS_V1,
    _CELL_FIELDS_V2,
    _EVIDENCE_FIELDS,
    _ROOT,
    GeneralizationBaseline,
    GeneralizationPolicy,
    _artifact_equality_sha256,
    _metric_payload,
    _metrics_reconciled_from_raw,
    _provenance_schema_failures,
    _replay_error,
    _schema_failures,
)


def _compatibility_head_and_source(root: Path) -> tuple[str, str]:
    facade = sys.modules.get("uquant.validation.generalization_reference")
    seam = getattr(facade, "_head_and_source", _head_and_source)
    return seam(root)

def evaluate_cell_non_regression(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Apply the frozen relative per-cell wealth, risk, order, and turnover gates."""
    failures: list[str] = []
    candidate_wealth = float(candidate["final_wealth"])
    reference_wealth = float(reference["final_wealth"])
    wealth_limit = reference_wealth * policy.wealth_ratio_min
    if candidate_wealth < wealth_limit:
        failures.append(
            f"final_wealth {candidate_wealth} is below 95% reference {wealth_limit:g}"
        )
    candidate_drawdown = float(candidate["max_drawdown"])
    drawdown_limit = float(reference["max_drawdown"]) + policy.drawdown_absolute_buffer
    if candidate_drawdown > drawdown_limit:
        failures.append(
            f"max_drawdown {candidate_drawdown} exceeds reference-plus-buffer {drawdown_limit:g}"
        )
    candidate_orders = int(candidate["account_orders"])
    reference_orders = int(reference["account_orders"])
    order_limit = max(
        reference_orders + policy.orders_absolute_buffer,
        math.ceil(reference_orders * policy.orders_ratio_max),
    )
    if candidate_orders > order_limit:
        failures.append(
            f"account_orders {candidate_orders} exceeds reference activity limit {order_limit}"
        )
    for name in ("gross_turnover", "annual_turnover"):
        candidate_turnover = float(candidate[name])
        reference_turnover = float(reference[name])
        if reference_turnover == 0.0:
            if candidate_turnover != 0.0:
                failures.append(
                    f"{name} {candidate_turnover} must remain zero because reference is zero"
                )
        else:
            turnover_limit = reference_turnover * policy.turnover_ratio_max
            if candidate_turnover > turnover_limit:
                failures.append(
                    f"{name} {candidate_turnover} exceeds 110% reference {turnover_limit:g}"
                )
    return tuple(failures)


def _evaluate_recovered_against_group_envelope(
    candidate: Mapping[str, Any],
    authenticated_valid_group: Sequence[Mapping[str, Any]],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Bound one recovered replay by the worst authenticated valid peer metrics."""

    if not authenticated_valid_group:
        return ("authenticated random group has no valid recovery envelope",)
    envelope = {
        "final_wealth": min(float(item["final_wealth"]) for item in authenticated_valid_group),
        "max_drawdown": max(float(item["max_drawdown"]) for item in authenticated_valid_group),
        "account_orders": max(int(item["account_orders"]) for item in authenticated_valid_group),
        "gross_turnover": max(float(item["gross_turnover"]) for item in authenticated_valid_group),
        "annual_turnover": max(float(item["annual_turnover"]) for item in authenticated_valid_group),
    }
    return evaluate_cell_non_regression(candidate, envelope, policy=policy)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("generalization tail quantile requires valid economic cells")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


@dataclass(frozen=True, slots=True)
class _RandomTailStatistics:
    """Authenticated tail statistics for one fixed window/pool-size group."""

    valid_cells: int
    replay_error_cells: int
    positive_return_fraction: float
    p10_wealth: float | None
    p90_drawdown: float | None
    p90_orders: float | None


def _random_tail_statistics(
    group: Sequence[tuple[str, Mapping[str, Any] | None, bool]],
    *,
    requested: int,
) -> _RandomTailStatistics:
    valid = [metrics for _, metrics, has_error in group if metrics is not None and not has_error]
    wealth_values = [float(item["final_wealth"]) for item in valid]
    drawdown_values = [float(item["max_drawdown"]) for item in valid]
    order_values = [float(item["account_orders"]) for item in valid]
    return _RandomTailStatistics(
        valid_cells=len(valid),
        replay_error_cells=sum(has_error for _, _, has_error in group),
        positive_return_fraction=(
            sum(value > 1.0 for value in wealth_values) / requested
        ),
        p10_wealth=_quantile(wealth_values, 0.10) if wealth_values else None,
        p90_drawdown=_quantile(drawdown_values, 0.90) if drawdown_values else None,
        p90_orders=_quantile(order_values, 0.90) if order_values else None,
    )


def _violates_effective_floor(
    value: float,
    *,
    literal: float,
    baseline: float,
    strict: bool = False,
) -> tuple[bool, float]:
    """Keep the literal floor unless the authenticated champion is lower."""

    effective = min(literal, baseline)
    if strict and baseline > literal:
        return value <= effective, effective
    return value < effective, effective

def evaluate_generalization_policy_artifact(
    artifact: Mapping[str, Any],
    *,
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    require_exact_equality: bool = False,
    data_dir: str | Path | None = None,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Recompute frozen relative, intrinsic, and random-tail results from raw cells."""
    if policy.baseline_sha256 != baseline.sha256:
        raise ValueError("generalization policy and baseline identities differ")
    failures: list[str] = []
    equality_differences: list[str] = []
    schema_version = artifact.get("schema_version")
    if schema_version == 2 and data_dir is None:
        raise ValueError(
            "schema-v2 evaluation requires an explicit frozen data directory"
        )
    v2_projection_valid = schema_version == 2
    expected_artifact_fields = (
        _ARTIFACT_FIELDS_V2 if schema_version == 2 else _ARTIFACT_FIELDS_V1
    )
    expected_cell_fields = _CELL_FIELDS_V2 if schema_version == 2 else _CELL_FIELDS_V1
    artifact_schema_failures = _schema_failures(
        artifact, expected_artifact_fields, label="generalization candidate artifact"
    )
    failures.extend(artifact_schema_failures)
    equality_differences.extend(artifact_schema_failures)
    if artifact_schema_failures:
        v2_projection_valid = False
    if schema_version not in {1, 2}:
        failures.append("generalization candidate schema version is malformed")
        equality_differences.append("schema version")
    if schema_version == 2 and artifact.get("attribution_definition") != _ATTRIBUTION_DEFINITION:
        failures.append("generalization candidate attribution definition is malformed")
        equality_differences.append("attribution definition")
    if artifact.get("gate") != "ai-era-generalization":
        failures.append("generalization candidate gate identity is malformed")
        equality_differences.append("gate identity")
    if not isinstance(artifact.get("passed"), bool):
        failures.append("generalization candidate passed state is malformed")
        equality_differences.append("passed state")
    advertised_failures = artifact.get("failures")
    if not isinstance(advertised_failures, list) or any(
        not isinstance(item, str) for item in advertised_failures
    ):
        failures.append("generalization candidate failure state is malformed")
        equality_differences.append("failure state")
    if not isinstance(artifact.get("concentration_definition"), Mapping):
        failures.append("generalization candidate concentration definition is malformed")
        equality_differences.append("concentration definition")
    if not isinstance(artifact.get("aggregates"), Mapping):
        failures.append("generalization candidate aggregates are malformed")
        equality_differences.append("aggregate schema")
    raw_cells_value = artifact.get("cells")
    provenance_value = artifact.get("provenance")
    if not isinstance(raw_cells_value, list):
        failures.append("generalization candidate cell collection is malformed")
        equality_differences.append("cell collection is malformed")
        raw_cells: list[Any] = []
    else:
        raw_cells = raw_cells_value
    provenance_schema_failures = _provenance_schema_failures(provenance_value)
    failures.extend(provenance_schema_failures)
    equality_differences.extend(provenance_schema_failures)
    if provenance_schema_failures:
        v2_projection_valid = False
    if not isinstance(provenance_value, Mapping):
        provenance: Mapping[str, Any] = {}
    else:
        provenance = provenance_value
    config_migration: GovernedConfigMigration | None = None
    if schema_version == 2 and isinstance(expected_config, SystemConfig):
        current_config_sha256 = config_fingerprint(expected_config)
        if (
            provenance.get("effective_config_sha256") == current_config_sha256
            and current_config_sha256 != baseline.provenance.get("effective_config_sha256")
        ):
            try:
                candidate_migration = validate_governed_config_migration(expected_config)
            except (RuntimeError, ValueError):
                pass
            else:
                if (
                    candidate_migration.champion_config_sha256
                    == baseline.provenance.get("effective_config_sha256")
                ):
                    config_migration = candidate_migration
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
    provenance_mismatches = tuple(
        name
        for name in provenance_fields
        if provenance.get(name) != baseline.provenance.get(name)
        and not (name == "effective_config_sha256" and config_migration is not None)
    )
    failures.extend(
        f"candidate provenance differs from champion inputs: {name}"
        for name in provenance_mismatches
    )
    equality_differences.extend(f"provenance {name}" for name in provenance_mismatches)
    if provenance_mismatches:
        v2_projection_valid = False
    market: VerifiedMarketData | None = None
    trusted_config: SystemConfig | None = None
    if schema_version == 2:
        if not isinstance(expected_config, SystemConfig):
            raise ValueError("schema-v2 evaluation requires a trusted effective config")
        trusted_config = expected_config
        current_config_sha256 = config_fingerprint(expected_config)
        if provenance.get("effective_config_sha256") != current_config_sha256:
            message = "candidate effective config differs from compiled production config"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        try:
            current_head, current_source = _compatibility_head_and_source(_ROOT)
        except RuntimeError as exc:
            message = f"candidate source binding cannot be verified: {exc}"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        else:
            if (
                provenance.get("head") != current_head
                or provenance.get("source_sha256") != current_source
            ):
                message = "candidate source binding differs from exact current HEAD"
                failures.append(message)
                equality_differences.append(message)
                v2_projection_valid = False
        if data_dir is None:
            message = "candidate v2 replay validation requires an explicit frozen data directory"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        else:
            data_binding = provenance.get("data")
            if not isinstance(data_binding, Mapping):
                message = "candidate frozen data binding is malformed"
                failures.append(message)
                equality_differences.append(message)
                v2_projection_valid = False
            else:
                try:
                    market = VerifiedMarketData(
                        data_dir,
                        expected_manifest=data_binding,
                    )
                except (RuntimeError, ValueError) as exc:
                    message = f"candidate frozen data binding cannot be verified: {exc}"
                    failures.append(message)
                    equality_differences.append(message)
                    v2_projection_valid = False
    if artifact.get("aggregates") != baseline.aggregates:
        equality_differences.append("aggregate evidence")
    observed: dict[str, Mapping[str, Any]] = {}
    invalid_cells: set[str] = set()
    for raw in raw_cells:
        if not isinstance(raw, Mapping):
            failures.append("candidate cell is malformed")
            equality_differences.append("malformed cell record")
            continue
        window = raw.get("window")
        scenario = raw.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario, str):
            failures.append("candidate cell identity is malformed")
            equality_differences.append("malformed cell identity")
            continue
        identifier = f"{window}/{scenario}"
        cell_schema_failures = _schema_failures(
            raw, expected_cell_fields, label=f"candidate cell {identifier}"
        )
        evidence_schema_failures = _schema_failures(
            raw.get("evidence"),
            _EVIDENCE_FIELDS,
            label=f"candidate cell evidence {identifier}",
        )
        if cell_schema_failures or evidence_schema_failures:
            failures.extend(cell_schema_failures)
            failures.extend(evidence_schema_failures)
            equality_differences.extend(cell_schema_failures)
            equality_differences.extend(evidence_schema_failures)
            invalid_cells.add(identifier)
            if schema_version == 2:
                v2_projection_valid = False
        if identifier in observed:
            failures.append(f"candidate contains duplicate cell: {identifier}")
            equality_differences.append(f"duplicate cell {identifier}")
        observed[identifier] = raw
    missing = sorted(set(baseline.cells) - set(observed))
    unexpected = sorted(set(observed) - set(baseline.cells))
    if missing:
        failures.append(f"candidate missing baseline cells: {missing}")
        equality_differences.extend(f"missing cell {identifier}" for identifier in missing)
    if unexpected:
        failures.append(f"candidate has unexpected cells: {unexpected}")
        equality_differences.extend(
            f"unexpected cell {identifier}" for identifier in unexpected
        )

    economic_valid = 0
    replay_errors = 0
    intrinsic_results: list[dict[str, Any]] = []
    random_groups: dict[tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]] = (
        defaultdict(list)
    )
    reference_random_groups: dict[
        tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]
    ] = defaultdict(list)
    for reference in baseline.cells.values():
        if reference.family == "random" and reference.pool_size is not None:
            reference_random_groups[(reference.window, reference.pool_size)].append(
                (
                    reference.identifier,
                    reference.metrics,
                    reference.replay_error is not None,
                )
            )
    for identifier in sorted(set(baseline.cells) & set(observed)):
        reference = baseline.cells[identifier]
        candidate = observed[identifier]
        if identifier in invalid_cells:
            continue
        try:
            candidate_contract_sha256 = _candidate_contract_sha256(candidate)
        except ValueError as exc:
            failures.append(f"candidate cell contract is malformed: {identifier}: {exc}")
            equality_differences.append(f"malformed cell contract {identifier}")
            continue
        if candidate_contract_sha256 != reference.contract_sha256:
            failures.append(f"candidate cell contract differs from baseline: {identifier}")
            equality_differences.append(f"cell contract {identifier}")
            continue
        metrics = candidate.get("metrics")
        error_raw = candidate.get("replay_error")
        attribution_status = candidate.get("attribution_status")
        attribution = candidate.get("attribution")
        concentration = candidate.get("concentration")
        try:
            error = _replay_error(error_raw, identifier=identifier)
        except ValueError as exc:
            failures.append(f"candidate replay error is malformed: {identifier}: {exc}")
            equality_differences.append(f"malformed replay error {identifier}")
            continue
        if not reference.economic:
            if metrics is not None or error is not None or candidate.get("raw") is not None:
                failures.append(f"candidate insufficient sample has economic evidence: {identifier}")
                equality_differences.append(f"insufficient-sample evidence {identifier}")
            if schema_version == 2 and (
                attribution_status != "INSUFFICIENT_SAMPLE"
                or attribution is not None
                or concentration is not None
            ):
                failures.append(f"candidate insufficient sample attribution state differs: {identifier}")
                equality_differences.append(f"insufficient-sample attribution {identifier}")
            continue
        if error is not None:
            replay_errors += 1
            identical_baseline_error = bool(
                policy.identical_baseline_replay_error_passes
                and reference.replay_error == error
            )
            if not identical_baseline_error:
                failures.append(
                    f"cell replay failed: {identifier}: {error.exception_type}: {error.message}"
                )
            if metrics is not None or candidate.get("raw") is not None:
                failures.append(f"candidate replay error contains fabricated metrics: {identifier}")
                equality_differences.append(f"fabricated replay-error evidence {identifier}")
            if schema_version == 2 and (
                attribution_status != "ERROR"
                or attribution is not None
                or concentration is not None
            ):
                failures.append(f"candidate replay error attribution state differs: {identifier}")
                equality_differences.append(f"replay-error attribution {identifier}")
            if reference.replay_error != error:
                equality_differences.append(f"replay error {identifier}")
            if reference.metrics is not None:
                failures.append(f"candidate lacks finite metrics required by reference: {identifier}")
        else:
            try:
                candidate_metrics = _metric_payload(metrics, identifier=identifier)
            except ValueError as exc:
                failures.append(f"candidate economic metrics are malformed: {identifier}: {exc}")
                equality_differences.append(f"malformed metrics {identifier}")
                continue
            candidate_raw = candidate.get("raw")
            if candidate_metrics is None or not isinstance(candidate_raw, Mapping):
                failures.append(f"candidate economic metrics are missing: {identifier}")
                equality_differences.append(f"economic evidence {identifier}")
                continue
            if schema_version == 2:
                if attribution_status != "VALID" or not isinstance(attribution, Mapping):
                    failures.append(f"candidate economic attribution is missing: {identifier}")
                    equality_differences.append(f"economic attribution {identifier}")
                    v2_projection_valid = False
                    continue
                try:
                    start = str(candidate.get("start"))
                    end = str(candidate.get("end"))
                    trusted_sessions = (
                        None if market is None else market.sessions(start, end)
                    )
                    if market is not None:
                        if trusted_config is None:
                            raise ValueError(
                                "schema-v2 evaluation requires a trusted effective config"
                            )
                        validate_engine_control_plane(
                            candidate_raw,
                            economic_start=start,
                            economic_end=end,
                            expected_sessions=trusted_sessions or (),
                            expected_config=trusted_config,
                            expected_code_sha256=code_fingerprint(),
                            attribution=attribution,
                        )
                    canonical_attribution = validate_attribution_against_engine_result(
                        candidate_raw,
                        economic_start=start,
                        economic_end=end,
                        attribution=attribution,
                        trusted_sessions=trusted_sessions,
                        trusted_close=None if market is None else market.close,
                        require_daily_replay_evidence=True,
                    )
                except (TypeError, ValueError) as exc:
                    failures.append(
                        f"candidate economic attribution is malformed: {identifier}: {exc}"
                    )
                    equality_differences.append(f"malformed attribution {identifier}")
                    v2_projection_valid = False
                    continue
                if concentration != canonical_attribution["symbol_concentration"]:
                    failures.append(
                        f"candidate concentration differs from economic attribution: {identifier}"
                    )
                    equality_differences.append(f"detached concentration {identifier}")
                    v2_projection_valid = False
                    continue
            try:
                reconciled_metrics = _metrics_reconciled_from_raw(
                    candidate_raw, identifier=identifier
                )
            except ValueError as exc:
                failures.append(f"candidate raw economic evidence is malformed: {identifier}: {exc}")
                equality_differences.append(f"malformed raw evidence {identifier}")
                continue
            if dict(candidate_metrics) != dict(reconciled_metrics):
                failures.append(f"candidate metrics do not reconcile to raw evidence: {identifier}")
                equality_differences.append(f"raw evidence reconciliation {identifier}")
                continue
            economic_valid += 1
            if reference.metrics is not None:
                failures.extend(
                    f"cell non-regression failed: {identifier}: {reason}"
                    for reason in evaluate_cell_non_regression(
                        candidate_metrics, reference.metrics, policy=policy
                    )
                )
                if dict(candidate_metrics) != dict(reference.metrics):
                    equality_differences.append(f"metrics {identifier}")
            else:
                equality_differences.append(f"replay recovered {identifier}")
            wealth = float(candidate_metrics["final_wealth"])
            drawdown = float(candidate_metrics["max_drawdown"])
            reference_wealth = (
                wealth
                if reference.metrics is None
                else float(reference.metrics["final_wealth"])
            )
            reference_drawdown = (
                drawdown
                if reference.metrics is None
                else float(reference.metrics["max_drawdown"])
            )
            intrinsic_reasons: list[str] = []
            if reference.family in {"remove_all_core", "tradable_no_optical"}:
                wealth_failed, wealth_floor = _violates_effective_floor(
                    wealth,
                    literal=policy.directional_final_wealth_strict_min,
                    baseline=reference_wealth,
                    strict=True,
                )
                drawdown_ceiling = max(
                    policy.directional_max_drawdown,
                    reference_drawdown,
                )
                if wealth_failed:
                    intrinsic_reasons.append(
                        f"final_wealth {wealth:g} violates effective minimum {wealth_floor:g}"
                    )
                if drawdown > drawdown_ceiling:
                    intrinsic_reasons.append(
                        f"max_drawdown {drawdown:g} exceeds effective maximum "
                        f"{drawdown_ceiling:g}"
                    )
            elif reference.family == "remove_one":
                wealth_failed, wealth_floor = _violates_effective_floor(
                    wealth,
                    literal=policy.remove_one_final_wealth_min,
                    baseline=reference_wealth,
                )
                drawdown_ceiling = max(
                    policy.remove_one_max_drawdown,
                    reference_drawdown,
                )
                if wealth_failed:
                    intrinsic_reasons.append(
                        f"final_wealth {wealth:g} is below effective minimum {wealth_floor:g}"
                    )
                if drawdown > drawdown_ceiling:
                    intrinsic_reasons.append(
                        f"max_drawdown {drawdown:g} exceeds effective maximum "
                        f"{drawdown_ceiling:g}"
                    )
            if reference.family in {"remove_all_core", "tradable_no_optical", "remove_one"}:
                intrinsic_results.append(
                    {
                        "identifier": identifier,
                        "family": reference.family,
                        "final_wealth": wealth,
                        "max_drawdown": drawdown,
                        "passed": not intrinsic_reasons,
                        "failures": intrinsic_reasons,
                    }
                )
                failures.extend(
                    f"intrinsic directional failed: {identifier}: {reason}"
                    for reason in intrinsic_reasons
                )
        if reference.family == "random" and reference.pool_size is not None:
            random_groups[(reference.window, reference.pool_size)].append(
                (identifier, metrics if isinstance(metrics, Mapping) else None, error is not None)
            )

    tail_results: list[dict[str, Any]] = []
    for (window, pool_size), group in sorted(random_groups.items()):
        requested = policy.requested_seeds_per_group
        if len(group) != requested:
            failures.append(
                f"random tail coverage failed: {window}/size-{pool_size}: "
                f"requested {requested}, observed {len(group)}"
            )
        candidate_tail = _random_tail_statistics(group, requested=requested)
        reference_group = reference_random_groups[(window, pool_size)]
        baseline_tail = _random_tail_statistics(
            reference_group,
            requested=requested,
        )
        authenticated_valid_metrics = [
            metrics
            for _, metrics, has_error in reference_group
            if metrics is not None and not has_error
        ]
        literal_fallback = not authenticated_valid_metrics
        comparison_group = (
            group
            if literal_fallback
            else [item for item in group if baseline.cells[item[0]].metrics is not None]
        )
        comparison_tail = _random_tail_statistics(
            comparison_group,
            requested=requested,
        )
        replay_error_ceiling = (
            0 if literal_fallback else baseline_tail.replay_error_cells
        )
        positive_floor = (
            policy.positive_return_fraction_min
            if literal_fallback
            else min(
                policy.positive_return_fraction_min,
                baseline_tail.positive_return_fraction,
            )
        )
        p10_floor = (
            policy.p10_wealth_min
            if baseline_tail.p10_wealth is None
            else min(policy.p10_wealth_min, baseline_tail.p10_wealth)
        )
        drawdown_ceiling = (
            policy.p90_drawdown_max
            if baseline_tail.p90_drawdown is None
            else max(policy.p90_drawdown_max, baseline_tail.p90_drawdown)
        )
        orders_ceiling = (
            policy.p90_orders_max
            if baseline_tail.p90_orders is None
            else max(policy.p90_orders_max, baseline_tail.p90_orders)
        )
        literal_reasons: list[str] = []
        if candidate_tail.replay_error_cells:
            literal_reasons.append(
                f"{candidate_tail.replay_error_cells} replay error cells"
            )
        if candidate_tail.positive_return_fraction < policy.positive_return_fraction_min:
            literal_reasons.append(
                f"positive-return fraction {candidate_tail.positive_return_fraction:g} "
                "is below 0.6"
            )
        if (
            candidate_tail.p10_wealth is None
            or candidate_tail.p10_wealth < policy.p10_wealth_min
        ):
            literal_reasons.append(
                f"p10 wealth {candidate_tail.p10_wealth} is below 0.8"
            )
        if (
            candidate_tail.p90_drawdown is None
            or candidate_tail.p90_drawdown > policy.p90_drawdown_max
        ):
            literal_reasons.append(
                f"p90 drawdown {candidate_tail.p90_drawdown} exceeds 0.3"
            )
        if (
            candidate_tail.p90_orders is None
            or candidate_tail.p90_orders > policy.p90_orders_max
        ):
            literal_reasons.append(
                f"p90 orders {candidate_tail.p90_orders} exceeds 20"
            )
        reasons: list[str] = []
        if candidate_tail.replay_error_cells > replay_error_ceiling:
            reasons.append(
                f"replay error cells {candidate_tail.replay_error_cells} exceed "
                f"effective maximum {replay_error_ceiling}"
            )
        if comparison_tail.positive_return_fraction < positive_floor:
            reasons.append(
                f"positive-return fraction {comparison_tail.positive_return_fraction:g} "
                f"is below effective minimum {positive_floor:g}"
            )
        if comparison_tail.p10_wealth is None or comparison_tail.p10_wealth < p10_floor:
            reasons.append(
                f"p10 wealth {comparison_tail.p10_wealth} is below effective minimum "
                f"{p10_floor:g}"
            )
        if (
            comparison_tail.p90_drawdown is None
            or comparison_tail.p90_drawdown > drawdown_ceiling
        ):
            reasons.append(
                f"p90 drawdown {comparison_tail.p90_drawdown} exceeds effective maximum "
                f"{drawdown_ceiling:g}"
            )
        if comparison_tail.p90_orders is None or comparison_tail.p90_orders > orders_ceiling:
            reasons.append(
                f"p90 orders {comparison_tail.p90_orders} exceeds effective maximum "
                f"{orders_ceiling:g}"
            )
        for identifier, metrics, has_error in group:
            if (
                literal_fallback
                or baseline.cells[identifier].replay_error is None
                or metrics is None
                or has_error
            ):
                continue
            reasons.extend(
                f"recovered cell {identifier} exceeds authenticated group envelope: {reason}"
                for reason in _evaluate_recovered_against_group_envelope(
                    metrics,
                    authenticated_valid_metrics,
                    policy=policy,
                )
            )
        tail_results.append(
            {
                "window": window,
                "pool_size": pool_size,
                "requested_cells": requested,
                "valid_cells": candidate_tail.valid_cells,
                "replay_error_cells": candidate_tail.replay_error_cells,
                "authenticated_support_cells": len(authenticated_valid_metrics),
                "literal_fallback": literal_fallback,
                "positive_return_fraction": candidate_tail.positive_return_fraction,
                "p10_wealth": candidate_tail.p10_wealth,
                "p90_drawdown": candidate_tail.p90_drawdown,
                "p90_orders": candidate_tail.p90_orders,
                "non_regression_tail": {
                    "valid_cells": comparison_tail.valid_cells,
                    "positive_return_fraction": comparison_tail.positive_return_fraction,
                    "p10_wealth": comparison_tail.p10_wealth,
                    "p90_drawdown": comparison_tail.p90_drawdown,
                    "p90_orders": comparison_tail.p90_orders,
                },
                "effective_bounds": {
                    "replay_error_cells_max": replay_error_ceiling,
                    "positive_return_fraction_min": positive_floor,
                    "p10_wealth_min": p10_floor,
                    "p90_drawdown_max": drawdown_ceiling,
                    "p90_orders_max": orders_ceiling,
                },
                "literal_passed": not literal_reasons,
                "literal_failures": literal_reasons,
                "non_regression_passed": not reasons,
                "grandfathered": bool(literal_reasons and not reasons),
                "passed": not reasons,
                "failures": reasons,
            }
        )
        failures.extend(
            f"random tail failed: {window}/size-{pool_size}: {reason}" for reason in reasons
        )

    expected_economic = sum(cell.economic for cell in baseline.cells.values())
    if economic_valid + replay_errors != expected_economic:
        failures.append(
            "candidate economic coverage is incomplete: "
            f"expected {expected_economic}, valid {economic_valid}, errors {replay_errors}"
        )
        equality_differences.append("economic coverage")
    if schema_version == 2 and not v2_projection_valid:
        equality_differences.append("validated v2 control-plane evidence")
    else:
        try:
            artifact_equality_sha256 = (
                _attribution_neutral_equality_sha256(
                    artifact,
                    config_migration=config_migration,
                )
                if schema_version == 2
                else _artifact_equality_sha256(artifact)
            )
        except ValueError as exc:
            failures.append(f"generalization candidate evidence is malformed: {exc}")
            equality_differences.append("malformed artifact evidence")
        else:
            expected_equality_sha256 = (
                baseline.attribution_neutral_equality_sha256
                if schema_version == 2
                else baseline.artifact_equality_sha256
            )
            if artifact_equality_sha256 != expected_equality_sha256:
                equality_differences.append("artifact evidence payload")
    exact_equality_passed = not equality_differences
    if require_exact_equality:
        failures.extend(
            f"exact equality differs: {reason}" for reason in equality_differences
        )
    champion_equality_accepted = bool(
        policy.champion_equality_passes and exact_equality_passed
    )
    return {
        "passed": not failures,
        "exact_equality_required": require_exact_equality,
        "exact_equality_passed": exact_equality_passed,
        "champion_equality_accepted": champion_equality_accepted,
        "config_migration": (
            None
            if config_migration is None
            else {
                "champion_config_sha256": config_migration.champion_config_sha256,
                "candidate_config_sha256": config_migration.candidate_config_sha256,
                "removed_fields": list(config_migration.removed_fields),
                "governance_sha256": config_migration.governance_sha256,
                "carrier_sha256": config_migration.carrier_sha256,
            }
        ),
        "economic_cells_expected": expected_economic,
        "economic_cells_valid": economic_valid,
        "replay_error_cells": replay_errors,
        "intrinsic_results": intrinsic_results,
        "random_tail_results": tail_results,
        "failures": failures,
    }
