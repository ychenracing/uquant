"""Compatibility facade for deterministic generalization validation."""

from __future__ import annotations

import hashlib as hashlib
import shutil as shutil
import subprocess as subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

from ..manifest import verify_data_manifest as verify_data_manifest
from .baseline import (
    load_generalization_baseline,
    reference_payload,
)
from .baseline import (
    parse_policy as _parse_policy,
)
from .baseline import (
    policy_number as _policy_number,
)
from .baseline import (
    read_generalization_baseline as _read_generalization_baseline,
)
from .baseline import (
    reject_duplicate_keys as _reject_duplicate_keys,
)
from .baseline import (
    reject_nonstandard_constant as _reject_nonstandard_constant,
)
from .baseline import (
    validate_baseline_envelope as _validate_baseline_envelope,
)
from .gates import (
    aggregate_gate_results as _aggregate_gate_results,
)
from .gates import (
    evaluate_generalization,
)
from .gates import (
    reference_aggregate as _reference_aggregate,
)
from .gates import (
    relative_change as _relative_change,
)
from .metrics import (
    aggregate_metrics,
    industry_pnl_shares,
    observation_from_result,
    prior_dependence,
    symbol_pnl_concentration,
    symbol_pnl_from_result,
)
from .metrics import (
    deployment_from_result as _deployment_from_result,
)
from .metrics import (
    quantile as _quantile,
)
from .models import (
    BASELINE_SCHEMA_VERSION as _BASELINE_SCHEMA_VERSION,
)
from .models import (
    COMMIT_PATTERN as _COMMIT,
)
from .models import (
    COMPETITOR_BEST_FIELDS as _COMPETITOR_BEST_FIELDS,
)
from .models import (
    COMPETITOR_PROVENANCE_FIELDS as _COMPETITOR_PROVENANCE_FIELDS,
)
from .models import (
    EXECUTION_CONTRACT as _EXECUTION_CONTRACT,
)
from .models import (
    FIXED_PRODUCTION_PATHS as _FIXED_PRODUCTION_PATHS,
)
from .models import (
    POLICY_FIELDS as _POLICY_FIELDS,
)
from .models import (
    PROVENANCE_SECTIONS as _PROVENANCE_SECTIONS,
)
from .models import (
    REFERENCE_FIELDS as _REFERENCE_FIELDS,
)
from .models import (
    SHA256_PATTERN as _SHA256,
)
from .models import (
    GeneralizationBaseline,
    GeneralizationObservation,
    GeneralizationPolicy,
    GeneralizationScenario,
    PreWindowEvidence,
)
from .provenance import (
    GeneralizationRuntimeCapabilities,
    build_generalization_provenance,
    generalization_runtime_scope,
)
from .provenance import (
    exact_fields as _exact_fields,
)
from .provenance import (
    fingerprint as _fingerprint,
)
from .provenance import (
    git_executable as _git_executable,
)
from .provenance import git_stdout as _git_stdout
from .provenance import (
    immutable_validation_inputs as _immutable_validation_inputs,
)
from .provenance import (
    nonempty_text as _nonempty_text,
)
from .provenance import (
    production_commit as _production_commit,
)
from .provenance import (
    production_source_fingerprint as _production_source_fingerprint,
)
from .provenance import (
    validated_competitor_best as _validated_competitor_best,
)
from .provenance import (
    validated_provenance as _validated_provenance,
)
from .provenance import (
    validation_fingerprint as _validation_fingerprint,
)
from .runner import (
    run_generalization as _owner_run_generalization,
)
from .scenarios import (
    build_generalization_scenarios,
    compute_pre_window_evidence,
    scenario_fingerprint,
)
from .scenarios import (
    canonical_symbols as _canonical_symbols,
)
from .scenarios import (
    derived_seed as _derived_seed,
)
from .scenarios import (
    slug as _slug,
)
from .scenarios import (
    unique_integers as _unique_integers,
)
from .scenarios import (
    validate_industry_coverage as _validate_industry_coverage,
)

_owner_immutable_validation_inputs = _immutable_validation_inputs
_owner_production_commit = _production_commit


def current_runtime_capabilities() -> GeneralizationRuntimeCapabilities:
    return GeneralizationRuntimeCapabilities(
        git_stdout=_git_stdout,
        production_source_fingerprint=_production_source_fingerprint,
        verify_data_manifest=verify_data_manifest,
    )


def _with_current_runtime_capabilities[**Parameters, Result](
    function: Callable[Parameters, Result],
) -> Callable[Parameters, Result]:
    @wraps(function)
    def delegated(*args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
        with generalization_runtime_scope(current_runtime_capabilities()):
            return function(*args, **kwargs)

    return delegated


@contextmanager
@wraps(_owner_immutable_validation_inputs)
def _scoped_immutable_validation_inputs(*args: Any, **kwargs: Any) -> Iterator[None]:
    with generalization_runtime_scope(
        current_runtime_capabilities()
    ), _owner_immutable_validation_inputs(*args, **kwargs):
        yield


_immutable_validation_inputs = _scoped_immutable_validation_inputs
_production_commit = _with_current_runtime_capabilities(_owner_production_commit)
run_generalization = _with_current_runtime_capabilities(_owner_run_generalization)
git_stdout = _git_stdout

for _value in (
    PreWindowEvidence,
    GeneralizationScenario,
    GeneralizationObservation,
    GeneralizationBaseline,
    GeneralizationPolicy,
    _canonical_symbols,
    _slug,
    _validate_industry_coverage,
    compute_pre_window_evidence,
    _derived_seed,
    _unique_integers,
    build_generalization_scenarios,
    scenario_fingerprint,
    _fingerprint,
    _validation_fingerprint,
    _exact_fields,
    _nonempty_text,
    _validated_provenance,
    _validated_competitor_best,
    _production_source_fingerprint,
    _git_executable,
    _git_stdout,
    _production_commit,
    _immutable_validation_inputs,
    build_generalization_provenance,
    _reject_duplicate_keys,
    _reject_nonstandard_constant,
    _policy_number,
    _parse_policy,
    _read_generalization_baseline,
    _validate_baseline_envelope,
    load_generalization_baseline,
    reference_payload,
    symbol_pnl_from_result,
    symbol_pnl_concentration,
    _deployment_from_result,
    observation_from_result,
    _quantile,
    aggregate_metrics,
    prior_dependence,
    industry_pnl_shares,
    _reference_aggregate,
    _relative_change,
    _aggregate_gate_results,
    evaluate_generalization,
    run_generalization,
):
    _value.__module__ = __name__

__all__ = (  # noqa: RUF022 - frozen public-name order
    "GeneralizationBaseline",
    "GeneralizationObservation",
    "GeneralizationPolicy",
    "GeneralizationScenario",
    "PreWindowEvidence",
    "_BASELINE_SCHEMA_VERSION",
    "_COMMIT",
    "_COMPETITOR_BEST_FIELDS",
    "_COMPETITOR_PROVENANCE_FIELDS",
    "_EXECUTION_CONTRACT",
    "_FIXED_PRODUCTION_PATHS",
    "_POLICY_FIELDS",
    "_PROVENANCE_SECTIONS",
    "_REFERENCE_FIELDS",
    "_SHA256",
    "aggregate_metrics",
    "build_generalization_provenance",
    "build_generalization_scenarios",
    "compute_pre_window_evidence",
    "evaluate_generalization",
    "industry_pnl_shares",
    "load_generalization_baseline",
    "observation_from_result",
    "prior_dependence",
    "reference_payload",
    "run_generalization",
    "scenario_fingerprint",
    "symbol_pnl_concentration",
    "symbol_pnl_from_result",
)
