from __future__ import annotations

import json
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG

PHASE7_CHANGED_PATHS = {
    "artifacts/sentinel/exclusive_freeze/README.md",
    "artifacts/sentinel/exclusive_freeze/account_code_identity_migration.json",
    "artifacts/sentinel/exclusive_freeze/atomicity_boundary.json",
    "artifacts/sentinel/exclusive_freeze/candidate_lock.json",
    "artifacts/sentinel/exclusive_freeze/exclusive_freeze_events.json",
    "artifacts/sentinel/exclusive_freeze/final_decision.json",
    "artifacts/sentinel/exclusive_freeze/first_divergence.json",
    "artifacts/sentinel/exclusive_freeze/phase6_baseline.json",
    "artifacts/sentinel/exclusive_freeze/small_gate.json",
    "benchmarks/config_parameter_governance.json",
    "docs/OPERATIONS.md",
    "docs/PERFORMANCE.md",
    "docs/RISK_SENTINEL.md",
    "docs/superpowers/plans/2026-08-20-risk-sentinel-exclusive-freeze.md",
    "research/first_divergence.py",
    "research/sentinel_exclusive_freeze.py",
    "tests/test_config_contracts.py",
    "tests/test_config_governance.py",
    "tests/test_phase7_evidence_artifacts.py",
    "tests/test_risk_sentinel_integration.py",
    "tests/test_sentinel_exclusive_freeze.py",
    "tests/test_sentinel_freeze_new_risk.py",
    "uquant/config.py",
    "uquant/config_governance.py",
    "uquant/engine.py",
    "uquant/risk.py",
    "uquant/risk_sentinel/integration.py",
}


def _inventory() -> dict[str, object]:
    path = Path(
        "artifacts/sentinel/evidence_closure/phase7_recovery_inventory.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase7_recovery_inventory_classifies_every_changed_path_once() -> None:
    payload = _inventory()
    rows = payload["files"]
    assert isinstance(rows, list)
    paths = [row["path"] for row in rows]

    assert set(paths) == PHASE7_CHANGED_PATHS
    assert len(paths) == len(set(paths))
    assert {row["disposition"] for row in rows} <= {
        "merge",
        "rewrite",
        "archive",
        "discard",
    }


def test_phase7_recovery_inventory_excludes_candidate_authority_assets() -> None:
    payload = _inventory()
    excluded = payload["excluded_from_main"]

    assert excluded == [
        "candidate configuration",
        "exclusive freeze authority",
        "new state machine",
        "new trading path",
    ]
    assert payload["production_causal_confirmation_enabled"] is False
    assert payload["whole_merge_or_cherry_pick"] is False
    assert not Path("artifacts/sentinel/exclusive_freeze/candidate_lock.json").exists()
    assert not Path("research/sentinel_exclusive_freeze.py").exists()


def test_consolidated_production_modes_reject_candidate_and_gross_cap_values() -> None:
    assert DEFAULT_CONFIG.risk_sentinel_mode == "FREEZE_ONLY"
    assert DEFAULT_CONFIG.risk_sentinel_causal_confirmation_enabled is False

    with pytest.raises(ValueError, match="LIMITED_GROSS_CAP was rejected"):
        DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="risk_sentinel_mode is invalid"):
        DEFAULT_CONFIG.override(  # type: ignore[arg-type]
            risk_sentinel_mode="SENTINEL_EXCLUSIVE_FREEZE"
        )


def test_phase8_economic_equivalence_artifact_is_exact_and_complete() -> None:
    payload = json.loads(
        Path("artifacts/sentinel/evidence_closure/economic_equivalence.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["passed"] is True
    assert payload["cases"] == 45
    assert payload["baseline_commit"] == "711af1179aa72ce48ca3a6af58ecddb3a029a7ce"
    assert payload["baseline_trace_sha256"] == payload["candidate_trace_sha256"]
    assert all(payload["exact_dimensions"].values())


def test_phase8_account_migration_changes_identity_only() -> None:
    payload = json.loads(
        Path(
            "artifacts/sentinel/evidence_closure/account_code_identity_migration.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "PASS"
    assert payload["schema_version_before"] == payload["schema_version_after"] == 5
    assert payload["economic_payload_equal"] is True
    assert (
        payload["economic_state_sha256_before"]
        == payload["economic_state_sha256_after"]
    )
    assert payload["changed_fields"] == ["code_hash", "account_migrations[-1]"]
    assert payload["migration_event"]["migration_type"] == "code_identity_only"
