"""Current test-layout contract with explicit frozen-path projections."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from ._governance_inventory import semantic_units

TEST_RELOCATION_PATHS: Mapping[str, tuple[str, ...]] = {
    "tests/architecture/_analysis.py": (
        "tests/architecture/_analysis.py",
        "tests/architecture/_analysis_authorities.py",
        "tests/architecture/_analysis_relocations.py",
        "tests/architecture/_analysis_debt.py",
    ),
    "tests/architecture/_task3_baseline.py": (
        "tests/architecture/_compatibility_baseline.py",
        "tests/architecture/_compatibility_validation_runtime.py",
    ),
    "tests/architecture/test_task7_risk_boundaries.py": (
        "tests/architecture/test_risk_boundaries.py",
        "tests/architecture/_risk_import_boundaries.py",
    ),
    "tests/test_attribution_identity.py": (
        "tests/test_attribution_identity.py",
        "tests/_attribution_identity_retention_cases.py",
        "tests/_attribution_identity_reconciliation_cases.py",
        "tests/_attribution_identity_schema_cases.py",
    ),
    "tests/test_engine_contracts.py": (
        "tests/test_engine_contracts.py",
        "tests/_engine_account_and_metrics_cases.py",
        "tests/_engine_decision_state_cases.py",
    ),
    "tests/test_engineering_gate_edges.py": (
        "tests/test_engineering_gate_edges.py",
        "tests/_engineering_holdout_observation_cases.py",
        "tests/_engineering_provenance_universe_cases.py",
    ),
    "tests/test_execution.py": (
        "tests/test_execution.py",
        "tests/_execution_order_lifecycle_cases.py",
        "tests/_execution_risk_and_fill_cases.py",
    ),
    "tests/test_future_holdout_runtime.py": (
        "tests/test_future_holdout_runtime.py",
        "tests/_future_holdout_transaction_recovery_cases.py",
        "tests/_future_holdout_carrier_identity_cases.py",
        "tests/_future_holdout_replay_binding_cases.py",
    ),
    "tests/test_generalization.py": (
        "tests/test_generalization.py",
        "tests/_generalization_validation_metrics_cases.py",
        "tests/_generalization_policy_cases.py",
        "tests/_generalization_runner_cases.py",
    ),
    "tests/test_generalization_matrix.py": (
        "tests/test_generalization_matrix.py",
        "tests/_generalization_matrix_replay_cases.py",
        "tests/_generalization_matrix_projection_cases.py",
        "tests/_generalization_matrix_validation_cases.py",
        "tests/_generalization_matrix_provenance_cases.py",
    ),
    "tests/test_lifecycle_and_risk.py": (
        "tests/test_lifecycle_and_risk.py",
        "tests/_lifecycle_strategic_discovery_cases.py",
        "tests/_lifecycle_strategic_cohort_cases.py",
        "tests/_lifecycle_freeze_tactical_probe_cases.py",
        "tests/_lifecycle_recovery_admission_cases.py",
        "tests/_lifecycle_protected_repair_cases.py",
        "tests/_lifecycle_freeze_execution_cases.py",
        "tests/_lifecycle_strategic_restore_cases.py",
        "tests/_lifecycle_strategic_guard_cases.py",
        "tests/_lifecycle_leader_recovery_cases.py",
        "tests/_lifecycle_restoration_risk_cases.py",
    ),
    "tests/test_phase2_ablation.py": (
        "tests/test_generalization_ablation.py",
        "tests/_generalization_carrier_worker_cases.py",
        "tests/_generalization_checkpoint_evidence_cases.py",
        "tests/_generalization_trust_boundary_cases.py",
    ),
    "tests/test_recovery_contracts.py": (
        "tests/test_recovery_contracts.py",
        "tests/_recovery_restore_completion_cases.py",
        "tests/_recovery_post_shock_cases.py",
    ),
    "tests/test_risk_transitions.py": (
        "tests/test_risk_transitions.py",
        "tests/_risk_transition_strategic_cap_cases.py",
        "tests/_risk_transition_overlay_budget_cases.py",
    ),
}


def _record_units(records: object) -> dict[str, Counter[str]]:
    assert isinstance(records, list)
    result: dict[str, Counter[str]] = {}
    for record in records:
        assert isinstance(record, Mapping)
        path = str(record["path"])
        units = record["semantic_units"]
        assert isinstance(units, list) and path not in result
        digests = Counter(
            str(unit["ast_sha256"])
            for unit in units
            if isinstance(unit, Mapping)
        )
        assert sum(digests.values()) == len(units)
        assert all(len(digest) == 64 for digest in digests)
        result[path] = digests
    return result


def verify_test_relocations(
    *,
    immutable_records: object,
    immutable_analysis_source: str,
    immutable_risk_source: str,
    root: Path,
    relocation_paths: Mapping[str, tuple[str, ...]] = TEST_RELOCATION_PATHS,
) -> None:
    """Verify frozen records and the exact current path projection independently."""

    frozen = _record_units(immutable_records)
    assert immutable_analysis_source.strip()
    assert immutable_risk_source.strip()
    assert dict(relocation_paths) == dict(TEST_RELOCATION_PATHS)
    assert set(frozen) == set(TEST_RELOCATION_PATHS)

    targets = [
        target
        for projected in relocation_paths.values()
        for target in projected
    ]
    assert len(targets) == len(set(targets))
    for projected in relocation_paths.values():
        assert projected
        for target in projected:
            path = root / target
            assert path.is_file()
            assert semantic_units(path.read_text(encoding="utf-8"))
