from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from research.strategic_evidence.models import canonical_sha256
from research.strategic_evidence.report import (
    assemble_evidence_artifacts,
    validate_evidence_artifacts,
)
from scripts.run_strategic_evidence_closure import build_phase_commands, main


ROOT = Path(__file__).resolve().parents[1]


def test_orchestrator_builds_resumable_stage_commands_without_economic_shortcuts(
    tmp_path: Path,
) -> None:
    commands = build_phase_commands(root=ROOT, runtime_dir=tmp_path, resume=True)

    assert set(commands) == {"forced-owner", "witness-ablation", "reachability"}
    assert commands["forced-owner"][-1] == "--resume"
    assert commands["witness-ablation"][-1] == "--resume"
    assert commands["reachability"][-1] == "--resume"
    assert "--initial-only" not in commands["witness-ablation"]
    assert "--state-id" not in commands["reachability"]
    assert "--path-id" not in commands["reachability"]


def test_orchestrator_dry_run_assemble_materializes_auditable_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    source_dir = root / "artifacts/strategic_evidence_closure"
    source_dir.mkdir(parents=True)
    (root / "benchmarks").mkdir()
    shutil.copy2(
        ROOT / "benchmarks/strategic_evidence_closure_contract.json",
        root / "benchmarks/strategic_evidence_closure_contract.json",
    )
    for name in (
        "checkpoint3_forced_owner_full.json",
        "checkpoint4_witness_ablation_full.json",
        "checkpoint5_state_reachability_summary.json",
    ):
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, source_dir / name)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    shutil.copy2(
        Path("/tmp/uquant-task5-d33d717-full-84.jsonl.gz"),
        runtime / "checkpoint5_state_reachability_84.jsonl.gz",
    )

    assert main(
        [
            "--root",
            str(root),
            "--runtime-dir",
            str(runtime),
            "run",
            "--phase",
            "assemble",
            "--dry-run",
        ]
    ) == 0

    compact = json.loads((source_dir / "compact_summary.json").read_text(encoding="utf-8"))
    assert compact["dry_run"] is True


def test_workflow_is_manual_non_blocking_and_uses_pinned_actions() -> None:
    workflow = (ROOT / ".github/workflows/strategic-evidence-closure.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "continue-on-error: true" in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            action = line.split("uses:", 1)[1].split("#", 1)[0].strip()
            assert "@" in action
            assert len(action.rsplit("@", 1)[1]) == 40


def test_artifact_assembly_binds_external_shards_and_validates_readback(tmp_path: Path) -> None:
    external = tmp_path / "reachability.jsonl.gz"
    external.write_bytes(b"deterministic-external-evidence")
    sources = {
        "task3": ROOT / "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json",
        "task4": ROOT / "artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_full.json",
        "task5": ROOT / "artifacts/strategic_evidence_closure/checkpoint5_state_reachability_summary.json",
    }
    output = tmp_path / "artifacts"

    assembled = assemble_evidence_artifacts(
        root=ROOT,
        output_dir=output,
        source_paths=sources,
        task5_shard=external,
        dry_run=True,
    )
    validated = validate_evidence_artifacts(output, external_paths={"task5": external})

    assert assembled["runner_success"] is False  # fixture shard intentionally mismatches Task 5
    assert validated["manifest_payload_sha256"] == assembled["manifest_payload_sha256"]
    manifest = json.loads((output / "evidence_manifest.json").read_text(encoding="utf-8"))
    assert manifest["external_shards"]["task5"]["logical_path"].startswith(
        "artifacts/strategic_evidence_closure/external/"
    )
    assert str(tmp_path) not in json.dumps(manifest, sort_keys=True)


def test_validator_rejects_tampered_compact_summary(tmp_path: Path) -> None:
    output = tmp_path / "artifacts"
    output.mkdir()
    compact = {"schema_version": "x", "payload_sha256": ""}
    compact["payload_sha256"] = canonical_sha256(compact)
    (output / "compact_summary.json").write_text(json.dumps(compact), encoding="utf-8")
    manifest = {
        "schema_version": "x",
        "files": {
            "compact_summary.json": {
                "bytes_sha256": hashlib.sha256(
                    (output / "compact_summary.json").read_bytes()
                ).hexdigest()
            }
        },
        "external_shards": {},
        "payload_sha256": "",
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    (output / "evidence_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (output / "SHA256SUMS").write_text("", encoding="utf-8")
    (output / "analysis.md").write_text("analysis\n", encoding="utf-8")
    (output / "README.md").write_text("readme\n", encoding="utf-8")
    (output / "compact_summary.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="compact summary"):
        validate_evidence_artifacts(output, external_paths={})


def test_analysis_answers_four_questions_and_marks_synthetic_diagnostic_only() -> None:
    analysis = ROOT / "artifacts/strategic_evidence_closure/analysis.md"
    if not analysis.exists():
        pytest.fail("Task 6 analysis artifact is absent")
    text = analysis.read_text(encoding="utf-8")

    for heading in (
        "Owner portability",
        "Witness sensitivity",
        "State reachability",
        "Cash vacancy",
    ):
        assert heading in text
    assert "DIAGNOSTIC_ONLY" in text
    assert "historical return" in text.lower()
