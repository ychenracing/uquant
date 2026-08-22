"""One operator entry point for evidence-only daily production observation."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed git command, never a shell
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from uquant.atomic_io import (
    atomic_write_bytes,
    atomic_write_text,
    validate_atomic_output_boundary,
)
from uquant.cli import main as uquant_main
from uquant.engine import code_fingerprint
from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)
from uquant.validation.holdout_runtime import (
    append_holdout_snapshot,
    generate_future_holdout_replay,
)

_FUTURE_HOLDOUT_SPEC = importlib.util.spec_from_file_location(
    "uquant_production_observation_future_holdout",
    Path(__file__).resolve().with_name("future_holdout.py"),
)
if _FUTURE_HOLDOUT_SPEC is None or _FUTURE_HOLDOUT_SPEC.loader is None:
    raise RuntimeError("cannot load the canonical Future Holdout operations module")
_FUTURE_HOLDOUT = importlib.util.module_from_spec(_FUTURE_HOLDOUT_SPEC)
_FUTURE_HOLDOUT_SPEC.loader.exec_module(_FUTURE_HOLDOUT)
CANONICAL_JOURNAL_CHECKPOINT_PATH: str = _FUTURE_HOLDOUT.CANONICAL_JOURNAL_CHECKPOINT_PATH
CANONICAL_JOURNAL_PATH: str = _FUTURE_HOLDOUT.CANONICAL_JOURNAL_PATH
CANONICAL_LOCAL_LANE_REPORT_PATH: str = _FUTURE_HOLDOUT.CANONICAL_LOCAL_LANE_REPORT_PATH
build_local_lane_report = _FUTURE_HOLDOUT.build_local_lane_report
load_journal_checkpoint = _FUTURE_HOLDOUT.load_journal_checkpoint
read_trusted_execution_journal = _FUTURE_HOLDOUT.read_trusted_execution_journal
write_journal_checkpoint = _FUTURE_HOLDOUT.write_journal_checkpoint

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DEFAULT_BACKUP_ROOT = "production_observation_backups"
_DEFAULT_REPLAY_OUTPUT = "artifacts/future_holdout_replay.json"
_DEFAULT_DECISION_OUTPUT = "artifacts/future_holdout_decision.json"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _manifest_payload(
    *,
    run_id: str,
    status: str,
    files: dict[str, dict[str, object]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "status": status,
        "files": dict(sorted(files.items())),
    }
    payload["canonical_sha256"] = _sha256(_canonical_bytes(payload))
    return payload


def _require_physical_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a physical regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc


def _physical_file_matches(path: Path, payload: bytes) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.read_bytes() == payload
    except OSError:
        return False


def _fsync_checkpoint_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_symlink_chain(path: Path, *, label: str) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise ValueError(f"{label} must not contain a symlink: {current}")
        if current == current.parent:
            return
        current = current.parent


def create_backup_checkpoint(
    *,
    backup_root: str | Path,
    run_id: str,
    sources: dict[str, Path],
) -> tuple[Path, dict[str, Any]]:
    """Copy exact pre-run carriers into one immutable, hash-verifiable directory."""

    if not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("production observation run ID is malformed")
    root = Path(backup_root)
    _reject_symlink_chain(root, label="production observation backup root")
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / run_id
    try:
        checkpoint.mkdir()
    except FileExistsError as exc:
        raise FileExistsError(f"backup checkpoint already exists: {checkpoint}") from exc

    files: dict[str, dict[str, object]] = {}
    try:
        for name, source in sorted(sources.items()):
            if Path(name).name != name or name == "manifest.json":
                raise ValueError(f"backup carrier name is unsafe: {name}")
            payload = _require_physical_file(Path(source), label="backup source")
            destination = checkpoint / name
            atomic_write_bytes(destination, payload)
            files[name] = {
                "sha256": _sha256(payload),
                "size": len(payload),
                "source": str(Path(source).resolve()),
            }
        manifest = _manifest_payload(run_id=run_id, status="PREPARED", files=files)
        atomic_write_text(
            checkpoint / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    except BaseException:
        # Preserve the incomplete checkpoint for forensic recovery; never reuse its run ID.
        raise
    return checkpoint, manifest


def add_backup_evidence(
    checkpoint: str | Path,
    sources: dict[str, Path],
) -> dict[str, Any]:
    """Extend one checkpoint with post-run evidence and reseal its manifest."""

    root = Path(checkpoint)
    manifest = verify_backup_checkpoint(root)
    if manifest["status"] != "PREPARED":
        raise ValueError("cannot add evidence to a finalized backup checkpoint")
    files = dict(manifest["files"])
    staged: dict[str, tuple[bytes, dict[str, object]]] = {}
    for name, source in sorted(sources.items()):
        if Path(name).name != name or name in {"manifest.json", "receipt.json"}:
            raise ValueError(f"backup carrier name is unsafe: {name}")
        if name in files or (root / name).exists():
            raise FileExistsError(f"backup carrier already exists: {name}")
        payload = _require_physical_file(Path(source), label="post-run backup source")
        evidence: dict[str, object] = {
            "sha256": _sha256(payload),
            "size": len(payload),
            "source": str(Path(source).resolve()),
        }
        staged[name] = (payload, evidence)
        files[name] = evidence
    updated = _manifest_payload(
        run_id=str(manifest["run_id"]),
        status="PREPARED",
        files=files,
    )
    attempted: list[tuple[Path, bytes]] = []
    manifest_committed = False
    try:
        for name, (payload, _) in staged.items():
            destination = root / name
            attempted.append((destination, payload))
            atomic_write_bytes(destination, payload)
        atomic_write_text(
            root / "manifest.json",
            json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        manifest_committed = True
        return verify_backup_checkpoint(root)
    except BaseException:
        if not manifest_committed:
            try:
                current = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                manifest_committed = (
                    isinstance(current, dict)
                    and current.get("canonical_sha256") == updated["canonical_sha256"]
                )
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        if not manifest_committed:
            for destination, payload in attempted:
                if _physical_file_matches(destination, payload):
                    with contextlib.suppress(FileNotFoundError):
                        destination.unlink()
        raise


def seal_backup_receipt(
    checkpoint: str | Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Finalize a prepared checkpoint by hash-binding its terminal receipt."""

    root = Path(checkpoint)
    manifest = verify_backup_checkpoint(root)
    if manifest["status"] != "PREPARED":
        raise ValueError("backup checkpoint is already finalized")
    status = receipt.get("status")
    if status not in {"COMPLETED", "FAILED"}:
        raise ValueError("backup receipt must have a terminal status")
    if receipt.get("run_id") != manifest["run_id"]:
        raise ValueError("backup receipt run ID does not match its manifest")
    payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    receipt_path = root / "receipt.json"
    try:
        atomic_write_bytes(
            receipt_path,
            payload,
            protected_paths=(root / "manifest.json",),
        )
    except BaseException:
        if not _physical_file_matches(receipt_path, payload):
            raise
    files = dict(manifest["files"])
    files["receipt.json"] = {
        "sha256": _sha256(payload),
        "size": len(payload),
        "source": "generated",
    }
    finalized = _manifest_payload(
        run_id=str(manifest["run_id"]),
        status=str(status),
        files=files,
    )
    manifest_text = json.dumps(
        finalized,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        atomic_write_text(
            root / "manifest.json",
            manifest_text,
            protected_paths=(receipt_path,),
        )
    except BaseException:
        if _physical_file_matches(root / "manifest.json", manifest_text.encode("utf-8")):
            _fsync_checkpoint_directory(root)
            return verify_backup_checkpoint(root)
        if _physical_file_matches(receipt_path, payload):
            with contextlib.suppress(FileNotFoundError):
                receipt_path.unlink()
        raise
    return verify_backup_checkpoint(root)


def verify_backup_checkpoint(checkpoint: str | Path) -> dict[str, Any]:
    """Read and verify every carrier named by one backup manifest."""

    root = Path(checkpoint)
    manifest_path = root / "manifest.json"
    manifest_bytes = _require_physical_file(manifest_path, label="backup manifest")
    try:
        raw = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is corrupt") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "run_id",
        "status",
        "files",
        "canonical_sha256",
    }:
        raise ValueError("backup manifest schema is malformed")
    seal = raw.pop("canonical_sha256")
    if not isinstance(seal, str) or seal != _sha256(_canonical_bytes(raw)):
        raise ValueError("backup manifest hash mismatch")
    raw["canonical_sha256"] = seal
    files = raw.get("files")
    status_value = raw.get("status")
    if (
        raw.get("schema_version") != 2
        or status_value not in {"PREPARED", "COMPLETED", "FAILED"}
        or not isinstance(files, dict)
    ):
        raise ValueError("backup manifest identity is malformed")
    for name, evidence in files.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(evidence, dict):
            raise ValueError("backup manifest file inventory is malformed")
        payload = _require_physical_file(root / name, label="backup carrier")
        if evidence.get("sha256") != _sha256(payload):
            raise ValueError(f"backup carrier hash mismatch: {name}")
        if evidence.get("size") != len(payload):
            raise ValueError(f"backup carrier size mismatch: {name}")
    receipt_evidence = files.get("receipt.json")
    if status_value == "PREPARED" and receipt_evidence is not None:
        raise ValueError("prepared backup checkpoint cannot contain a receipt")
    if status_value != "PREPARED":
        if not isinstance(receipt_evidence, dict):
            raise ValueError("finalized backup checkpoint requires receipt.json evidence")
        try:
            receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("backup receipt.json is corrupt") from exc
        if not isinstance(receipt, dict) or receipt.get("status") != status_value:
            raise ValueError("backup receipt.json status mismatch")
        if receipt.get("run_id") != raw.get("run_id"):
            raise ValueError("backup receipt.json run ID mismatch")
    expected = {"manifest.json", *files}
    try:
        observed = {path.name for path in root.iterdir()}
    except OSError as exc:
        raise ValueError("cannot inventory backup checkpoint") from exc
    unexpected = observed - expected
    if unexpected:
        raise ValueError(f"backup checkpoint has untracked carriers: {sorted(unexpected)}")
    return raw


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/production_observation.py",
        description="Run one evidence-only uquant production observation cycle",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--repository-root", default=".")
    run.add_argument("--run-id", default=None)
    run.add_argument("--date", required=True)
    run.add_argument("--symbols", nargs="+", required=True)
    run.add_argument("--account", required=True)
    run.add_argument("--data-dir", required=True)
    run.add_argument("--broker-snapshot", required=True)
    run.add_argument("--holdout-snapshot-dir", required=True)
    run.add_argument("--holdout-account", required=True)
    run.add_argument("--journal", default=CANONICAL_JOURNAL_PATH)
    run.add_argument("--journal-checkpoint", default=CANONICAL_JOURNAL_CHECKPOINT_PATH)
    run.add_argument("--daily-report", default=None)
    run.add_argument("--lane-report", default=CANONICAL_LOCAL_LANE_REPORT_PATH)
    run.add_argument("--backup-root", default=_DEFAULT_BACKUP_ROOT)
    run.add_argument("--holdout-replay-output", default=_DEFAULT_REPLAY_OUTPUT)
    run.add_argument("--holdout-decision-output", default=_DEFAULT_DECISION_OUTPUT)
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--checkpoint", required=True)
    return parser


def _rooted(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        canonical_left = left.resolve(strict=False)
        canonical_right = right.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve observation paths: {left}, {right}") from exc
    if (
        canonical_left == canonical_right
        or canonical_left in canonical_right.parents
        or canonical_right in canonical_left.parents
    ):
        return True
    if left.exists() and right.exists():
        try:
            return os.path.samefile(left, right)
        except OSError as exc:
            raise ValueError(f"cannot compare observation paths: {left}, {right}") from exc
    return False


@contextlib.contextmanager
def _observation_lock(root: Path, account: Path) -> Iterator[None]:
    """Serialize the complete observation transaction for one repository/account."""

    identity = f"{root.resolve(strict=False)}\0{account.resolve(strict=False)}".encode()
    lock_name = f"uquant-production-observation-{_sha256(identity)}.lock"
    lock_path = Path(tempfile.gettempdir()) / lock_name
    _reject_symlink_chain(lock_path, label="production observation lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    acquired = False
    primary_error: BaseException | None = None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("production observation lock must be a regular file")
        acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
        acquired = True
        yield
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[tuple[str, BaseException]] = []
        if acquired:
            try:
                release_file_lock(descriptor)
            except BaseException as cleanup_error:
                cleanup_errors.append(("lock cleanup", cleanup_error))
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            cleanup_errors.append(("descriptor cleanup", cleanup_error))
        if primary_error is not None:
            for label, failure in cleanup_errors:
                primary_error.add_note(
                    f"production observation {label} also failed: "
                    f"{type(failure).__name__}: {failure}"
                )
        elif cleanup_errors:
            _, first_failure = cleanup_errors[0]
            for later_label, later_error in cleanup_errors[1:]:
                first_failure.add_note(
                    f"production observation {later_label} also failed: "
                    f"{type(later_error).__name__}: {later_error}"
                )
            raise first_failure


def _require_physical_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be a physical directory: {path}")


def _git_head(root: Path) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )  # nosec B603
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _preflight_run(args: argparse.Namespace) -> dict[str, Path | str]:
    root = Path(args.repository_root).resolve()
    try:
        run_date = date.fromisoformat(args.date).isoformat()
    except ValueError as exc:
        raise ValueError("production observation date must be canonical ISO-8601") from exc
    if run_date != args.date:
        raise ValueError("production observation date must be canonical ISO-8601")
    run_id = args.run_id or run_date
    if not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("production observation run ID is malformed")
    paths: dict[str, Path | str] = {
        "root": root,
        "run_id": run_id,
        "account": _rooted(root, args.account),
        "data_dir": _rooted(root, args.data_dir),
        "broker": _rooted(root, args.broker_snapshot),
        "snapshot_dir": _rooted(root, args.holdout_snapshot_dir),
        "holdout_account": _rooted(root, args.holdout_account),
        "journal": _rooted(root, args.journal),
        "journal_checkpoint": _rooted(root, args.journal_checkpoint),
        "daily_report": _rooted(root, args.daily_report or f"daily_report_{run_date}.md"),
        "lane_report": _rooted(root, args.lane_report),
        "backup_root": _rooted(root, args.backup_root),
        "replay_output": _rooted(root, args.holdout_replay_output),
        "decision_output": _rooted(root, args.holdout_decision_output),
    }
    for key in ("account", "broker", "holdout_account"):
        _require_physical_file(Path(paths[key]), label=key.replace("_", " "))
    for key in ("data_dir", "snapshot_dir"):
        _require_physical_directory(Path(paths[key]), label=key.replace("_", " "))
    protected_paths = tuple(
        Path(paths[key])
        for key in ("account", "broker", "holdout_account", "journal", "journal_checkpoint")
    )
    protected_roots = (Path(paths["data_dir"]), Path(paths["snapshot_dir"]))
    for key in ("daily_report", "lane_report", "replay_output", "decision_output"):
        validate_atomic_output_boundary(
            Path(paths[key]),
            protected_paths=protected_paths,
            protected_roots=protected_roots,
        )
    validate_atomic_output_boundary(
        Path(paths["journal_checkpoint"]),
        protected_paths=tuple(
            Path(paths[key])
            for key in ("account", "broker", "holdout_account", "journal")
        ),
        protected_roots=protected_roots,
    )
    backup_root = Path(paths["backup_root"])
    if any(
        backup_root == protected or protected in backup_root.parents
        for protected in protected_roots
    ):
        raise ValueError("production observation backups cannot be inside market inputs")
    backup_checkpoint = backup_root / run_id
    output_keys = (
        "daily_report",
        "lane_report",
        "replay_output",
        "decision_output",
        "journal_checkpoint",
    )
    for index, left_key in enumerate(output_keys):
        left = Path(paths[left_key])
        for right_key in output_keys[index + 1 :]:
            right = Path(paths[right_key])
            if _paths_overlap(left, right):
                raise ValueError(
                    f"production observation output paths overlap: {left_key}, {right_key}"
                )
        if _paths_overlap(left, backup_root) or _paths_overlap(left, backup_checkpoint):
            raise ValueError(
                f"production observation output overlaps backup checkpoint: {left_key}"
            )
    paths["backup_checkpoint"] = backup_checkpoint
    return paths


def run_production_observation(args: argparse.Namespace) -> dict[str, Any]:
    """Run the canonical observation sequence without feeding evidence back to decisions."""

    paths = _preflight_run(args)
    root = Path(paths["root"])
    account = Path(paths["account"])
    with _observation_lock(root, account):
        # Re-evaluate identities after acquiring the stable transaction lock.
        paths = _preflight_run(args)
        broker = Path(paths["broker"])
        holdout_account = Path(paths["holdout_account"])
        journal = Path(paths["journal"])
        journal_checkpoint = Path(paths["journal_checkpoint"])
        read_trusted_execution_journal(journal, journal_checkpoint)
        steps: list[str] = ["journal_verified"]
        before = {
            "account.before.json": account,
            "broker_snapshot.json": broker,
            "holdout_account.json": holdout_account,
        }
        if journal.exists():
            before["journal.before.jsonl"] = journal
        if journal_checkpoint.exists():
            before["journal_checkpoint.before.json"] = journal_checkpoint
        backup, _ = create_backup_checkpoint(
            backup_root=Path(paths["backup_root"]),
            run_id=str(paths["run_id"]),
            sources=before,
        )
        steps.append("backup_created")
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "run_id": paths["run_id"],
            "date": args.date,
            "repository_head": _git_head(root),
            "production_code_sha256": code_fingerprint(),
            "backup_checkpoint": str(backup.resolve()),
            "steps": list(steps),
        }
        try:
            prepared = verify_backup_checkpoint(backup)
            if prepared["status"] != "PREPARED":
                raise ValueError("new backup checkpoint is not prepared")
            steps.append("backup_verified")
            append_result = append_holdout_snapshot(
                repository_root=root,
                snapshot_dir=Path(paths["snapshot_dir"]),
            )
            steps.append("holdout_snapshot_appended")
            if append_result.get("session") != args.date:
                raise ValueError(
                    "appended holdout session does not match the daily date: "
                    f"{append_result.get('session')} != {args.date}"
                )
            replay = generate_future_holdout_replay(
                repository_root=root,
                account_path=holdout_account,
                output_path=Path(paths["replay_output"]),
                decision_output_path=Path(paths["decision_output"]),
                journal_path=journal,
            )
            steps.append("holdout_replay_generated")
            daily_arguments = [
                "daily",
                "--symbols",
                *args.symbols,
                "--date",
                args.date,
                "--account",
                str(account),
                "--data-dir",
                str(paths["data_dir"]),
                "--broker-snapshot",
                str(broker),
                "--output",
                str(paths["daily_report"]),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                daily_status = uquant_main(daily_arguments)
            if daily_status != 0:
                raise RuntimeError(f"uquant daily returned status {daily_status}")
            steps.append("account_synced_and_daily_generated")
            lane_args = argparse.Namespace(
                repository_root=str(root),
                registry="benchmarks/future_holdout_lane_registry.json",
            )
            lane_report = build_local_lane_report(lane_args)
            atomic_write_text(
                Path(paths["lane_report"]),
                json.dumps(lane_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                protected_paths=(journal, account, holdout_account),
            )
            steps.append("local_lane_report_generated")
            current_checkpoint = write_journal_checkpoint(journal, journal_checkpoint)
            steps.append("journal_checkpoint_written")
            after = {
                "account.after.json": account,
                "daily_report.md": Path(paths["daily_report"]),
                "holdout_decision.json": Path(paths["decision_output"]),
                "holdout_replay.json": Path(paths["replay_output"]),
                "journal_checkpoint.after.json": journal_checkpoint,
                "lane_report.json": Path(paths["lane_report"]),
            }
            if journal.exists():
                after["journal.after.jsonl"] = journal
            add_backup_evidence(backup, after)
            steps.append("post_run_evidence_archived")
            receipt.update(
                {
                    "status": "COMPLETED",
                    "steps": list(steps),
                    "holdout_append": append_result,
                    "holdout_replay_sha256": replay.get("canonical_sha256"),
                    "observed_sessions": lane_report.get("observed_sessions"),
                    "journal_checkpoint": asdict(current_checkpoint),
                }
            )
            seal_backup_receipt(backup, receipt)
            return receipt
        except BaseException as exc:
            receipt.update(
                {
                    "status": "FAILED",
                    "steps": list(steps),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            try:
                seal_backup_receipt(backup, receipt)
            except BaseException as receipt_error:
                exc.add_note(
                    "production observation receipt also failed: "
                    f"{type(receipt_error).__name__}: {receipt_error}"
                )
            raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-backup":
        payload = {
            "status": "VALID",
            "manifest": verify_backup_checkpoint(args.checkpoint),
        }
    else:
        payload = run_production_observation(args)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
