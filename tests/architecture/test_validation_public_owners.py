from __future__ import annotations

import importlib
from collections.abc import Mapping

import pytest

from ._analysis import ROOT, architecture_snapshot
from ._private_imports import (
    current_governed_sources,
    load_inventory,
    scan_governed_private_edges,
    verify_inventory_seal,
)
from ._validation_transport import assert_validation_importer_public_transports

_OWNER_CAPABILITIES = {
    "research.first_divergence": (
        ("_CAUSAL_STAGES", "CAUSAL_STAGES"),
        ("_canonical_stages", "canonical_stages"),
        ("_trace_row", "trace_row"),
        ("_validate_trace_interval", "validate_trace_interval"),
    ),
    "uquant.validation.equivalence": (
        ("_baseline_data_provenance", "baseline_data_provenance"),
        ("_git_commit", "git_commit"),
        ("_immutable_equivalence_data", "immutable_equivalence_data"),
        ("_isolated_equivalence_tree", "isolated_equivalence_tree"),
        ("_require_clean_equivalence_tree", "require_clean_equivalence_tree"),
    ),
    "uquant.validation.generalization.baseline": (
        ("_parse_policy", "parse_policy"),
        ("_policy_number", "policy_number"),
        ("_read_generalization_baseline", "read_generalization_baseline"),
        ("_reject_duplicate_keys", "reject_duplicate_keys"),
        ("_reject_nonstandard_constant", "reject_nonstandard_constant"),
        ("_validate_baseline_envelope", "validate_baseline_envelope"),
    ),
    "uquant.validation.generalization.gates": (
        ("_aggregate_gate_results", "aggregate_gate_results"),
        ("_reference_aggregate", "reference_aggregate"),
        ("_relative_change", "relative_change"),
    ),
    "uquant.validation.generalization.metrics": (
        ("_deployment_from_result", "deployment_from_result"),
        ("_quantile", "quantile"),
    ),
    "uquant.validation.generalization.models": (
        ("_BASELINE_SCHEMA_VERSION", "BASELINE_SCHEMA_VERSION"),
        ("_COMMIT", "COMMIT_PATTERN"),
        ("_COMPETITOR_BEST_FIELDS", "COMPETITOR_BEST_FIELDS"),
        ("_COMPETITOR_PROVENANCE_FIELDS", "COMPETITOR_PROVENANCE_FIELDS"),
        ("_EXECUTION_CONTRACT", "EXECUTION_CONTRACT"),
        ("_FIXED_PRODUCTION_PATHS", "FIXED_PRODUCTION_PATHS"),
        ("_POLICY_FIELDS", "POLICY_FIELDS"),
        ("_PROVENANCE_SECTIONS", "PROVENANCE_SECTIONS"),
        ("_REFERENCE_FIELDS", "REFERENCE_FIELDS"),
        ("_SHA256", "SHA256_PATTERN"),
    ),
    "uquant.validation.generalization.provenance": (
        ("_exact_fields", "exact_fields"),
        ("_fingerprint", "fingerprint"),
        ("_git_executable", "git_executable"),
        ("_git_stdout", "git_stdout"),
        ("_immutable_validation_inputs", "immutable_validation_inputs"),
        ("_nonempty_text", "nonempty_text"),
        ("_production_commit", "production_commit"),
        ("_production_source_fingerprint", "production_source_fingerprint"),
        ("_validated_competitor_best", "validated_competitor_best"),
        ("_validated_provenance", "validated_provenance"),
        ("_validation_fingerprint", "validation_fingerprint"),
    ),
    "uquant.validation.generalization.scenarios": (
        ("_canonical_symbols", "canonical_symbols"),
        ("_derived_seed", "derived_seed"),
        ("_slug", "slug"),
        ("_unique_integers", "unique_integers"),
        ("_validate_industry_coverage", "validate_industry_coverage"),
    ),
    "uquant.validation.generalization_matrix": (("_head_and_source", "head_and_source"),),
    "uquant.validation.generalization_policy.cells": (("_load_baseline_cells", "load_baseline_cells"),),
    "uquant.validation.generalization_policy.evaluator": (
        ("_RandomTailStatistics", "RandomTailStatistics"),
        (
            "_evaluate_recovered_against_group_envelope",
            "evaluate_recovered_against_group_envelope",
        ),
        ("_quantile", "quantile"),
        ("_random_tail_statistics", "random_tail_statistics"),
        ("_violates_effective_floor", "violates_effective_floor"),
    ),
    "uquant.validation.generalization_policy.projection": (
        ("_attribution_neutral_equality_sha256", "attribution_neutral_equality_sha256"),
        ("_candidate_contract_sha256", "candidate_contract_sha256"),
        ("_project_raw_evidence_for_frozen_v1", "project_raw_evidence_for_frozen_v1"),
        ("_v2_economic_projection", "v2_economic_projection"),
    ),
    "uquant.validation.generalization_policy.schema": (
        ("_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS", "ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS"),
        ("_ARTIFACT_FIELDS_V1", "ARTIFACT_FIELDS_V1"),
        ("_ARTIFACT_FIELDS_V2", "ARTIFACT_FIELDS_V2"),
        ("_ATTRIBUTION_DEFINITION", "ATTRIBUTION_DEFINITION"),
        ("_BASELINE_CELL_FIELDS", "BASELINE_CELL_FIELDS"),
        ("_CELL_FIELDS_V1", "CELL_FIELDS_V1"),
        ("_CELL_FIELDS_V2", "CELL_FIELDS_V2"),
        ("_COMMIT", "COMMIT_PATTERN"),
        ("_DATA_FIELDS", "DATA_FIELDS"),
        ("_DEPRECATED_V1_ATTRIBUTION_TOKEN", "DEPRECATED_V1_ATTRIBUTION_TOKEN"),
        ("_EVIDENCE_FIELDS", "EVIDENCE_FIELDS"),
        ("_METRIC_FIELDS", "METRIC_FIELDS"),
        ("_PROVENANCE_FIELDS", "PROVENANCE_FIELDS"),
        (
            "_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256",
            "REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256",
        ),
        ("_ROOT", "REPOSITORY_ROOT"),
        ("_RUNTIME_FIELDS", "RUNTIME_FIELDS"),
        ("_SHA256", "SHA256_PATTERN"),
        ("_artifact_equality_sha256", "artifact_equality_sha256"),
        ("_canonical_sha256", "canonical_sha256"),
        ("_derived_seed", "derived_seed"),
        ("_hash_json", "hash_json"),
        ("_metric_payload", "metric_payload"),
        ("_metrics_reconciled_from_raw", "metrics_reconciled_from_raw"),
        ("_provenance_schema_failures", "provenance_schema_failures"),
        ("_read_json", "read_json"),
        ("_reject_duplicate_keys", "reject_duplicate_keys"),
        ("_reject_nonstandard_constant", "reject_nonstandard_constant"),
        ("_replay_error", "replay_error"),
        ("_require_exact_seal", "require_exact_seal"),
        ("_require_sha256", "require_sha256"),
        ("_schema_failures", "schema_failures"),
    ),
    "uquant.validation.holdout.artifact_transaction": (
        ("_artifact_bundle_lock", "artifact_bundle_lock"),
        ("_artifact_bundle_lock_path", "artifact_bundle_lock_path"),
        ("_artifact_bundle_lock_paths", "artifact_bundle_lock_paths"),
        ("_artifact_snapshots", "artifact_snapshots"),
        ("_canonical_carrier_path", "canonical_carrier_path"),
        ("_read_protected_artifact", "read_protected_artifact"),
        ("_reject_authoritative_output_paths", "reject_authoritative_output_paths"),
        ("_reject_output_in_protected_data", "reject_output_in_protected_data"),
        ("_resolved_path_text", "resolved_path_text"),
        ("_restore_artifact_snapshots", "restore_artifact_snapshots"),
    ),
    "uquant.validation.holdout.checkpoints": (
        ("_checkpoint_payload", "checkpoint_payload"),
        ("_read_checkpoint_carrier", "read_checkpoint_carrier"),
        ("_validate_daily_replay_continuity", "validate_daily_replay_continuity"),
        ("_verify_checkpoint_artifacts", "verify_checkpoint_artifacts"),
    ),
    "uquant.validation.holdout.contract": (
        ("_ACCOUNT_EXECUTION_FIELDS", "ACCOUNT_EXECUTION_FIELDS"),
        ("_CHECKPOINT_RELATIVE", "CHECKPOINT_RELATIVE"),
        ("_CLI_OPERATIONAL_COMMANDS", "CLI_OPERATIONAL_COMMANDS"),
        ("_COMMIT", "COMMIT_PATTERN"),
        ("_MANIFEST_FIELDS", "MANIFEST_FIELDS"),
        ("_SHA256", "SHA256_PATTERN"),
        ("_STRATEGY_FIXED_RELATIVES", "STRATEGY_FIXED_RELATIVES"),
        ("_STRATEGY_OPERATIONAL_RELATIVES", "STRATEGY_OPERATIONAL_RELATIVES"),
        ("_canonical_sha256", "canonical_sha256"),
        ("_closed_csv_files", "closed_csv_files"),
        ("_csv_dates_from_text", "csv_dates_from_text"),
        ("_git_executable", "git_executable"),
        ("_read_json", "read_json"),
        ("_repository_root", "repository_root"),
        ("_session_dates", "session_dates"),
    ),
    "uquant.validation.holdout.manifest": (
        ("_assemble_future_holdout_manifest", "assemble_future_holdout_manifest"),
        ("_normalized_scores", "normalized_scores"),
        ("_validate_future_holdout_manifest_payload", "validate_future_holdout_manifest_payload"),
        ("_validated_score_values", "validated_score_values"),
    ),
    "uquant.validation.holdout.replay": (("_daily_decision_payload", "daily_decision_payload"),),
    "uquant.validation.holdout.snapshots": (
        ("_capture_holdout_data", "capture_holdout_data"),
        ("_materialize_overlay", "materialize_overlay"),
        ("_validated_snapshot_prefix_sha256", "validated_snapshot_prefix_sha256"),
    ),
    "uquant.validation.holdout.source_identity": (("_state_hashes", "state_hashes"),),
    "uquant.validation.promotion": (("_compact", "compact_promotion_payload"),),
}

DIRECT_PUBLIC_ROUTES = {
    (owner, private): (owner, public)
    for owner, capabilities in _OWNER_CAPABILITIES.items()
    for private, public in capabilities
}
DIRECT_PUBLIC_ROUTES[("uquant.engine", "_attach_target_attribution")] = (
    "uquant.engine",
    "attach_target_attribution",
)

_QUALIFIED_CAPABILITIES = {
    "uquant.contracts.strict_json": (
        ("_reject_contract_json_constant", "reject_contract_json_constant"),
        ("_reject_duplicate_json_keys", "reject_duplicate_json_keys"),
    ),
    "uquant.infrastructure.atomic_files": (
        ("_aliases", "atomic_path_aliases"),
        ("_existing_destination_mode", "existing_destination_mode"),
        ("_fsync_directory", "fsync_directory"),
        ("_open_temporary", "open_temporary"),
        ("_reject_symlink_path", "reject_symlink_path"),
    ),
    "uquant.contracts.universe": (
        ("_DEFAULT_UNIVERSE", "DEFAULT_UNIVERSE"),
        ("_MEMBER_FIELDS", "MEMBER_FIELDS"),
        ("_SHA256", "SHA256_PATTERN"),
        ("_SYMBOL", "SYMBOL_PATTERN"),
        ("_canonical_payload", "canonical_payload"),
        ("_parse_date", "parse_date"),
        ("_read_json", "read_json"),
        ("_read_json_bytes", "read_json_bytes"),
        ("_reject_duplicate_keys", "reject_duplicate_keys"),
        ("_reject_nonstandard_constant", "reject_nonstandard_constant"),
        ("_resource_bytes", "resource_bytes"),
        ("_sha256", "sha256_bytes"),
    ),
}

QUALIFIED_PUBLIC_ROUTES = {
    (owner, private): (owner, public)
    for owner, capabilities in _QUALIFIED_CAPABILITIES.items()
    for private, public in capabilities
}


def _rows(kind: str) -> list[Mapping[str, object]]:
    inventory = load_inventory()
    verify_inventory_seal(inventory)
    field = (
        "direct_private_imports"
        if kind == "direct"
        else "qualified_private_accesses"
    )
    routes = DIRECT_PUBLIC_ROUTES if kind == "direct" else QUALIFIED_PUBLIC_ROUTES
    raw_rows = inventory[field]
    assert isinstance(raw_rows, list)
    rows = [
        row
        for row in raw_rows
        if isinstance(row, Mapping)
        and (str(row["imported_from"]), str(row["name"])) in routes
    ]
    assert isinstance(rows, list)
    assert all(isinstance(row, Mapping) for row in rows)
    return rows


def test_architecture_validation_inventory_and_public_owner_routes_are_exact() -> None:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    validation = graph["task9_relocated_private_imports"]
    assert isinstance(validation, list) and len(validation) == 178
    direct = _rows("direct")
    qualified = _rows("qualified")
    assert len(direct) == 189 and len(qualified) == 19
    assert {(str(row["imported_from"]), str(row["name"])) for row in direct} == set(DIRECT_PUBLIC_ROUTES)
    assert {(str(row["imported_from"]), str(row["name"])) for row in qualified} == set(
        QUALIFIED_PUBLIC_ROUTES
    )
    for (legacy_owner, private), (public_owner, public) in sorted(
        {**DIRECT_PUBLIC_ROUTES, **QUALIFIED_PUBLIC_ROUTES}.items()
    ):
        legacy_module = importlib.import_module(legacy_owner)
        public_module = importlib.import_module(public_owner)
        assert getattr(legacy_module, private) is getattr(public_module, public)


def test_architecture_validation_direct_importers_keep_exact_local_legacy_bindings() -> None:
    rows = _rows("direct")
    assert_validation_importer_public_transports(
        root=ROOT,
        rows=rows,
        routes=DIRECT_PUBLIC_ROUTES,
    )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "research/generalization_ablation_cli.py",
            "CAUSAL_STAGES as TRACE_STAGES",
            "CAUSAL_STAGES as TRACE_STAGE_VALUES",
        ),
        (
            "uquant/validation/generalization_reference.py",
            "_load_baseline_cells = _cells.load_baseline_cells",
            "_load_baseline_cells = _cells.load_generalization_baseline",
        ),
    ),
)
def test_architecture_validation_importer_transport_rejects_unknown_binding(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert original in source
    with pytest.raises(AssertionError):
        assert_validation_importer_public_transports(
            root=ROOT,
            rows=_rows("direct"),
            routes=DIRECT_PUBLIC_ROUTES,
            candidate_sources={relative: source.replace(original, mutation, 1)},
        )


def test_architecture_validation_closes_all_raw_private_edges() -> None:
    observed = scan_governed_private_edges(current_governed_sources())
    assert observed["direct"] == []
    assert observed["qualified"] == []
