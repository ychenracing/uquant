"""Compatibility facade for the frozen generalization reference policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..config import DEFAULT_CONFIG, SystemConfig
from .generalization_matrix import head_and_source as _head_and_source
from .generalization_policy import cells as _cells
from .generalization_policy import evaluator as _evaluator
from .generalization_policy import projection as _projection
from .generalization_policy import schema as _schema

BaselineCell = _schema.BaselineCell
CHAMPION_MATRIX_PATH = _schema.CHAMPION_MATRIX_PATH
GENERALIZATION_BASELINE_PATH = _schema.GENERALIZATION_BASELINE_PATH
GENERALIZATION_POLICY_PATH = _schema.GENERALIZATION_POLICY_PATH
GeneralizationBaseline = _schema.GeneralizationBaseline
GeneralizationPolicy = _schema.GeneralizationPolicy
REQUIRED_GENERALIZATION_BASELINE_SHA256 = _schema.REQUIRED_GENERALIZATION_BASELINE_SHA256
REQUIRED_GENERALIZATION_POLICY_SHA256 = _schema.REQUIRED_GENERALIZATION_POLICY_SHA256
ReplayError = _schema.ReplayError

_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS = _schema.ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS
_ARTIFACT_FIELDS_V1 = _schema.ARTIFACT_FIELDS_V1
_ARTIFACT_FIELDS_V2 = _schema.ARTIFACT_FIELDS_V2
_ATTRIBUTION_DEFINITION = _schema.ATTRIBUTION_DEFINITION
_BASELINE_CELL_FIELDS = _schema.BASELINE_CELL_FIELDS
_CELL_FIELDS_V1 = _schema.CELL_FIELDS_V1
_CELL_FIELDS_V2 = _schema.CELL_FIELDS_V2
_COMMIT = _schema.COMMIT_PATTERN
_DATA_FIELDS = _schema.DATA_FIELDS
_DEPRECATED_V1_ATTRIBUTION_TOKEN = _schema.DEPRECATED_V1_ATTRIBUTION_TOKEN
_EVIDENCE_FIELDS = _schema.EVIDENCE_FIELDS
_METRIC_FIELDS = _schema.METRIC_FIELDS
_PROVENANCE_FIELDS = _schema.PROVENANCE_FIELDS
_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256 = (
    _schema.REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256
)
_ROOT = _schema.REPOSITORY_ROOT
_RUNTIME_FIELDS = _schema.RUNTIME_FIELDS
_SHA256 = _schema.SHA256_PATTERN
_artifact_equality_sha256 = _schema.artifact_equality_sha256
_canonical_sha256 = _schema.canonical_sha256
_derived_seed = _schema.derived_seed
_hash_json = _schema.hash_json
_metric_payload = _schema.metric_payload
_metrics_reconciled_from_raw = _schema.metrics_reconciled_from_raw
_provenance_schema_failures = _schema.provenance_schema_failures
_read_json = _schema.read_json
_reject_duplicate_keys = _schema.reject_duplicate_keys
_reject_nonstandard_constant = _schema.reject_nonstandard_constant
_replay_error = _schema.replay_error
_require_exact_seal = _schema.require_exact_seal
_require_sha256 = _schema.require_sha256
_schema_failures = _schema.schema_failures

_attribution_neutral_equality_sha256 = _projection.attribution_neutral_equality_sha256
_candidate_contract_sha256 = _projection.candidate_contract_sha256
_project_raw_evidence_for_frozen_v1 = _projection.project_raw_evidence_for_frozen_v1
_v2_economic_projection = _projection.v2_economic_projection

_load_baseline_cells = _cells.load_baseline_cells
load_generalization_baseline = _cells.load_generalization_baseline
load_generalization_policy = _cells.load_generalization_policy
_RandomTailStatistics = _evaluator.RandomTailStatistics
_evaluate_recovered_against_group_envelope = (
    _evaluator.evaluate_recovered_against_group_envelope
)
_quantile = _evaluator.quantile
_random_tail_statistics = _evaluator.random_tail_statistics
_violates_effective_floor = _evaluator.violates_effective_floor
evaluate_cell_non_regression = _evaluator.evaluate_cell_non_regression


def head_and_source_capability() -> Callable[[Path], tuple[str, str]]:
    return _head_and_source


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

    with _evaluator.generalization_policy_capabilities(
        head_and_source=head_and_source_capability()
    ):
        return _evaluator.evaluate_generalization_policy_artifact(
            artifact,
            baseline=baseline,
            policy=policy,
            require_exact_equality=require_exact_equality,
            data_dir=data_dir,
            expected_config=expected_config,
        )

candidate_contract_sha256 = _candidate_contract_sha256
head_and_source = _head_and_source

for _value in (
    ReplayError,
    BaselineCell,
    GeneralizationBaseline,
    GeneralizationPolicy,
    _reject_duplicate_keys,
    _reject_nonstandard_constant,
    _read_json,
    _hash_json,
    _artifact_equality_sha256,
    _schema_failures,
    _provenance_schema_failures,
    _metrics_reconciled_from_raw,
    _canonical_sha256,
    _require_sha256,
    _require_exact_seal,
    _metric_payload,
    _replay_error,
    _derived_seed,
    _load_baseline_cells,
    load_generalization_baseline,
    load_generalization_policy,
    evaluate_cell_non_regression,
    _evaluate_recovered_against_group_envelope,
    _quantile,
    _RandomTailStatistics,
    _random_tail_statistics,
    _violates_effective_floor,
    _candidate_contract_sha256,
    _project_raw_evidence_for_frozen_v1,
    _v2_economic_projection,
    _attribution_neutral_equality_sha256,
    evaluate_generalization_policy_artifact,
):
    _value.__module__ = __name__

__all__ = (  # noqa: RUF022 - frozen public-name order
    "BaselineCell",
    "CHAMPION_MATRIX_PATH",
    "GENERALIZATION_BASELINE_PATH",
    "GENERALIZATION_POLICY_PATH",
    "GeneralizationBaseline",
    "GeneralizationPolicy",
    "REQUIRED_GENERALIZATION_BASELINE_SHA256",
    "REQUIRED_GENERALIZATION_POLICY_SHA256",
    "ReplayError",
    "_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS",
    "_ARTIFACT_FIELDS_V1",
    "_ARTIFACT_FIELDS_V2",
    "_ATTRIBUTION_DEFINITION",
    "_BASELINE_CELL_FIELDS",
    "_CELL_FIELDS_V1",
    "_CELL_FIELDS_V2",
    "_COMMIT",
    "_DATA_FIELDS",
    "_DEPRECATED_V1_ATTRIBUTION_TOKEN",
    "_EVIDENCE_FIELDS",
    "_METRIC_FIELDS",
    "_PROVENANCE_FIELDS",
    "_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256",
    "_ROOT",
    "_RUNTIME_FIELDS",
    "_SHA256",
    "evaluate_cell_non_regression",
    "evaluate_generalization_policy_artifact",
    "load_generalization_baseline",
    "load_generalization_policy",
)
