from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from research.sentinel_evidence_closure import run_evidence_closure
from uquant.config import DEFAULT_CONFIG

EVIDENCE_RECOVERY_CHANGED_PATHS = {
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
EVIDENCE_CLOSURE_DELIVERY_COMMIT = "0ae54c0a6d2d4ca3dfe9814c75fbe82ae5591ac4"
_CURRENT_CONFIG_SHA256 = (
    "c05faf292a508d825cb4aaee09de65a5fb5a8db6acae6d21348ffcbec86d954b"
)
_HISTORICAL_CONFIG_SHA256 = (
    "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
)


def _inventory() -> dict[str, object]:
    path = Path(
        "artifacts/sentinel/evidence_closure/phase7_recovery_inventory.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_blob_sha256(commit: str, path: str) -> str:
    source = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(source).hexdigest()


def _historical_whole_package_fingerprint(commit: str) -> str:
    paths = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "uquant"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    digest = hashlib.sha256()
    for path in sorted(path for path in paths if path.endswith(".py")):
        digest.update(path.removeprefix("uquant/").encode())
        source = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
        digest.update(source)
    for path in (
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
    ):
        digest.update(Path(path).name.encode())
        source = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
        digest.update(source)
    return digest.hexdigest()


def test_evidence_recovery_inventory_classifies_every_changed_path_once() -> None:
    payload = _inventory()
    rows = payload["files"]
    assert isinstance(rows, list)
    paths = [row["path"] for row in rows]

    assert set(paths) == EVIDENCE_RECOVERY_CHANGED_PATHS
    assert len(paths) == len(set(paths))
    assert {row["disposition"] for row in rows} <= {
        "merge",
        "rewrite",
        "archive",
        "discard",
    }


def test_evidence_recovery_inventory_excludes_candidate_authority_assets() -> None:
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


def test_evidence_closure_economic_equivalence_artifact_is_exact_and_complete() -> None:
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

    seal = payload["delivery_seal"]
    assert payload["candidate_commit"] == "4067f0eb686ca29739f044dd4ee546b75c154a59"
    assert payload["candidate_remote_commit"] == (
        "ca422cb981493bc2c16a2f2113fd5a3f9e6b5943"
    )
    assert seal["economic_replay_rerun"] is True
    assert seal["replayed_candidate_commit"] == payload["candidate_commit"]
    assert seal["replayed_candidate_remote_commit"] == (
        payload["candidate_remote_commit"]
    )
    assert seal["allowed_post_replay_paths"] == [
        "artifacts/sentinel/evidence_closure/economic_equivalence.json",
        "artifacts/sentinel/evidence_closure/evidence_closure.json",
        "docs/reviews/2026-08-20-risk-sentinel-consolidation.md",
        "tests/test_phase8_consolidation_artifacts.py",
    ]

    subprocess.run(
        ["git", "merge-base", "--is-ancestor", EVIDENCE_CLOSURE_DELIVERY_COMMIT, "HEAD"],
        check=True,
    )
    changed_during_evidence_closure_delivery = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            f"{payload['candidate_remote_commit']}..{EVIDENCE_CLOSURE_DELIVERY_COMMIT}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert sorted(changed_during_evidence_closure_delivery) == seal["allowed_post_replay_paths"]

    assert seal["production_code_sha256"] == (
        "591d1659c8d4498f37700c651fcde25bdf4ca89054df7ec8d849e5dda374c1b6"
    )
    assert seal["config_sha256"] == (
        "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
    )
    assert seal["uv_lock_sha256"] == _sha256("uv.lock")
    assert seal["equivalence_runner_sha256"] == _git_blob_sha256(
        payload["candidate_remote_commit"],
        "research/committed_economic_equivalence.py",
    )


def test_evidence_closure_seal_matches_historical_analyzer(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    committed = json.loads(
        Path("artifacts/sentinel/evidence_closure/evidence_closure.json").read_text(
            encoding="utf-8"
        )
    )
    provenance = committed["provenance"]
    assert provenance["analyzer_commit"] == (
        "4067f0eb686ca29739f044dd4ee546b75c154a59"
    )
    assert provenance["analyzer_remote_commit"] == (
        "ca422cb981493bc2c16a2f2113fd5a3f9e6b5943"
    )
    assert provenance["analyzer_sha256"] == _git_blob_sha256(
        provenance["analyzer_remote_commit"],
        "research/sentinel_evidence_closure.py",
    )
    assert provenance["code_sha256"] == _historical_whole_package_fingerprint(
        provenance["analyzer_remote_commit"]
    )

    output = tmp_path / "reviewed-evidence-closure.json"
    regenerated = run_evidence_closure(
        data_dir=data_dir,
        as_of="2026-08-05",
        output=output,
    )
    expected = json.loads(json.dumps(committed))
    for field in (
        "analyzer_commit",
        "analyzer_remote_commit",
        "analyzer_sha256",
        "regenerated_payload_sha256",
    ):
        del expected["provenance"][field]

    current_without_identity = json.loads(json.dumps(regenerated))
    historical_without_identity = json.loads(json.dumps(expected))
    assert (
        current_without_identity["provenance"]["code_sha256"]
        != historical_without_identity["provenance"]["code_sha256"]
    )
    del current_without_identity["provenance"]["code_sha256"]
    del historical_without_identity["provenance"]["code_sha256"]
    assert (
        current_without_identity["provenance"]["config_sha256"]
        == _CURRENT_CONFIG_SHA256
    )
    assert (
        historical_without_identity["provenance"]["config_sha256"]
        == _HISTORICAL_CONFIG_SHA256
    )
    current_without_identity["provenance"]["config_sha256"] = (
        _HISTORICAL_CONFIG_SHA256
    )
    assert current_without_identity == historical_without_identity
    historical_bytes = (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode()
    assert provenance["regenerated_payload_sha256"] == hashlib.sha256(
        historical_bytes
    ).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8")) == regenerated


def test_evidence_closure_account_migration_changes_identity_only() -> None:
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
