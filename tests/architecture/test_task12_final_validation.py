from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from uquant.contracts.source_surfaces import SOURCE_SURFACE_IDS
from uquant.provenance.fingerprints import source_surface_fingerprint

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = ROOT / "artifacts" / "architecture_refactor"
GATE_COMMIT = "ecee225237f02b4d21cbf65d88bc4ec5761603d3"
BASELINE_COMMIT = "f9fd489806a86b3a56f62b8668aafa252012d405"
GITHUB_JOB_TIMEOUT_MAX_MINUTES = 360


def _artifact(name: str) -> dict[str, Any]:
    value = json.loads((ARTIFACT_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_task12_engineering_timeout_can_cover_the_authoritative_suite() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    quality_block = workflow.partition("\n  quality:\n")[2].partition("\n  security:\n")[0]
    timeout = re.search(r"(?m)^    timeout-minutes: (\d+)$", quality_block)

    assert timeout is not None
    assert int(timeout.group(1)) == GITHUB_JOB_TIMEOUT_MAX_MINUTES


def test_task12_code_identity_migration_preserves_economics_and_old_epochs() -> None:
    payload = _artifact("account_code_identity_migration.json")

    assert payload["schema"] == (
        "uquant.architecture-refactor-account-code-identity-migration.v1"
    )
    assert payload["status"] == "PASS"
    assert payload["baseline_commit"] == BASELINE_COMMIT
    assert payload["gate_candidate_commit"] == GATE_COMMIT
    account = payload["account_identity"]
    assert account["economic_payload_equal"] is True
    assert account["economic_state_sha256_before"] == account["economic_state_sha256_after"]
    assert account["schema_version_before"] == account["schema_version_after"] == 5
    assert account["migration_event"]["migration_type"] == "code_identity_only"
    assert payload["legacy_identity_policy"] == {
        "full_package_v1": "KEEP_AUTHORITATIVE",
        "requirements.txt": "KEEP_AUTHORITATIVE",
        "rewritten_prior_epoch": False,
    }
    holdout = payload["future_holdout"]
    assert holdout["observed_sessions"] == 0
    assert holdout["action"] == "NO_APPEND_NO_BACKFILL"


def test_task12_source_epoch_binds_current_surfaces_and_production_wheel() -> None:
    payload = _artifact("account_code_identity_migration.json")
    epoch = payload["source_epoch"]
    assert epoch["epoch_id"] == "production_wheel_v1"
    assert epoch["registered_at_commit"] == GATE_COMMIT
    assert epoch["status"] == "ACTIVE_FOR_NEW_ACCOUNTS"
    expected_surfaces = {
        identifier: source_surface_fingerprint(ROOT, identifier)
        for identifier in SOURCE_SURFACE_IDS
    }
    assert epoch["reviewed_surfaces"] == expected_surfaces
    wheel = epoch["production_wheel"]
    assert wheel["members"] == wheel["uquant_members"] + wheel["dist_info_members"]
    assert wheel["uquant_members"] == 209
    assert wheel["unexpected_members"] == []
    assert epoch["requirements_sha256"] == _sha256(ROOT / "requirements.txt")
    assert epoch["uv_lock_sha256"] == _sha256(ROOT / "uv.lock")


def test_task12_committed_economic_equivalence_is_exact() -> None:
    payload = _artifact("economic_equivalence.json")

    assert payload["schema"] == "uquant.committed-economic-equivalence.v1"
    assert payload["baseline_commit"] == BASELINE_COMMIT
    assert payload["candidate_commit"] == GATE_COMMIT
    assert payload["cases"] == 45
    assert payload["passed"] is True
    assert payload["baseline_trace_sha256"] == payload["candidate_trace_sha256"]
    assert set(payload["exact_dimensions"]) == {
        "account_state_economic_fields",
        "decision_digest",
        "fills",
        "final_wealth",
        "max_drawdown",
        "orders",
        "risk_assessment_control",
        "target_portfolio",
        "trade_count",
    }
    assert all(payload["exact_dimensions"].values())


def test_task12_performance_uses_unchanged_budgets_and_paired_baseline() -> None:
    payload = _artifact("performance_equivalence.json")

    assert payload["schema"] == "uquant.architecture-refactor-performance-equivalence.v1"
    assert payload["status"] == "PASS"
    assert payload["gate_candidate_commit"] == GATE_COMMIT
    assert payload["authoritative_budget"]["thresholds_changed"] is False
    assert payload["paired_immutable_baseline"]["commit"] == BASELINE_COMMIT
    assert payload["paired_immutable_baseline"]["economic_summary_exact"] is True
    assert payload["frozen_wall_observation"]["wall_checks_passed"] is False
    assert payload["frozen_wall_observation"]["disposition"] == (
        "HOST_VARIANCE_CONFIRMED_BY_SAME_HOST_IMMUTABLE_BASELINE"
    )
    for check in payload["checks"].values():
        assert check["passed"] is True
        assert float(check["ratio"]) <= float(check["limit_ratio"])


def test_task12_final_validation_seals_local_evidence_without_faking_remote_ci() -> None:
    payload = _artifact("final_validation.json")

    assert payload["schema"] == "uquant.architecture-refactor-final-validation.v1"
    assert payload["status"] == "PASS_LOCAL_REMOTE_REQUIRED"
    assert payload["candidate"]["gate_commit"] == GATE_COMMIT
    assert payload["candidate"]["production_bytes_changed_after_gate"] is False
    assert payload["local_blockers"] == []
    assert payload["l4"]["status"] == "PASS_AFTER_TARGETED_CLOSURE"
    assert payload["phase1"]["status"] == "PASS"
    assert payload["phase2"]["status"] == "PASS"
    assert payload["phase2"]["aggregate"]["records"] == 234
    assert payload["phase2"]["aggregate"]["economic_cells_valid"] == 192
    assert payload["remote_release_gates"]["status"] == "REQUIRED_POST_SNAPSHOT"
    assert payload["remote_release_gates"]["windows_local_disposition"] == (
        "NOT_RUN_NON_NATIVE_BY_CONTRACT"
    )
    for evidence in payload["evidence"].values():
        path = ROOT / evidence["path"]
        assert path.stat().st_size == evidence["bytes"]
        assert _sha256(path) == evidence["sha256"]
        assert evidence["status"] == "PASS"
