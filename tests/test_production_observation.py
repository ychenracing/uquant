from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import uquant.atomic_io as atomic_io
from uquant.validation.execution_journal import (
    append_planned,
    execution_journal_checkpoint,
    read_execution_journal,
)

_SCRIPT = Path(__file__).parents[1] / "scripts/production_observation.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("production_observation_cli", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_checkpoint_is_exact_verifiable_and_never_overwritten(tmp_path: Path) -> None:
    module = _load_module()
    account = tmp_path / "account_state.json"
    broker = tmp_path / "broker_snapshot.json"
    journal = tmp_path / "future_holdout_execution_journal.jsonl"
    account.write_bytes(b'{"cash": 100.0}\n')
    broker.write_bytes(b'{"as_of": "2026-08-21"}\n')
    journal.write_bytes(b'{"record_hash": "abc"}\n')

    checkpoint, manifest = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="2026-08-21",
        sources={
            "account.before.json": account,
            "broker_snapshot.json": broker,
            "journal.before.jsonl": journal,
        },
    )

    assert checkpoint == tmp_path / "backups/2026-08-21"
    assert (checkpoint / "account.before.json").read_bytes() == account.read_bytes()
    assert (checkpoint / "broker_snapshot.json").read_bytes() == broker.read_bytes()
    assert (checkpoint / "journal.before.jsonl").read_bytes() == journal.read_bytes()
    assert module.verify_backup_checkpoint(checkpoint) == manifest
    assert json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8")) == manifest

    with pytest.raises(FileExistsError, match="already exists"):
        module.create_backup_checkpoint(
            backup_root=tmp_path / "backups",
            run_id="2026-08-21",
            sources={"account.before.json": account},
        )

    (checkpoint / "account.before.json").write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        module.verify_backup_checkpoint(checkpoint)


def test_completed_and_failed_receipts_are_bound_by_the_backup_manifest(
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    for status in ("COMPLETED", "FAILED"):
        checkpoint, _ = module.create_backup_checkpoint(
            backup_root=tmp_path / "backups",
            run_id=status.lower(),
            sources={"account.before.json": source},
        )
        receipt = {
            "schema_version": 1,
            "run_id": status.lower(),
            "status": status,
        }
        sealed = module.seal_backup_receipt(checkpoint, receipt)
        assert sealed["status"] == status
        assert "receipt.json" in sealed["files"]
        module.verify_backup_checkpoint(checkpoint)

        (checkpoint / "receipt.json").write_text("{}\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r"receipt\.json"):
            module.verify_backup_checkpoint(checkpoint)


def test_post_run_evidence_stages_all_sources_before_mutating_the_checkpoint(
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, prepared = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="staged-failure",
        sources={"account.before.json": source},
    )
    valid = tmp_path / "valid.json"
    valid.write_text('{"valid": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="post-run backup source"):
        module.add_backup_evidence(
            checkpoint,
            {
                "a-valid.json": valid,
                "z-missing.json": tmp_path / "missing.json",
            },
        )

    assert module.verify_backup_checkpoint(checkpoint) == prepared
    assert not (checkpoint / "a-valid.json").exists()
    sealed = module.seal_backup_receipt(
        checkpoint,
        {
            "schema_version": 1,
            "run_id": "staged-failure",
            "status": "FAILED",
        },
    )
    assert sealed["status"] == "FAILED"


def test_post_run_evidence_cannot_mutate_a_finalized_checkpoint(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, _ = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="completed",
        sources={"account.before.json": source},
    )
    module.seal_backup_receipt(
        checkpoint,
        {"schema_version": 1, "run_id": "completed", "status": "COMPLETED"},
    )

    with pytest.raises(ValueError, match="finalized"):
        module.add_backup_evidence(checkpoint, {"late.json": source})
    assert not (checkpoint / "late.json").exists()


def test_post_run_evidence_rolls_back_a_partial_carrier_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, prepared = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="write-failure",
        sources={"account.before.json": source},
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("1\n", encoding="utf-8")
    second.write_text("2\n", encoding="utf-8")
    real_write = module.atomic_write_bytes

    def publish_then_fail_second(destination, payload, **kwargs):
        result = real_write(destination, payload, **kwargs)
        if Path(destination).name == "b-second.json":
            raise OSError("simulated post-publication carrier failure")
        return result

    monkeypatch.setattr(module, "atomic_write_bytes", publish_then_fail_second)
    with pytest.raises(OSError, match="post-publication carrier failure"):
        module.add_backup_evidence(
            checkpoint,
            {"a-first.json": first, "b-second.json": second},
        )

    assert module.verify_backup_checkpoint(checkpoint) == prepared
    assert not (checkpoint / "a-first.json").exists()
    assert not (checkpoint / "b-second.json").exists()


@pytest.mark.parametrize("publication", ["receipt", "manifest"])
def test_receipt_seal_reconciles_post_publication_atomic_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    publication: str,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, _ = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id=publication,
        sources={"account.before.json": source},
    )
    if publication == "receipt":
        real_write = module.atomic_write_bytes

        def publish_receipt_then_fail(destination, payload, **kwargs):
            result = real_write(destination, payload, **kwargs)
            if Path(destination).name == "receipt.json":
                raise OSError("simulated receipt directory fsync failure")
            return result

        monkeypatch.setattr(module, "atomic_write_bytes", publish_receipt_then_fail)
    else:
        real_write_text = module.atomic_write_text

        def publish_manifest_then_fail(destination, text, **kwargs):
            result = real_write_text(destination, text, **kwargs)
            if Path(destination).name == "manifest.json":
                raise OSError("simulated manifest directory fsync failure")
            return result

        monkeypatch.setattr(module, "atomic_write_text", publish_manifest_then_fail)

    sealed = module.seal_backup_receipt(
        checkpoint,
        {"schema_version": 1, "run_id": publication, "status": "FAILED"},
    )
    assert sealed["status"] == "FAILED"
    assert module.verify_backup_checkpoint(checkpoint) == sealed


def test_receipt_seal_retries_a_real_final_manifest_directory_fsync_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, _ = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="durability-retry",
        sources={"account.before.json": source},
    )
    real_directory_fsync = atomic_io._fsync_directory
    checkpoint_fsync_calls = 0

    def fail_final_manifest_fsync(path: Path) -> None:
        nonlocal checkpoint_fsync_calls
        if Path(path) == checkpoint:
            checkpoint_fsync_calls += 1
            if checkpoint_fsync_calls == 2:
                raise OSError("simulated real final manifest fsync failure")
        real_directory_fsync(path)

    retries: list[Path] = []
    real_retry = module._fsync_checkpoint_directory

    def record_retry(path: Path) -> None:
        retries.append(path)
        real_retry(path)

    monkeypatch.setattr(atomic_io, "_fsync_directory", fail_final_manifest_fsync)
    monkeypatch.setattr(module, "_fsync_checkpoint_directory", record_retry)
    sealed = module.seal_backup_receipt(
        checkpoint,
        {"schema_version": 1, "run_id": "durability-retry", "status": "COMPLETED"},
    )

    assert checkpoint_fsync_calls == 2
    assert retries == [checkpoint]
    assert module.verify_backup_checkpoint(checkpoint) == sealed


def test_receipt_seal_fails_closed_when_manifest_directory_fsync_retry_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    checkpoint, _ = module.create_backup_checkpoint(
        backup_root=tmp_path / "backups",
        run_id="durability-failure",
        sources={"account.before.json": source},
    )
    real_directory_fsync = atomic_io._fsync_directory
    checkpoint_fsync_calls = 0

    def fail_final_manifest_fsync(path: Path) -> None:
        nonlocal checkpoint_fsync_calls
        if Path(path) == checkpoint:
            checkpoint_fsync_calls += 1
            if checkpoint_fsync_calls == 2:
                raise OSError("simulated real final manifest fsync failure")
        real_directory_fsync(path)

    monkeypatch.setattr(atomic_io, "_fsync_directory", fail_final_manifest_fsync)
    monkeypatch.setattr(
        module,
        "_fsync_checkpoint_directory",
        lambda _: (_ for _ in ()).throw(OSError("checkpoint fsync retry failed")),
    )

    with pytest.raises(OSError, match="checkpoint fsync retry failed"):
        module.seal_backup_receipt(
            checkpoint,
            {
                "schema_version": 1,
                "run_id": "durability-failure",
                "status": "COMPLETED",
            },
        )


def test_backup_checkpoint_rejects_a_symlinked_parent(tmp_path: Path) -> None:
    module = _load_module()
    source = tmp_path / "account.json"
    source.write_text("{}\n", encoding="utf-8")
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        module.create_backup_checkpoint(
            backup_root=alias / "backups",
            run_id="2026-08-21",
            sources={"account.before.json": source},
        )


def test_run_closes_the_observation_loop_and_archives_pre_and_post_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    data_dir = root / "data/live"
    snapshot_dir = root / "incoming/2026-08-21"
    data_dir.mkdir(parents=True)
    snapshot_dir.mkdir(parents=True)
    account = root / "account_state.json"
    holdout_account = root / "holdout_prior_close_account.json"
    broker = root / "broker_snapshot.json"
    journal = root / "future_holdout_execution_journal.jsonl"
    checkpoint = root / "future_holdout_execution_journal.checkpoint.json"
    account.write_bytes(b'{"account": "before"}\n')
    holdout_account.write_bytes(b'{"holdout": "anchor"}\n')
    broker.write_bytes(b'{"as_of": "2026-08-21"}\n')
    append_planned(
        journal,
        plan_id="plan-1",
        decision_date="2026-08-20",
        recorded_at="2026-08-20T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_weight=0.08,
        planned_price=100.0,
        planned_shares=100,
    )
    journal_checkpoint = execution_journal_checkpoint(read_execution_journal(journal))
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": journal_checkpoint.schema_version,
                "sequence": journal_checkpoint.sequence,
                "record_sha256": journal_checkpoint.record_sha256,
            }
        ),
        encoding="utf-8",
    )

    events: list[str] = []

    def append_snapshot(**kwargs):
        events.append("holdout-append")
        assert kwargs["repository_root"] == root.resolve()
        assert kwargs["snapshot_dir"] == snapshot_dir.resolve()
        return {"session": "2026-08-21", "files": 34, "idempotent": False}

    def replay(**kwargs):
        events.append("holdout-replay")
        output_path = Path(kwargs["output_path"])
        decision_output_path = Path(kwargs["decision_output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        decision_output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"replay": true}\n', encoding="utf-8")
        decision_output_path.write_text(
            '{"decision": true}\n', encoding="utf-8"
        )
        return {"sessions": ["2026-08-21"], "canonical_sha256": "a" * 64}

    def daily(argv):
        events.append("daily")
        assert argv[0] == "daily"
        account_path = Path(argv[argv.index("--account") + 1])
        report_path = Path(argv[argv.index("--output") + 1])
        account_path.write_bytes(b'{"account": "after"}\n')
        report_path.write_text("# Daily Report\n", encoding="utf-8")
        return 0

    def lanes(args):
        events.append("lane-report")
        return {
            "schema_version": 1,
            "observed_sessions": 1,
            "canonical_sha256": "b" * 64,
        }

    monkeypatch.setattr(module, "append_holdout_snapshot", append_snapshot)
    monkeypatch.setattr(module, "generate_future_holdout_replay", replay)
    monkeypatch.setattr(module, "uquant_main", daily)
    monkeypatch.setattr(module, "build_local_lane_report", lanes)

    assert module.main(
        [
            "run",
            "--repository-root",
            str(root),
            "--run-id",
            "2026-08-21",
            "--date",
            "2026-08-21",
            "--symbols",
            "sz300308",
            "--account",
            str(account),
            "--data-dir",
            str(data_dir),
            "--broker-snapshot",
            str(broker),
            "--holdout-snapshot-dir",
            str(snapshot_dir),
            "--holdout-account",
            str(holdout_account),
            "--journal",
            str(journal),
            "--journal-checkpoint",
            str(checkpoint),
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    backup = Path(output["backup_checkpoint"])
    manifest = module.verify_backup_checkpoint(backup)
    assert events == ["holdout-append", "holdout-replay", "daily", "lane-report"]
    assert output["status"] == "COMPLETED"
    assert json.loads((backup / "receipt.json").read_text(encoding="utf-8"))["status"] == "COMPLETED"
    assert (backup / "account.before.json").read_bytes() == b'{"account": "before"}\n'
    assert (backup / "account.after.json").read_bytes() == b'{"account": "after"}\n'
    assert (backup / "daily_report.md").read_text(encoding="utf-8") == "# Daily Report\n"
    assert (backup / "holdout_replay.json").is_file()
    assert (backup / "holdout_decision.json").is_file()
    assert (backup / "lane_report.json").is_file()
    assert (backup / "journal_checkpoint.after.json").is_file()
    assert {
        "account.before.json",
        "account.after.json",
        "broker_snapshot.json",
        "daily_report.md",
        "holdout_account.json",
        "holdout_decision.json",
        "holdout_replay.json",
        "journal.before.jsonl",
        "journal.after.jsonl",
        "journal_checkpoint.before.json",
        "journal_checkpoint.after.json",
        "lane_report.json",
    } <= set(manifest["files"])


def test_run_failure_preserves_the_pre_run_checkpoint_and_writes_a_failed_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    (root / "data/live").mkdir(parents=True)
    (root / "incoming").mkdir()
    account = root / "account_state.json"
    broker = root / "broker_snapshot.json"
    holdout_account = root / "holdout_prior_close_account.json"
    account.write_bytes(b'{"account": "before"}\n')
    broker.write_text("{}\n", encoding="utf-8")
    holdout_account.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "append_holdout_snapshot",
        lambda **_: {"session": "2026-08-21", "idempotent": False},
    )

    def fail_replay(**_):
        raise RuntimeError("deterministic replay failed")

    monkeypatch.setattr(module, "generate_future_holdout_replay", fail_replay)
    monkeypatch.setattr(
        module,
        "uquant_main",
        lambda _: pytest.fail("daily must not run after replay failure"),
    )

    with pytest.raises(RuntimeError, match="deterministic replay failed"):
        module.main(
            [
                "run",
                "--repository-root",
                str(root),
                "--date",
                "2026-08-21",
                "--symbols",
                "sz300308",
                "--account",
                str(account),
                "--data-dir",
                "data/live",
                "--broker-snapshot",
                str(broker),
                "--holdout-snapshot-dir",
                "incoming",
                "--holdout-account",
                str(holdout_account),
            ]
        )

    backup = root / "production_observation_backups/2026-08-21"
    assert module.verify_backup_checkpoint(backup)["run_id"] == "2026-08-21"
    assert (backup / "account.before.json").read_bytes() == b'{"account": "before"}\n'
    assert account.read_bytes() == b'{"account": "before"}\n'
    receipt = json.loads((backup / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "FAILED"
    assert receipt["error_type"] == "RuntimeError"
    assert receipt["steps"][-1] == "holdout_snapshot_appended"


def test_run_rejects_a_holdout_snapshot_for_a_different_daily_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    (root / "data/live").mkdir(parents=True)
    (root / "incoming").mkdir()
    for name in (
        "account_state.json",
        "broker_snapshot.json",
        "holdout_prior_close_account.json",
    ):
        (root / name).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "append_holdout_snapshot",
        lambda **_: {"session": "2026-08-20", "files": 34, "idempotent": False},
    )
    monkeypatch.setattr(
        module,
        "generate_future_holdout_replay",
        lambda **_: pytest.fail("replay must not run for a mismatched session"),
    )

    with pytest.raises(ValueError, match="does not match the daily date"):
        module.main(
            [
                "run",
                "--repository-root",
                str(root),
                "--date",
                "2026-08-21",
                "--symbols",
                "sz300308",
                "--account",
                "account_state.json",
                "--data-dir",
                "data/live",
                "--broker-snapshot",
                "broker_snapshot.json",
                "--holdout-snapshot-dir",
                "incoming",
                "--holdout-account",
                "holdout_prior_close_account.json",
            ]
        )


def _minimal_run_arguments(root: Path, *, run_id: str = "2026-08-21") -> list[str]:
    return [
        "run",
        "--repository-root",
        str(root),
        "--run-id",
        run_id,
        "--date",
        "2026-08-21",
        "--symbols",
        "sz300308",
        "--account",
        "account_state.json",
        "--data-dir",
        "data/live",
        "--broker-snapshot",
        "broker_snapshot.json",
        "--holdout-snapshot-dir",
        "incoming",
        "--holdout-account",
        "holdout_prior_close_account.json",
    ]


def _create_minimal_run_inputs(root: Path) -> None:
    (root / "data/live").mkdir(parents=True)
    (root / "incoming").mkdir()
    for name in (
        "account_state.json",
        "broker_snapshot.json",
        "holdout_prior_close_account.json",
    ):
        (root / name).write_text("{}\n", encoding="utf-8")


def test_run_rejects_outputs_that_overlap_each_other_or_the_backup_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    _create_minimal_run_inputs(root)
    monkeypatch.setattr(
        module,
        "append_holdout_snapshot",
        lambda **_: pytest.fail("preflight must reject before snapshot append"),
    )

    overlapping_outputs = [
        *_minimal_run_arguments(root),
        "--daily-report",
        "artifacts/shared.json",
        "--holdout-replay-output",
        "artifacts/shared.json",
    ]
    with pytest.raises(ValueError, match="output paths overlap"):
        module.main(overlapping_outputs)

    backup_overwrite = [
        *_minimal_run_arguments(root),
        "--daily-report",
        "production_observation_backups/2026-08-21/account.before.json",
    ]
    with pytest.raises(ValueError, match="backup checkpoint"):
        module.main(backup_overwrite)
    assert not (root / "production_observation_backups/2026-08-21").exists()

    checkpoint_overwrites_journal = [
        *_minimal_run_arguments(root),
        "--journal",
        "execution.jsonl",
        "--journal-checkpoint",
        "execution.jsonl",
    ]
    with pytest.raises(ValueError, match="protected path"):
        module.main(checkpoint_overwrites_journal)


def test_run_rejects_hardlinked_output_aliases_before_irreversible_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    _create_minimal_run_inputs(root)
    artifacts = root / "artifacts"
    artifacts.mkdir()
    daily = artifacts / "daily.md"
    replay = artifacts / "replay.json"
    daily.write_text("evidence\n", encoding="utf-8")
    replay.hardlink_to(daily)
    monkeypatch.setattr(
        module,
        "append_holdout_snapshot",
        lambda **_: pytest.fail("preflight must reject before snapshot append"),
    )

    with pytest.raises(ValueError, match="output paths overlap"):
        module.main(
            [
                *_minimal_run_arguments(root),
                "--daily-report",
                str(daily),
                "--holdout-replay-output",
                str(replay),
            ]
        )


def test_run_verifies_the_new_backup_before_snapshot_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    _create_minimal_run_inputs(root)
    real_create = module.create_backup_checkpoint

    def create_and_corrupt(**kwargs):
        checkpoint, manifest = real_create(**kwargs)
        (checkpoint / "account.before.json").write_text("tampered\n", encoding="utf-8")
        return checkpoint, manifest

    monkeypatch.setattr(module, "create_backup_checkpoint", create_and_corrupt)
    monkeypatch.setattr(
        module,
        "append_holdout_snapshot",
        lambda **_: pytest.fail("invalid backup must block snapshot append"),
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        module.main(_minimal_run_arguments(root))


def test_observation_lock_serializes_different_run_ids_for_one_account(
    tmp_path: Path,
) -> None:
    module = _load_module()
    root = tmp_path / "repository"
    root.mkdir()
    account = root / "account.json"
    account.write_text("{}\n", encoding="utf-8")
    ready = root / "child-ready"
    marker = root / "child-acquired"
    code = "\n".join(
        (
            "import importlib.util, pathlib, sys",
            "spec = importlib.util.spec_from_file_location('child_observation', pathlib.Path(sys.argv[1]))",
            "module = importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(module)",
            "pathlib.Path(sys.argv[4]).write_text('ready', encoding='utf-8')",
            "with module._observation_lock(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])):",
            "    pathlib.Path(sys.argv[5]).write_text('acquired', encoding='utf-8')",
        )
    )

    with module._observation_lock(root, account):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(_SCRIPT),
                str(root),
                str(account),
                str(ready),
                str(marker),
            ]
        )
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.read_text(encoding="utf-8") == "ready"
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=0.25)
        assert not marker.exists()
    assert child.wait(timeout=5) == 0
    assert marker.read_text(encoding="utf-8") == "acquired"
