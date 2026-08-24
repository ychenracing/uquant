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
from ..execution_journal import JournalCheckpoint
from .artifact_transaction import (
    artifact_bundle_lock as _artifact_bundle_lock,
)
from .artifact_transaction import (
    artifact_bundle_lock_path as _artifact_bundle_lock_path,
)
from .artifact_transaction import (
    artifact_bundle_lock_paths as _artifact_bundle_lock_paths,
)
from .artifact_transaction import (
    artifact_snapshots as _artifact_snapshots,
)
from .artifact_transaction import (
    canonical_carrier_path as _canonical_carrier_path,
)
from .artifact_transaction import (
    read_protected_artifact as _read_protected_artifact,
)
from .artifact_transaction import (
    reject_authoritative_output_paths as _reject_authoritative_output_paths,
)
from .artifact_transaction import (
    reject_output_in_protected_data as _reject_output_in_protected_data,
)
from .artifact_transaction import (
    resolved_path_text as _resolved_path_text,
)
from .artifact_transaction import (
    restore_artifact_snapshots as _restore_artifact_snapshots,
)
from .capabilities import holdout_facade_capabilities, holdout_runtime_capabilities
from .checkpoints import (
    checkpoint_payload as _checkpoint_payload,
)
from .checkpoints import (
    read_checkpoint_carrier as _read_checkpoint_carrier,
)
from .checkpoints import (
    validate_daily_replay_continuity as _validate_daily_replay_continuity,
)
from .checkpoints import (
    verify_checkpoint_artifacts as _verify_checkpoint_artifacts,
)
from .contract import (
    CHECKPOINT_RELATIVE as _CHECKPOINT_RELATIVE,
)
from .contract import (
    FutureHoldoutContract,
    load_future_holdout_contract,
    validate_holdout_layout,
)
from .contract import (
    closed_csv_files as _closed_csv_files,
)
from .contract import (
    git_executable as _git_executable,
)
from .contract import (
    read_json as _read_json,
)
from .contract import (
    repository_root as _repository_root,
)
from .manifest import (
    assemble_future_holdout_manifest as _assemble_future_holdout_manifest,
)
from .manifest import (
    normalized_scores as _normalized_scores,
)
from .manifest import (
    validate_future_holdout_manifest_payload as _validate_future_holdout_manifest_payload,
)
from .replay import (
    daily_decision_payload as _daily_decision_payload,
)
from .replay import (
    read_future_holdout_decision,
    read_future_holdout_replay,
    replay_future_holdout,
)
from .snapshots import (
    append_holdout_snapshot as _append_holdout_snapshot,
)
from .snapshots import (
    capture_holdout_data as _capture_holdout_data,
)
from .snapshots import (
    validated_snapshot_prefix_sha256 as _validated_snapshot_prefix_sha256,
)
from .source_identity import current_holdout_binding, validate_prior_close_account


def _manifest_repository_root(repository_root: str | Path | None) -> Path:
    capabilities = holdout_facade_capabilities()
    repository_root_capability = (
        _repository_root if capabilities is None else capabilities.repository_root
    )
    owning_root = repository_root_capability().resolve()
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
    capabilities = holdout_runtime_capabilities()
    replay = replay_future_holdout if capabilities is None else capabilities.replay_future_holdout
    expected = replay(
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
    capabilities = holdout_facade_capabilities()
    validate_account = (
        validate_prior_close_account
        if capabilities is None
        else capabilities.validate_prior_close_account
    )
    current_binding = (
        current_holdout_binding
        if capabilities is None
        else capabilities.current_holdout_binding
    )
    validate_account(account, frozen_data_dir=root / "data/frozen")
    scores, metrics_sha256 = _observation_metrics(
        metrics_path,
        sessions=sessions,
        holdout_data_sha256=data_sha256,
        contract=contract,
        account_path=account_path,
        repository_root=root,
        journal_path=journal_path,
    )
    binding = current_binding(root)
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


def _validated_replay_request(
    *,
    repository_root: Path,
    account_path: str | Path,
    output_path: Path,
    decision_output_path: Path | None,
    checkpoint_path: Path,
    journal_path: str | Path | None,
) -> tuple[
    FutureHoldoutContract,
    Path,
    Mapping[str, Any] | None,
    JournalCheckpoint | None,
]:
    contract = load_future_holdout_contract(repository_root / "benchmarks/future_holdout_contract.json")
    protected_data = (
        repository_root / "data/frozen",
        repository_root / contract.data_directory,
    )
    _reject_output_in_protected_data(output_path, protected_directories=protected_data)
    if decision_output_path is not None:
        _reject_output_in_protected_data(
            decision_output_path,
            protected_directories=protected_data,
        )
    _reject_authoritative_output_paths(
        repository_root=repository_root,
        output_path=output_path,
        decision_output_path=decision_output_path,
        account_path=account_path,
        journal_path=journal_path,
        holdout_data_directory=contract.data_directory,
        checkpoint_path=checkpoint_path,
        lock_paths=(
            _artifact_bundle_lock_path(repository_root),
            *_artifact_bundle_lock_paths(
                (
                    output_path,
                    checkpoint_path,
                    *(() if decision_output_path is None else (decision_output_path,)),
                )
            ),
        ),
    )
    if decision_output_path is None:
        raise ValueError("future holdout replay requires a daily decision output artifact")
    prior_checkpoint = _read_checkpoint_carrier(checkpoint_path, contract=contract)
    prior_payload = None if prior_checkpoint is None else prior_checkpoint[0]
    if prior_payload is not None:
        if prior_payload["replay_output_path"] != _resolved_path_text(output_path) or prior_payload[
            "decision_output_path"
        ] != _resolved_path_text(decision_output_path):
            raise ValueError("future holdout replay must reuse the checkpointed output paths")
        _verify_checkpoint_artifacts(prior_payload, contract=contract)
    trusted_checkpoint = None if prior_checkpoint is None else prior_checkpoint[1]
    return contract, decision_output_path, prior_payload, trusted_checkpoint


def _validated_generated_replay(
    *,
    repository_root: Path,
    account_path: str | Path,
    journal_path: str | Path | None,
    contract: FutureHoldoutContract,
    prior_payload: Mapping[str, Any] | None,
    trusted_checkpoint: JournalCheckpoint | None,
) -> dict[str, Any]:
    capabilities = holdout_runtime_capabilities()
    replay_capability = (
        replay_future_holdout if capabilities is None else capabilities.replay_future_holdout
    )
    replay = replay_capability(
        repository_root=repository_root,
        account_path=account_path,
        journal_path=journal_path,
        trusted_journal_checkpoint=trusted_checkpoint,
        contract=contract,
    )
    holdout_root = repository_root / contract.data_directory
    if holdout_root.exists():
        snapshot = _capture_holdout_data(holdout_root)
        _validated_snapshot_prefix_sha256(snapshot, prefix_sessions=snapshot.sessions)
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
    return replay


def _write_replay_artifact(
    replay: Mapping[str, Any],
    *,
    destination: Path,
    repository_root: Path,
    account_path: str | Path,
    journal_path: str | Path | None,
    contract: FutureHoldoutContract,
    owned: dict[Path, bytes],
) -> None:
    replay_text = json.dumps(replay, ensure_ascii=False, indent=2) + "\n"
    owned[destination] = replay_text.encode("utf-8")
    capabilities = holdout_runtime_capabilities()
    write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text
    write_text(
        destination,
        replay_text,
        protected_paths=(
            account_path,
            repository_root / "data/frozen",
            repository_root / contract.data_directory,
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


def _write_decision_artifact(
    replay: Mapping[str, Any],
    *,
    destination: Path,
    replay_destination: Path,
    account_path: str | Path,
    journal_path: str | Path | None,
    owned: dict[Path, bytes],
) -> None:
    latest = _daily_decision_payload(replay)
    decision_text = json.dumps(latest, ensure_ascii=False, indent=2) + "\n"
    owned[destination] = decision_text.encode("utf-8")
    capabilities = holdout_runtime_capabilities()
    write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text
    write_text(
        destination,
        decision_text,
        protected_paths=(
            replay_destination,
            account_path,
            *(() if journal_path is None else (journal_path,)),
        ),
    )
    observed_decision = read_future_holdout_decision(destination, replay=replay)
    if observed_decision != latest:
        raise RuntimeError("future holdout daily decision changed during readback")


def _write_checkpoint_artifact(
    replay: Mapping[str, Any],
    *,
    replay_destination: Path,
    decision_destination: Path,
    checkpoint_path: Path,
    account_path: str | Path,
    journal_path: str | Path | None,
    contract: FutureHoldoutContract,
    owned: dict[Path, bytes],
) -> None:
    replay_output_bytes = _read_protected_artifact(
        replay_destination,
        label="deterministic replay artifact",
    )
    decision_output_bytes = _read_protected_artifact(
        decision_destination,
        label="daily decision artifact",
    )
    checkpoint = _checkpoint_payload(
        replay,
        replay_output_path=replay_destination,
        replay_output_bytes=replay_output_bytes,
        decision_output_path=decision_destination,
        decision_output_bytes=decision_output_bytes,
    )
    checkpoint_text = json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n"
    owned[checkpoint_path] = checkpoint_text.encode("utf-8")
    capabilities = holdout_runtime_capabilities()
    write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text
    write_text(
        checkpoint_path,
        checkpoint_text,
        protected_paths=(
            replay_destination,
            account_path,
            decision_destination,
            *(() if journal_path is None else (journal_path,)),
        ),
    )
    observed_checkpoint = _read_checkpoint_carrier(checkpoint_path, contract=contract)
    if observed_checkpoint is None or observed_checkpoint[0] != checkpoint:
        raise RuntimeError("future holdout journal checkpoint changed during readback")
    _verify_checkpoint_artifacts(checkpoint, contract=contract)


def _persist_replay_bundle(
    replay: dict[str, Any],
    *,
    repository_root: Path,
    account_path: str | Path,
    output_path: Path,
    decision_output_path: Path,
    checkpoint_path: Path,
    journal_path: str | Path | None,
    contract: FutureHoldoutContract,
) -> None:
    snapshots = _artifact_snapshots((output_path, decision_output_path, checkpoint_path))
    owned: dict[Path, bytes] = {}
    try:
        _write_replay_artifact(
            replay,
            destination=output_path,
            repository_root=repository_root,
            account_path=account_path,
            journal_path=journal_path,
            contract=contract,
            owned=owned,
        )
        _write_decision_artifact(
            replay,
            destination=decision_output_path,
            replay_destination=output_path,
            account_path=account_path,
            journal_path=journal_path,
            owned=owned,
        )
        _write_checkpoint_artifact(
            replay,
            replay_destination=output_path,
            decision_destination=decision_output_path,
            checkpoint_path=checkpoint_path,
            account_path=account_path,
            journal_path=journal_path,
            contract=contract,
            owned=owned,
        )
    except BaseException as primary:
        for failure in _restore_artifact_snapshots(snapshots, owned):
            primary.add_note(f"future holdout rollback also failed: {type(failure).__name__}: {failure}")
        raise


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

    contract, decision_destination, prior_payload, trusted_checkpoint = _validated_replay_request(
        repository_root=repository_root,
        account_path=account_path,
        output_path=output_path,
        decision_output_path=decision_output_path,
        checkpoint_path=checkpoint_path,
        journal_path=journal_path,
    )
    replay = _validated_generated_replay(
        repository_root=repository_root,
        account_path=account_path,
        journal_path=journal_path,
        contract=contract,
        prior_payload=prior_payload,
        trusted_checkpoint=trusted_checkpoint,
    )
    _persist_replay_bundle(
        replay,
        repository_root=repository_root,
        account_path=account_path,
        output_path=output_path,
        decision_output_path=decision_destination,
        checkpoint_path=checkpoint_path,
        journal_path=journal_path,
        contract=contract,
    )
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
    capabilities = holdout_runtime_capabilities()
    artifact_bundle_lock = (
        _artifact_bundle_lock if capabilities is None else capabilities.artifact_bundle_lock
    )
    with artifact_bundle_lock(root, carriers):
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


generate_future_holdout_replay_locked = _generate_future_holdout_replay_locked
manifest_repository_root = _manifest_repository_root
observation_metrics = _observation_metrics


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
