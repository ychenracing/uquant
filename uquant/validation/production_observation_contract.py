"""Typed finite compatibility contract for the production observation CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

type VoidOperation = Callable[..., None]
type PayloadOperation = Callable[..., dict[str, Any]]
type BackupOperation = Callable[..., tuple[Path, dict[str, Any]]]
type ExitOperation = Callable[..., int]


@dataclass(frozen=True, slots=True)
class ProductionObservationCliSeams:
    """The exact legacy monkeypatch capabilities retained by the thin CLI."""

    atomic_write_bytes: VoidOperation
    atomic_write_text: VoidOperation
    fsync_checkpoint_directory: VoidOperation
    acquire_file_lock: VoidOperation
    release_file_lock: VoidOperation
    append_holdout_snapshot: PayloadOperation
    generate_future_holdout_replay: PayloadOperation
    uquant_main: ExitOperation
    build_local_lane_report: PayloadOperation
    create_backup_checkpoint: BackupOperation
