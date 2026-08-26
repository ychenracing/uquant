from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from test_generalization_ablation import (
    ROOT,
    _runner_module,
    _single_cell_worker,
)

from research.ablation_registry import (
    DEFAULT_ABLATION_REGISTRY_PATH,
    MINIMAL_ABLATION_REGISTRY_PATH,
    ContractCell,
    load_ablation_registry,
)


def test_aggregate_authenticates_invalid_results_without_claiming_complete() -> None:
    """Catches hiding invalid experiments from coverage or counting them as valid results."""
    runner = _runner_module()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    valid = {
        item.experiment_id: {
            "experiment_id": item.experiment_id,
            "kind": "experiment",
            "variant_worker_artifact": {"payload_sha256": f"{index + 1:064x}"},
        }
        for index, item in enumerate(registry.experiments[:11])
    }
    invalid = {
        item.experiment_id: {
            "experiment_id": item.experiment_id,
            "kind": "invalid_experiment",
            "reason": "no_behavior_divergence",
            "coverage_complete": True,
            "variant_worker_artifact": {"payload_sha256": f"{index + 12:064x}"},
        }
        for index, item in enumerate(registry.experiments[11:])
    }

    summary = runner._evidence_coverage(registry, valid=valid, invalid=invalid)

    assert summary["coverage_complete"] is True
    assert summary["complete"] is False
    assert summary["valid_experiment_count"] == 11
    assert summary["invalid_experiment_count"] == 2
    assert summary["missing_experiment_ids"] == []
    assert set(summary["invalid_experiments"]) == set(invalid)

def test_frozen_replay_error_anchor_rejects_resealed_message_mutation() -> None:
    """Catches preserving only the frozen REPLAY_ERROR status while rewriting its exception."""
    runner = _runner_module()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    anchors = runner._frozen_replay_error_anchors(registry, source_root=ROOT)
    identity = ("ai_era_generalization", "continuous_ai_era/random__20__0000")
    anchor = anchors[identity]
    schedule = (
        ContractCell(
            contract=identity[0],
            cell_id=identity[1],
            status="REPLAY_ERROR",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2026-08-05",
        ),
    )
    provenance = {"effective_config_sha256": "d" * 64}
    payload = {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": "b" * 64,
        "experiment_id": "baseline",
        "cells": [
            {
                "contract": identity[0],
                "cell_id": identity[1],
                "frozen_status": "REPLAY_ERROR",
                "status": "REPLAY_ERROR",
                "economic": True,
                "metrics": None,
                "replay_error": {
                    **anchor,
                    "contract": identity[0],
                    "cell_id": identity[1],
                    "binding_sha256": "b" * 64,
                    "carrier_sha256": runner._BASELINE_CARRIER_SHA256,
                    "provenance_sha256": runner._sha256_mapping(provenance),
                },
                "raw_result_sha256": None,
            }
        ],
        "traces": {f"{identity[0]}/{identity[1]}": []},
        "provenance": provenance,
    }
    runner._validate_worker_payload(
        payload,
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="baseline",
        frozen_replay_errors=anchors,
    )
    rewritten = copy.deepcopy(payload)
    rewritten["cells"][0]["replay_error"]["message"] += " rewritten"
    with pytest.raises(ValueError, match="frozen replay error anchor"):
        runner._validate_worker_payload(
            rewritten,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
            frozen_replay_errors=anchors,
        )

def test_replay_command_materializes_exact_historical_evidence_commit(tmp_path: Path) -> None:
    """Catches later report-only HEADs replaying from the wrong source checkout."""
    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    command = runner._replay_command(
        repository_root=ROOT,
        evidence_commit=evidence_commit,
        registry_relative=Path("artifacts/phase2/ablations/registry.json"),
        data_dir=ROOT / "data" / "frozen",
        experiment_id="without_sector_guard",
        checkpoint_dir=tmp_path,
        output=tmp_path / "progress.json",
    )
    assert command[2] == "replay"
    assert command[command.index("--evidence-commit") + 1] == evidence_commit
    with runner._isolated_evidence_checkout(ROOT, evidence_commit) as checkout:
        assert runner._git_output(checkout, "rev-parse", "HEAD") == evidence_commit
        assert runner._git_output(checkout, "status", "--porcelain", "--untracked-files=all") == ""
    wrong_commit = list(command)
    wrong_commit[wrong_commit.index("--evidence-commit") + 1] = "0" * 40
    with pytest.raises(ValueError, match="evidence commit"):
        runner._validate_replay_command(wrong_commit, expected=command)

def test_evidence_checkout_rejects_a_clean_post_replay_commit_switch(
    tmp_path: Path,
) -> None:
    """Catches historical evidence attributed after a clean checkout reset."""

    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    current = runner._git_output(ROOT, "rev-parse", "HEAD")
    assert current != evidence_commit

    with (
        pytest.raises(ValueError, match="changed during replay"),
        runner._isolated_evidence_checkout(ROOT, evidence_commit) as checkout,
    ):
        subprocess.run(
            ["git", "-C", str(checkout), "reset", "--hard", current],
            check=True,
            capture_output=True,
            text=True,
        )

def test_evidence_checkout_cleanup_does_not_relabel_a_replay_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches cleanup spawn failures masking or relabeling the caller's error."""

    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    original_run = runner.subprocess.run

    with (
        pytest.raises(OSError, match="caller replay failure"),
        runner._isolated_evidence_checkout(ROOT, evidence_commit),
    ):
        def fail_remove(command: list[str], **kwargs: object) -> object:
            if "remove" in command:
                raise OSError("cleanup spawn failure")
            return original_run(command, **kwargs)

        monkeypatch.setattr(runner.subprocess, "run", fail_remove)
        raise OSError("caller replay failure")

@pytest.mark.parametrize("mutation", ["economics", "trace"])
def test_manifest_anchor_rejects_fully_resealed_worker_attack(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Catches a raw worker, comparison, and checkpoint that are re-signed together."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[0]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    _, variant = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id=experiment.experiment_id,
        carrier_sha256=experiment.carrier.sha256,
        stage_hash="2" * 64,
        wealth=1.1,
    )
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    experiment_path = runner._write_experiment_result(
        checkpoint_dir=tmp_path,
        experiment=experiment,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        variant_worker=variant,
        replay_command=replay_command,
        expected_variant_provenance=variant["provenance"],
        frozen_replay_errors={},
    )
    authentic_envelope = json.loads(experiment_path.read_text(encoding="utf-8"))
    authentic_payload = authentic_envelope["payload"]
    authentic_raw_path = tmp_path / authentic_payload["variant_worker_artifact"]["path"]
    trusted_entry = {
        "experiment_id": experiment.experiment_id,
        "artifact": {
            "path": experiment_path.name,
            "kind": "experiment",
            "file_sha256": hashlib.sha256(experiment_path.read_bytes()).hexdigest(),
            "payload_sha256": authentic_envelope["payload_sha256"],
        },
        "raw": {
            "path": authentic_raw_path.relative_to(tmp_path).as_posix(),
            "file_sha256": hashlib.sha256(authentic_raw_path.read_bytes()).hexdigest(),
            "canonical_worker_sha256": hashlib.sha256(runner._canonical_bytes(variant)).hexdigest(),
        },
    }

    forged = copy.deepcopy(variant)
    if mutation == "economics":
        forged["cells"][0]["metrics"]["final_wealth"] += 123.0
        assert forged["cells"][0]["raw_result_sha256"] == variant["cells"][0]["raw_result_sha256"]
    else:
        forged["traces"]["phase1_performance/a/h1_2023"][0]["stages"]["risk"] = "3" * 64
    forged_reference = runner._write_worker_artifact(tmp_path, forged)
    forged_payload = copy.deepcopy(authentic_payload)
    forged_payload["variant_worker_artifact"] = forged_reference
    forged_payload["comparison"] = runner._compare_worker_payloads(
        baseline_worker,
        forged,
        require_divergence=False,
    )
    forged_payload["execution_pass"] = forged_payload["comparison"]["execution_pass"]
    runner._write_checkpoint(experiment_path, forged_payload)

    with pytest.raises(ValueError, match=r"evidence manifest .* hash differs"):
        runner._validate_evidence_manifest_entry(tmp_path, trusted_entry)

def test_tracked_manifest_rejects_edit_and_self_resign(tmp_path: Path) -> None:
    """Catches trusting a manifest hash recomputed from the edited manifest itself."""
    runner = _runner_module()
    manifest = runner._load_trusted_evidence_manifest()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    compiled = runner._compile_evidence_manifest(
        manifest,
        registry=registry,
        evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
        binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    assert [row["experiment_id"] for row in compiled["entries"]] == [
        "baseline",
        *(item.experiment_id for item in registry.experiments),
    ]

    envelope = json.loads(runner._EVIDENCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    envelope["payload"]["entries"][1]["raw"]["file_sha256"] = "0" * 64
    envelope["payload_sha256"] = hashlib.sha256(runner._canonical_bytes(envelope["payload"])).hexdigest()
    rewritten = tmp_path / "evidence_manifest.json"
    rewritten.write_bytes(runner._canonical_bytes(envelope))

    with pytest.raises(ValueError, match="trusted digest"):
        runner._load_trusted_evidence_manifest(rewritten)

def test_trusted_evidence_manifest_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Catches last-key-wins JSON preserving a compiled canonical mapping digest."""

    runner = _runner_module()
    encoded = runner._EVIDENCE_MANIFEST_PATH.read_text(encoding="utf-8")
    duplicated = encoded.replace(
        '"schema_version": 1',
        '"schema_version": 999,\n  "schema_version": 1',
        1,
    )
    assert duplicated != encoded
    path = tmp_path / "duplicate-evidence-manifest.json"
    path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key"):
        runner._load_trusted_evidence_manifest(path)

def test_historical_and_post_deletion_manifests_are_distinct_trust_roots() -> None:
    """Catches selecting or cross-accepting evidence from the other registry epoch."""
    runner = _runner_module()
    historical_path, historical_digest = runner._evidence_manifest_anchor(
        Path("artifacts/phase2/ablations/registry.json")
    )
    minimal_path, minimal_digest = runner._evidence_manifest_anchor(
        Path("artifacts/phase2/ablations/minimal_registry.json")
    )

    assert historical_path == runner._EVIDENCE_MANIFEST_PATH
    assert historical_digest == runner._EVIDENCE_MANIFEST_CANONICAL_SHA256
    assert minimal_path == runner._MINIMAL_EVIDENCE_MANIFEST_PATH
    assert minimal_digest == runner._MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256
    assert historical_path != minimal_path
    assert historical_digest != minimal_digest

    historical = runner._load_trusted_evidence_manifest(
        historical_path,
        trusted_digest=historical_digest,
    )
    minimal = runner._load_trusted_evidence_manifest(
        minimal_path,
        trusted_digest=minimal_digest,
    )
    historical_registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    minimal_registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    runner._compile_evidence_manifest(
        historical,
        registry=historical_registry,
        evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
        binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    runner._compile_evidence_manifest(
        minimal,
        registry=minimal_registry,
        evidence_commit="aa4b313e000002adae27b32f91b5a84425c78987",
        binding_sha256="bc5c82c7f3b3a0ba28f6965f90160aca4aa78d12fe6c375c20b4207cf653fb74",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    with pytest.raises(ValueError, match="manifest binding differs"):
        runner._compile_evidence_manifest(
            minimal,
            registry=historical_registry,
            evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
            binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
            schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
        )

@pytest.mark.parametrize("root_kind", ["schema1", "malformed"])
def test_invalid_writer_rejects_any_existing_root_artifact(
    tmp_path: Path,
    root_kind: str,
) -> None:
    """Catches an invalid result coexisting with a legacy or malformed root artifact."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[4]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    variant = copy.deepcopy(baseline)
    variant["experiment_id"] = experiment.experiment_id
    variant["provenance"] = {
        **baseline["provenance"],
        "checkout": {"carrier_sha256": experiment.carrier.sha256},
    }
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    root_path = tmp_path / f"{experiment.experiment_id}.json"
    if root_kind == "schema1":
        runner._write_checkpoint(
            root_path,
            {
                "schema_version": 1,
                "kind": "experiment",
                "binding_sha256": binding_sha256,
            },
        )
    else:
        root_path.write_bytes(b"{malformed")

    with pytest.raises(ValueError, match="both standard and invalid artifacts"):
        runner._write_experiment_result(
            checkpoint_dir=tmp_path,
            experiment=experiment,
            binding_sha256=binding_sha256,
            schedule=schedule,
            baseline_checkpoint=baseline_checkpoint,
            baseline_worker=baseline_worker,
            variant_worker=variant,
            replay_command=replay_command,
            expected_variant_provenance=variant["provenance"],
            frozen_replay_errors={},
        )

@pytest.mark.parametrize("root_kind", ["schema1", "malformed"])
def test_invalid_reader_rejects_any_existing_root_artifact(
    tmp_path: Path,
    root_kind: str,
) -> None:
    """Catches readback ignoring a legacy or malformed root artifact beside invalid."""
    runner = _runner_module()
    experiment_id = "without_challenger_scout"
    invalid_path = tmp_path / "invalid" / f"{experiment_id}.json"
    invalid_path.parent.mkdir()
    invalid_path.write_text("invalid artifact placeholder", encoding="utf-8")
    root_path = tmp_path / f"{experiment_id}.json"
    if root_kind == "schema1":
        runner._write_checkpoint(
            root_path,
            {
                "schema_version": 1,
                "kind": "experiment",
                "binding_sha256": "a" * 64,
            },
        )
    else:
        root_path.write_bytes(b"{malformed")

    with pytest.raises(ValueError, match="both standard and invalid artifacts"):
        runner._select_experiment_result_path(tmp_path, experiment_id)
