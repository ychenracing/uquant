from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from research.strategic_evidence.models import canonical_sha256
from research.strategic_evidence.provenance import seal_payload, write_gzip_shard
from research.strategic_evidence.report import (
    _forced_owner_answer,
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
    source = (ROOT / "scripts/run_strategic_evidence_closure.py").read_text(encoding="utf-8")
    assert "/tmp/uquant-strategic-evidence" not in source


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
        "checkpoint3_forced_owner_manifest.json",
        "checkpoint4_witness_ablation_manifest.json",
    ):
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, source_dir / name)
    sentinels = {
        name: f"preexisting {name}\n"
        for name in (
            "README.md",
            "analysis.md",
            "compact_summary.json",
            "evidence_manifest.json",
            "SHA256SUMS",
        )
    }
    for name, content in sentinels.items():
        (source_dir / name).write_text(content, encoding="utf-8")
    runtime = tmp_path / "runtime"

    assert (
        main(
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
        )
        == 0
    )

    assert {name: (source_dir / name).read_text(encoding="utf-8") for name in sentinels} == sentinels


def test_workflow_is_manual_non_blocking_and_uses_pinned_actions() -> None:
    workflow = (ROOT / ".github/workflows/strategic-evidence-closure.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "continue-on-error: true" in workflow
    assert "id: execute" in workflow
    assert "steps.execute.outcome == 'success'" in workflow
    assert "failed-run.json" in workflow
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
    with pytest.raises(ValueError, match=r"Task 5|external shard"):
        validate_evidence_artifacts(output, external_paths={"task5": external})

    assert assembled["runner_success"] is False  # fixture shard intentionally mismatches Task 5
    manifest = json.loads((output / "evidence_manifest.json").read_text(encoding="utf-8"))
    task5 = manifest["external_shards"]["task5"]
    assert task5["logical_path"].startswith("artifacts/strategic_evidence_closure/external/")
    assert task5["available_for_current_readback"] is True
    assert task5["current_readback_verified"] is False
    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "sealed expected identity" in readme
    assert "assembly-time readback" in readme
    assert str(tmp_path) not in json.dumps(manifest, sort_keys=True)


def test_absent_task5_shard_is_not_reported_as_read_back(tmp_path: Path) -> None:
    output = tmp_path / "artifacts"
    assembled = assemble_evidence_artifacts(
        root=ROOT,
        output_dir=output,
        source_paths={
            "task3": ROOT / "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json",
            "task4": ROOT / "artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_full.json",
            "task5": ROOT
            / "artifacts/strategic_evidence_closure/checkpoint5_state_reachability_summary.json",
        },
        task5_shard=None,
        dry_run=True,
    )

    compact = json.loads((output / "compact_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "evidence_manifest.json").read_text(encoding="utf-8"))
    assert assembled["runner_success"] is False
    assert compact["evidence_integrity"]["task5_shard_readback"] is False
    assert manifest["external_shards"]["task5"]["available_for_current_readback"] is False
    assert manifest["external_shards"]["task5"]["current_readback_verified"] is False


def test_supplied_bad_task4_shard_fails_evidence_integrity(tmp_path: Path) -> None:
    shard = tmp_path / "task4.jsonl.gz"
    shard.write_bytes(b"not-a-task4-shard")
    output = tmp_path / "artifacts"

    assemble_evidence_artifacts(
        root=ROOT,
        output_dir=output,
        source_paths={
            "task3": ROOT / "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json",
            "task4": ROOT / "artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_full.json",
            "task5": ROOT
            / "artifacts/strategic_evidence_closure/checkpoint5_state_reachability_summary.json",
        },
        task4_shard=shard,
        task5_shard=None,
        dry_run=True,
    )

    compact = json.loads((output / "compact_summary.json").read_text(encoding="utf-8"))
    assert compact["evidence_integrity"]["task4_supplied_shard_readback"] is False
    assert compact["readback_errors"]["task4"] != "NOT_SUPPLIED"


def test_self_sealed_partial_task4_search_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source_dir = root / "artifacts/strategic_evidence_closure"
    source_dir.mkdir(parents=True)
    (root / "benchmarks").mkdir()
    shutil.copy2(
        ROOT / "benchmarks/strategic_evidence_closure_contract.json",
        root / "benchmarks/strategic_evidence_closure_contract.json",
    )
    source_paths = {}
    for task, name in {
        "task3": "checkpoint3_forced_owner_full.json",
        "task4": "checkpoint4_witness_ablation_full.json",
        "task5": "checkpoint5_state_reachability_summary.json",
    }.items():
        target = source_dir / name
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, target)
        source_paths[task] = target
    for name in (
        "checkpoint3_forced_owner_manifest.json",
        "checkpoint4_witness_ablation_manifest.json",
    ):
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, source_dir / name)
    task4 = json.loads(source_paths["task4"].read_text(encoding="utf-8"))
    task4["search_cells"].pop()
    source_paths["task4"].write_text(json.dumps(seal_payload(task4), sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="Task 4"):
        assemble_evidence_artifacts(
            root=root,
            output_dir=tmp_path / "output",
            source_paths=source_paths,
            task5_shard=None,
            dry_run=True,
        )


def test_jointly_resealed_task4_witness_claims_are_recomputed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source_dir = root / "artifacts/strategic_evidence_closure"
    source_dir.mkdir(parents=True)
    (root / "benchmarks").mkdir()
    shutil.copy2(
        ROOT / "benchmarks/strategic_evidence_closure_contract.json",
        root / "benchmarks/strategic_evidence_closure_contract.json",
    )
    source_paths = {}
    for task, name in {
        "task3": "checkpoint3_forced_owner_full.json",
        "task4": "checkpoint4_witness_ablation_full.json",
        "task5": "checkpoint5_state_reachability_summary.json",
    }.items():
        target = source_dir / name
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, target)
        source_paths[task] = target
    shutil.copy2(
        ROOT / "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_manifest.json",
        source_dir / "checkpoint3_forced_owner_manifest.json",
    )
    task4 = json.loads(source_paths["task4"].read_text(encoding="utf-8"))
    task4["minimal_witness_sets"] = [["fabricated"]]
    task4 = seal_payload(task4)
    source_paths["task4"].write_text(json.dumps(task4, sort_keys=True), encoding="utf-8")
    manifest_path = source_dir / "checkpoint4_witness_ablation_manifest.json"
    manifest = json.loads(
        (ROOT / "artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["summary"] = {
        **manifest["summary"],
        "byte_size": source_paths["task4"].stat().st_size,
        "bytes_sha256": hashlib.sha256(source_paths["task4"].read_bytes()).hexdigest(),
        "payload_sha256": task4["payload_sha256"],
    }
    manifest_path.write_text(json.dumps(seal_payload(manifest), sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="Task 4"):
        assemble_evidence_artifacts(
            root=root,
            output_dir=tmp_path / "output",
            source_paths=source_paths,
            task5_shard=None,
            dry_run=True,
        )


def test_resealed_task5_source_and_cell_linkage_mutation_fails_closed(tmp_path: Path) -> None:
    contract = json.loads(
        (ROOT / "benchmarks/strategic_evidence_closure_contract.json").read_text(encoding="utf-8")
    )
    identity_fields = (
        "config_sha256",
        "data_manifest_sha256",
        "industry_mapping_sha256",
        "production_source_sha256",
        "universe_sha256",
        "uv_lock_sha256",
        "window_sha256",
    )
    provenance = {
        "base_commit": contract["base_commit"],
        "experiment_commit": "d" * 40,
        **{field: contract["identities"][field] for field in identity_fields},
        "research_source_sha256": "e" * 64,
        "scenario_sha256": "f" * 64,
        "python": contract["runtime"]["python"],
        "numpy": contract["runtime"]["numpy"],
        "pandas": contract["runtime"]["pandas"],
        "uv": contract["runtime"]["uv"],
        "generated_at": "2026-08-27T00:00:00Z",
    }
    rows = [
        seal_payload(
            {
                "cell_id": canonical_sha256({"state_id": state_id, "path_id": path_id}),
                "state_id": state_id,
                "path_id": path_id,
                "state_source": "BOGUS",
                "path_source": "SYNTHETIC",
                "status": "SUCCESS",
                "evidence_class": "DIAGNOSTIC_ONLY",
                "observation_count": 1,
                "input_bindings": {
                    "state_account_sha256": "1" * 64,
                    "state_dimensions_sha256": "2" * 64,
                    "state_provenance_sha256": "3" * 64,
                    "path_scenario_sha256": "4" * 64,
                    "path_provenance_sha256": "5" * 64,
                    "path_bars_sha256": "6" * 64,
                    "cell_scenario_sha256": "7" * 64,
                },
                "analysis": {},
                "analysis_sha256": canonical_sha256({}),
                "error": None,
            }
        )
        for state_id in contract["matrix"]["initial_state_ids"]
        for path_id in contract["matrix"]["path_ids"]
    ]
    shard = tmp_path / "task5.jsonl.gz"
    write_gzip_shard(shard, rows=rows, provenance=provenance)
    root = tmp_path / "repo"
    source_dir = root / "artifacts/strategic_evidence_closure"
    source_dir.mkdir(parents=True)
    (root / "benchmarks").mkdir()
    shutil.copy2(
        ROOT / "benchmarks/strategic_evidence_closure_contract.json",
        root / "benchmarks/strategic_evidence_closure_contract.json",
    )
    source_paths = {}
    for task, name in {
        "task3": "checkpoint3_forced_owner_full.json",
        "task4": "checkpoint4_witness_ablation_full.json",
    }.items():
        target = source_dir / name
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, target)
        source_paths[task] = target
    for name in (
        "checkpoint3_forced_owner_manifest.json",
        "checkpoint4_witness_ablation_manifest.json",
    ):
        shutil.copy2(ROOT / "artifacts/strategic_evidence_closure" / name, source_dir / name)
    task5 = seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-reachability-summary.v1",
            "evidence_class": "DIAGNOSTIC_ONLY",
            "experiment_commit": provenance["experiment_commit"],
            "research_source_sha256": provenance["research_source_sha256"],
            "scenario_sha256": provenance["scenario_sha256"],
            "cell_count": 84,
            "statuses": {"SUCCESS": 84},
            "output_bytes_sha256": hashlib.sha256(shard.read_bytes()).hexdigest(),
            "output_byte_size": shard.stat().st_size,
            "output_row_count": 84,
            "synthetic_historical_return_claims": "FORBIDDEN",
        }
    )
    task5_path = source_dir / "checkpoint5_state_reachability_summary.json"
    task5_path.write_text(json.dumps(task5, sort_keys=True), encoding="utf-8")
    source_paths["task5"] = task5_path

    output = tmp_path / "output"
    result = assemble_evidence_artifacts(
        root=root,
        output_dir=output,
        source_paths=source_paths,
        task5_shard=shard,
        dry_run=True,
    )
    compact = json.loads((output / "compact_summary.json").read_text(encoding="utf-8"))
    assert result["runner_success"] is False
    assert compact["evidence_integrity"]["task5_shard_readback"] is False
    assert compact["readback_errors"]["task5"].startswith("Task 5")


def test_owner_portability_requires_common_and_native_success() -> None:
    cells = [
        {
            "cell_id": f"CONTROL:{index}:{mode}",
            "status": "REPLAY_ERROR" if mode == "COMMON_ACTIVATION_DATE" else "SUCCESS",
        }
        for index in range(8)
        for mode in ("COMMON_ACTIVATION_DATE", "NATIVE_ELIGIBILITY_DATE")
    ]

    answer = _forced_owner_answer({"cells": cells}, evidence_valid=True)

    assert answer["answer"] == "NOT_ESTABLISHED"


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
                "bytes_sha256": hashlib.sha256((output / "compact_summary.json").read_bytes()).hexdigest()
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
