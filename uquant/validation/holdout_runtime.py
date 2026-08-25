"""Compatibility facade for future-holdout replay and artifact transactions."""

# ruff: noqa: F401, I001 - frozen compatibility exports and seams
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..atomic_io import atomic_write_text
from .ai_era import AI_ERA_WINDOWS
from .holdout.artifact_transaction import (
    AUTHORITATIVE_REPOSITORY_RELATIVES as _AUTHORITATIVE_REPOSITORY_RELATIVES,
    ArtifactSnapshot as _ArtifactSnapshot,
    artifact_bundle_lock as _artifact_bundle_lock,
    artifact_bundle_lock_path as _artifact_bundle_lock_path,
    artifact_bundle_lock_paths as _artifact_bundle_lock_paths,
    artifact_snapshots as _owner_artifact_snapshots,
    canonical_carrier_path as _canonical_carrier_path,
    git_metadata_paths as _git_metadata_paths,
    link_bytes_if_absent as _link_bytes_if_absent,
    paths_overlap as _paths_overlap,
    read_protected_artifact as _read_protected_artifact,
    reject_authoritative_output_paths as _reject_authoritative_output_paths,
    reject_output_in_protected_data as _reject_output_in_protected_data,
    resolved_path_text as _resolved_path_text,
    restore_artifact_snapshots as _owner_restore_artifact_snapshots,
    restore_owned_artifact as _owner_restore_owned_artifact,
    tracked_repository_paths as _tracked_repository_paths,
)
from .holdout.capabilities import (
    HoldoutRuntimeCapabilities,
    holdout_runtime_scope,
    scoped_capability_wrapper,
)
from .holdout.checkpoints import (
    CHECKPOINT_FIELDS as _CHECKPOINT_FIELDS,
    checkpoint_payload as _checkpoint_payload,
    read_checkpoint_carrier as _read_checkpoint_carrier,
    validate_daily_replay_continuity as _validate_daily_replay_continuity,
    verify_checkpoint_artifacts as _verify_checkpoint_artifacts,
)
from .holdout.contract import CHECKPOINT_RELATIVE as _CHECKPOINT_RELATIVE
from .holdout.contract import FutureHoldoutContract
from .holdout.replay import (
    DAILY_DECISION_FIELDS as _DAILY_DECISION_FIELDS,
    REPLAY_FIELDS as _REPLAY_FIELDS,
    daily_decision_payload as _daily_decision_payload,
    decision_payload as _decision_payload,
    decision_payload_sha256 as _decision_payload_sha256,
    drawdown as _drawdown,
    period_symbol_pnl as _period_symbol_pnl,
    read_future_holdout_decision as _owner_read_future_holdout_decision,
    read_future_holdout_replay as _owner_read_future_holdout_replay,
    replay_future_holdout as _owner_replay_future_holdout,
)
from .holdout.snapshots import (
    HoldoutDataSnapshot as _HoldoutDataSnapshot,
    capture_holdout_data as _capture_holdout_data,
    csv_inventory as _csv_inventory,
    materialize_overlay as _materialize_overlay,
    merged_csv_text as _merged_csv_text,
    one_snapshot_row as _one_snapshot_row,
    snapshot_files_sha256 as _snapshot_files_sha256,
    validated_snapshot_prefix_sha256 as _validated_snapshot_prefix_sha256,
)
from .holdout.source_identity import holdout_source_sha256, validate_prior_close_account
from .holdout.service import (
    append_holdout_snapshot as _append_holdout_snapshot,
    generate_future_holdout_replay as _owner_generate_future_holdout_replay,
    observation_metrics as _owner_observation_metrics,
)

replay_future_holdout = _owner_replay_future_holdout


def current_runtime_capabilities() -> HoldoutRuntimeCapabilities:
    return HoldoutRuntimeCapabilities(
        holdout_source_sha256=holdout_source_sha256,
        validate_prior_close_account=validate_prior_close_account,
        replay_future_holdout=replay_future_holdout,
        atomic_write_text=atomic_write_text,
        artifact_bundle_lock=_artifact_bundle_lock,
        read_protected_artifact=_read_protected_artifact,
        os_adapter=os,
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
    )


_owner_append_holdout_snapshot = append_holdout_snapshot
append_holdout_snapshot = scoped_capability_wrapper(
    _owner_append_holdout_snapshot,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
generate_future_holdout_replay = scoped_capability_wrapper(
    _owner_generate_future_holdout_replay,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
_observation_metrics = scoped_capability_wrapper(
    _owner_observation_metrics,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
read_future_holdout_decision = scoped_capability_wrapper(
    _owner_read_future_holdout_decision,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
read_future_holdout_replay = scoped_capability_wrapper(
    _owner_read_future_holdout_replay,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
replay_future_holdout = scoped_capability_wrapper(
    _owner_replay_future_holdout,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
_artifact_snapshots = scoped_capability_wrapper(
    _owner_artifact_snapshots,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
_restore_artifact_snapshots = scoped_capability_wrapper(
    _owner_restore_artifact_snapshots,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)
_restore_owned_artifact = scoped_capability_wrapper(
    _owner_restore_owned_artifact,
    capabilities=current_runtime_capabilities,
    scope=holdout_runtime_scope,
)


__all__ = (
    "_AUTHORITATIVE_REPOSITORY_RELATIVES",
    "_CHECKPOINT_FIELDS",
    "_CHECKPOINT_RELATIVE",
    "_DAILY_DECISION_FIELDS",
    "_REPLAY_FIELDS",
    "append_holdout_snapshot",
    "generate_future_holdout_replay",
    "read_future_holdout_decision",
    "read_future_holdout_replay",
    "replay_future_holdout",
)

for _name, _value in (
    ("append_holdout_snapshot", append_holdout_snapshot),
    ("generate_future_holdout_replay", generate_future_holdout_replay),
    ("read_future_holdout_decision", read_future_holdout_decision),
    ("read_future_holdout_replay", read_future_holdout_replay),
    ("replay_future_holdout", replay_future_holdout),
):
    _value.__module__ = __name__
    _value.__qualname__ = _name
