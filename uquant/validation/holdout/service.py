"""Top-level future-holdout manifest, append, and replay orchestration."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import hashlib
import json
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from ...atomic_io import atomic_write_text
from .artifact_transaction import (
    _artifact_bundle_lock,
    _artifact_bundle_lock_path,
    _artifact_bundle_lock_paths,
    _artifact_snapshots,
    _canonical_carrier_path,
    _read_protected_artifact,
    _reject_authoritative_output_paths,
    _reject_output_in_protected_data,
    _resolved_path_text,
    _restore_artifact_snapshots,
)
from .checkpoints import (
    _checkpoint_payload,
    _read_checkpoint_carrier,
    _validate_daily_replay_continuity,
    _verify_checkpoint_artifacts,
)
from .contract import (
    _CHECKPOINT_RELATIVE,
    FutureHoldoutContract,
    _closed_csv_files,
    _git_executable,
    _read_json,
    _repository_root,
    compatibility_value,
    load_future_holdout_contract,
    runtime_compatibility_value,
    validate_holdout_layout,
)
from .manifest import (
    _assemble_future_holdout_manifest,
    _normalized_scores,
    _validate_future_holdout_manifest_payload,
)
from .replay import (
    _daily_decision_payload,
    read_future_holdout_decision,
    read_future_holdout_replay,
    replay_future_holdout,
)
from .snapshots import (
    _capture_holdout_data,
    _validated_snapshot_prefix_sha256,
)
from .snapshots import (
    append_holdout_snapshot as _append_holdout_snapshot,
)
from .source_identity import current_holdout_binding, validate_prior_close_account


def _manifest_repository_root(repository_root: str | Path | None) -> Path:
    owning_root = compatibility_value("_repository_root", _repository_root)().resolve()
    root = owning_root if repository_root is None else Path(repository_root).resolve()
    if root != owning_root:
        raise ValueError("holdout manifest requires the owning repository root")
    return root


def _observation_metrics(
    metrics_path: str | Path | None,
    *,
    sessions: tuple[str, ...],
    holdout_data_sha256: str,
    contract: FutureHoldoutContract,
    account_path: str | Path | None = None,
    repository_root: str | Path | None = None,
    journal_path: str | Path | None = None,
) -> tuple[dict[str, float | int | None], str | None]:
    if not sessions:
        if metrics_path is not None:
            raise ValueError("holdout metrics must be omitted before observations exist")
        return _normalized_scores(None, sessions=sessions, contract=contract), None
    if metrics_path is None:
        raise RuntimeError(
            "observed sessions require a deterministic holdout replay; detached score files are prohibited"
        )
    source = Path(metrics_path)
    try:
        before = source.read_bytes()
        observed = read_future_holdout_replay(
            source,
            contract=contract,
            sessions=sessions,
            holdout_data_sha256=holdout_data_sha256,
        )
        after = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "observed sessions require a deterministic holdout replay; detached score files are prohibited"
        ) from exc
    if before != after:
        raise RuntimeError("future holdout replay changed during readback")
    if account_path is None or repository_root is None:
        raise RuntimeError("observed sessions require deterministic holdout re-execution")
    expected = runtime_compatibility_value("replay_future_holdout", replay_future_holdout)(
        repository_root=repository_root,
        account_path=account_path,
        journal_path=journal_path,
        contract=contract,
    )
    if observed != expected:
        raise RuntimeError("future holdout replay differs from deterministic re-execution")
    scores = cast(Mapping[str, float | int | None], observed["scores"])
    return dict(scores), hashlib.sha256(before).hexdigest()


def build_future_holdout_manifest(
    *,
    account_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build evidence only from authoritative repository and file inputs."""

    root = _manifest_repository_root(repository_root)
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    sessions, data_sha256 = validate_holdout_layout(root, contract=contract)
    from ...account import load_account

    account = load_account(account_path).to_dict()
    compatibility_value("validate_prior_close_account", validate_prior_close_account)(account, frozen_data_dir=root / "data/frozen")
    scores, metrics_sha256 = _observation_metrics(
        metrics_path,
        sessions=sessions,
        holdout_data_sha256=data_sha256,
        contract=contract,
        account_path=account_path,
        repository_root=root,
        journal_path=journal_path,
    )
    binding = compatibility_value("current_holdout_binding", current_holdout_binding)(root)
    return _assemble_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=sessions,
        scores=scores,
        holdout_data_sha256=data_sha256,
        metrics_sha256=metrics_sha256,
    )


def validate_future_holdout_manifest(
    *,
    manifest_path: str | Path,
    account_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> None:
    """Re-read every authoritative input and reject stale or forged evidence."""

    manifest = _read_json(Path(manifest_path), label="future holdout manifest")
    expected = build_future_holdout_manifest(
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=repository_root,
    )
    _validate_future_holdout_manifest_payload(manifest, expected=expected)


def generate_future_holdout_manifest(
    *,
    account_path: str | Path,
    output_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Generate the ignored exact-HEAD manifest used by the final acceptance gate."""

    root = _manifest_repository_root(repository_root)
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    manifest = build_future_holdout_manifest(
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=root,
    )
    tracked = subprocess.run(
        [_git_executable(), "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")  # nosec B603
    protected_paths = [Path(account_path)]
    if metrics_path is not None:
        protected_paths.append(Path(metrics_path))
    if journal_path is not None:
        protected_paths.append(Path(journal_path))
    protected_paths.extend(
        [
            *(root / value.decode("utf-8") for value in tracked if value),
            *(path for path in (root / "data/frozen").rglob("*") if path.is_file()),
            *_closed_csv_files(
                root / contract.data_directory,
                label="future holdout",
                missing_ok=True,
            ),
        ]
    )
    destination = Path(output_path)
    atomic_write_text(
        destination,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        protected_paths=protected_paths,
    )
    validate_future_holdout_manifest(
        manifest_path=destination,
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=root,
    )
    return manifest

def _generate_future_holdout_replay_locked(
    *,
    repository_root: Path,
    account_path: str | Path,
    output_path: Path,
    decision_output_path: Path | None,
    checkpoint_path: Path,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate and persist evidence while the caller owns the bundle lock."""

    root = repository_root
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    destination = output_path
    decision_destination = decision_output_path
    protected_data = (root / "data/frozen", root / contract.data_directory)
    _reject_output_in_protected_data(
        destination,
        protected_directories=protected_data,
    )
    if decision_destination is not None:
        _reject_output_in_protected_data(
            decision_destination,
            protected_directories=protected_data,
        )
    _reject_authoritative_output_paths(
        repository_root=root,
        output_path=destination,
        decision_output_path=decision_destination,
        account_path=account_path,
        journal_path=journal_path,
        holdout_data_directory=contract.data_directory,
        checkpoint_path=checkpoint_path,
        lock_paths=(
            _artifact_bundle_lock_path(root),
            *_artifact_bundle_lock_paths(
                (
                    destination,
                    checkpoint_path,
                    *(() if decision_destination is None else (decision_destination,)),
                )
            ),
        ),
    )
    if decision_destination is None:
        raise ValueError("future holdout replay requires a daily decision output artifact")
    prior_checkpoint = _read_checkpoint_carrier(
        checkpoint_path,
        contract=contract,
    )
    prior_payload = None if prior_checkpoint is None else prior_checkpoint[0]
    if prior_payload is not None:
        if prior_payload["replay_output_path"] != _resolved_path_text(destination) or prior_payload[
            "decision_output_path"
        ] != _resolved_path_text(decision_destination):
            raise ValueError("future holdout replay must reuse the checkpointed output paths")
        _verify_checkpoint_artifacts(prior_payload, contract=contract)
    trusted_checkpoint = None if prior_checkpoint is None else prior_checkpoint[1]
    replay = runtime_compatibility_value("replay_future_holdout", replay_future_holdout)(
        repository_root=root,
        account_path=account_path,
        journal_path=journal_path,
        trusted_journal_checkpoint=trusted_checkpoint,
        contract=contract,
    )
    holdout_root = root / contract.data_directory
    if holdout_root.exists():
        snapshot = _capture_holdout_data(holdout_root)
        _validated_snapshot_prefix_sha256(
            snapshot,
            prefix_sessions=snapshot.sessions,
        )
        replay_sessions = tuple(cast(Sequence[str], replay.get("sessions", ())))
        if snapshot.sessions != replay_sessions or snapshot.sha256 != replay.get("holdout_data_sha256"):
            raise ValueError("future holdout data changed during deterministic replay")
        if (
            prior_payload is not None
            and _validated_snapshot_prefix_sha256(
                snapshot,
                prefix_sessions=cast(Sequence[str], prior_payload["sessions"]),
            )
            != prior_payload["holdout_data_sha256"]
        ):
            raise ValueError("future holdout changed the checkpointed data prefix")
    _validate_daily_replay_continuity(
        replay,
        prior_checkpoint=prior_payload,
        contract=contract,
    )
    snapshots = _artifact_snapshots((destination, decision_destination, checkpoint_path))
    owned: dict[Path, bytes] = {}
    try:
        replay_text = json.dumps(replay, ensure_ascii=False, indent=2) + "\n"
        owned[destination] = replay_text.encode("utf-8")
        runtime_compatibility_value("atomic_write_text", atomic_write_text)(
            destination,
            replay_text,
            protected_paths=(
                account_path,
                root / "data/frozen",
                root / contract.data_directory,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed = read_future_holdout_replay(
            destination,
            contract=contract,
            sessions=tuple(replay["sessions"]),
            holdout_data_sha256=str(replay["holdout_data_sha256"]),
        )
        if observed != replay:
            raise RuntimeError("future holdout replay changed during readback")
        latest = _daily_decision_payload(replay)
        decision_text = json.dumps(latest, ensure_ascii=False, indent=2) + "\n"
        owned[decision_destination] = decision_text.encode("utf-8")
        runtime_compatibility_value("atomic_write_text", atomic_write_text)(
            decision_destination,
            decision_text,
            protected_paths=(
                destination,
                account_path,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed_decision = read_future_holdout_decision(
            decision_destination,
            replay=replay,
        )
        if observed_decision != latest:
            raise RuntimeError("future holdout daily decision changed during readback")
        replay_output_bytes = _read_protected_artifact(
            destination,
            label="deterministic replay artifact",
        )
        decision_output_bytes = _read_protected_artifact(
            decision_destination,
            label="daily decision artifact",
        )
        checkpoint = _checkpoint_payload(
            replay,
            replay_output_path=destination,
            replay_output_bytes=replay_output_bytes,
            decision_output_path=decision_destination,
            decision_output_bytes=decision_output_bytes,
        )
        checkpoint_text = json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n"
        owned[checkpoint_path] = checkpoint_text.encode("utf-8")
        runtime_compatibility_value("atomic_write_text", atomic_write_text)(
            checkpoint_path,
            checkpoint_text,
            protected_paths=(
                destination,
                account_path,
                decision_destination,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed_checkpoint = _read_checkpoint_carrier(
            checkpoint_path,
            contract=contract,
        )
        if observed_checkpoint is None or observed_checkpoint[0] != checkpoint:
            raise RuntimeError("future holdout journal checkpoint changed during readback")
        _verify_checkpoint_artifacts(checkpoint, contract=contract)
    except BaseException as primary:
        for failure in _restore_artifact_snapshots(snapshots, owned):
            primary.add_note(f"future holdout rollback also failed: {type(failure).__name__}: {failure}")
        raise
    return replay


def generate_future_holdout_replay(
    *,
    repository_root: str | Path,
    account_path: str | Path,
    output_path: str | Path,
    decision_output_path: str | Path | None = None,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate, atomically persist, and re-read the deterministic replay."""

    root = Path(repository_root).resolve()
    checkpoint = _canonical_carrier_path(root / _CHECKPOINT_RELATIVE)
    destination = _canonical_carrier_path(output_path)
    decision_destination = (
        None if decision_output_path is None else _canonical_carrier_path(decision_output_path)
    )
    carriers = (
        destination,
        checkpoint,
        *(() if decision_destination is None else (decision_destination,)),
    )
    with runtime_compatibility_value("_artifact_bundle_lock", _artifact_bundle_lock)(root, carriers):
        return _generate_future_holdout_replay_locked(
            repository_root=root,
            account_path=account_path,
            output_path=destination,
            decision_output_path=decision_destination,
            checkpoint_path=checkpoint,
            journal_path=journal_path,
        )


def append_holdout_snapshot(
    *,
    repository_root: str | Path,
    snapshot_dir: str | Path,
    contract: FutureHoldoutContract | None = None,
) -> dict[str, object]:
    """Atomically append one complete daily snapshot outside the frozen prefix."""

    return _append_holdout_snapshot(
        repository_root=repository_root,
        snapshot_dir=snapshot_dir,
        contract=contract,
        _read_checkpoint=_read_checkpoint_carrier,
        _verify_checkpoint=_verify_checkpoint_artifacts,
    )

__all__ = (
    "_manifest_repository_root",
    "_observation_metrics",
    "build_future_holdout_manifest",
    "validate_future_holdout_manifest",
    "generate_future_holdout_manifest",
    "_generate_future_holdout_replay_locked",
    "generate_future_holdout_replay",
    "append_holdout_snapshot",
)
