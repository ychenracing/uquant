from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from uquant.contracts.source_surfaces import SOURCE_SURFACE_IDS
from uquant.contracts.strict_json import canonical_json_sha256
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


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return completed.stdout


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


def test_task12_preserves_the_historical_source_epoch() -> None:
    payload = _artifact("account_code_identity_migration.json")
    epoch = payload["source_epoch"]
    assert epoch["epoch_id"] == "production_wheel_v1"
    assert epoch["registered_at_commit"] == GATE_COMMIT
    assert epoch["status"] == "ACTIVE_FOR_NEW_ACCOUNTS"


def test_source_epoch_v2_has_a_remote_recovery_anchor() -> None:
    payload = _artifact("source_epoch_v2.json")
    unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    assert payload["canonical_sha256"] == canonical_json_sha256(unsealed)
    assert payload["superseded_by_epoch_id"] == "production_wheel_v3"
    epoch = payload["source_epoch"]
    recovery = epoch["remote_recovery"]

    assert epoch["registered_at_commit"] == recovery["original_local_gate_commit"]
    assert epoch["production_wheel"]["reproducible_build"]["clean_source"] == (
        f"git archive {epoch['registered_at_commit']}"
    )
    assert recovery["original_local_gate_commit"] == (
        "f6b75aa809294ebabebf7e6e0cf4cea6dea41da6"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", recovery["commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", recovery["tree_sha"])
    assert _git("rev-parse", f"{recovery['commit']}^{{tree}}") == recovery["tree_sha"]
    for relative in ("LICENSE", "README.md", "pyproject.toml"):
        assert _git_bytes("show", f"{epoch['registered_at_commit']}:{relative}") == _git_bytes(
            "show", f"{recovery['commit']}:{relative}"
        )
    assert _git("rev-parse", f"{epoch['registered_at_commit']}:uquant") == _git(
        "rev-parse", f"{recovery['commit']}:uquant"
    )
    assert recovery["package_input_equivalent"] is True
    assert recovery["payload_manifest_equal"] is True
    assert recovery["verified_payload_manifest_sha256"] == (
        epoch["production_wheel"]["payload_manifest_sha256"]
    )
    assert recovery["historical_container_sha256"] == epoch["production_wheel"]["sha256"]
    assert recovery["canonical_container_sha256"] != recovery["historical_container_sha256"]


def test_current_source_epoch_binds_current_surfaces_and_production_wheel() -> None:
    payload = _artifact("source_epoch_v3.json")
    assert payload["schema"] == "uquant.source-epoch.v1"
    unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    assert payload["canonical_sha256"] == canonical_json_sha256(unsealed)
    assert payload["status"] == "PASS"
    assert payload["previous_epoch_id"] == "production_wheel_v2"
    assert payload["change_classification"] == (
        "NON_ECONOMIC_DOCUMENTATION_AND_BUILD_GOVERNANCE"
    )
    assert payload["economic_ast_equal"] is True
    assert payload["requirements_changed"] is False
    assert payload["uv_lock_changed"] is False
    epoch = payload["source_epoch"]
    assert epoch["epoch_id"] == "production_wheel_v3"
    assert re.fullmatch(r"[0-9a-f]{40}", epoch["registered_at_commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", epoch["registered_tree_sha"])
    assert _git("cat-file", "-t", epoch["registered_at_commit"]) == "commit"
    assert _git("rev-parse", f"{epoch['registered_at_commit']}^{{tree}}") == (
        epoch["registered_tree_sha"]
    )
    assert _git("show", "-s", "--format=%P", epoch["registered_at_commit"]) == (
        payload["baseline_commit"]
    )
    if github_sha := os.environ.get("GITHUB_SHA"):
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", epoch["registered_at_commit"], github_sha],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        assert ancestry.returncode == 0, ancestry.stderr.decode()
    assert epoch["status"] == "ACTIVE_FOR_NEW_ACCOUNTS"
    expected_surfaces = {
        identifier: source_surface_fingerprint(ROOT, identifier)
        for identifier in SOURCE_SURFACE_IDS
    }
    assert epoch["reviewed_surfaces"] == expected_surfaces
    wheel = epoch["production_wheel"]
    build = wheel["reproducible_build"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["build-system"]["requires"] == [build["backend_requirement"]]
    assert build == {
        "backend_requirement": "setuptools==84.0.0",
        "builder": "python -m scripts.build_reproducible_wheel",
        "clean_source": f"git archive {epoch['registered_at_commit']}",
        "container_normalization": (
            "ZIP_STORED; lexicographic member order; 1980-01-01T00:00:00; "
            "regular files 0644"
        ),
        "frontend": "build==1.5.0",
        "source_date_epoch": 315532800,
    }
    wheel_path = ROOT / wheel["artifact_path"]
    assert wheel_path.is_file() and not wheel_path.is_symlink()
    assert wheel["bytes"] == wheel_path.stat().st_size
    assert wheel["sha256"] == _sha256(wheel_path)
    with zipfile.ZipFile(wheel_path) as archive:
        members = []
        for info in archive.infolist():
            content = archive.read(info.filename)
            members.append(
                {
                    "path": info.filename,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
    members.sort(key=lambda member: member["path"])
    assert wheel["payload_manifest_sha256"] == canonical_json_sha256(members)
    assert wheel["members"] == len(members)
    assert wheel["uquant_members"] == sum(
        str(member["path"]).startswith("uquant/") for member in members
    )
    assert wheel["dist_info_members"] == sum(
        ".dist-info/" in str(member["path"]) for member in members
    )
    assert wheel["members"] == wheel["uquant_members"] + wheel["dist_info_members"]
    assert wheel["uquant_members"] == 209
    assert wheel["unexpected_members"] == [
        member["path"]
        for member in members
        if not (
            str(member["path"]).startswith("uquant/")
            or ".dist-info/" in str(member["path"])
        )
    ]
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
