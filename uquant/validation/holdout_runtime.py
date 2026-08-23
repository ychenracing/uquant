"""Compatibility facade for future-holdout replay and artifact transactions."""

# ruff: noqa: F401, F405, I001 - frozen compatibility exports and seams
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..atomic_io import atomic_write_text
from .ai_era import AI_ERA_WINDOWS
from .holdout.artifact_transaction import *  # noqa: F403
from .holdout.checkpoints import *  # noqa: F403
from .holdout.contract import FutureHoldoutContract, _CHECKPOINT_RELATIVE
from .holdout.replay import *  # noqa: F403
from .holdout.snapshots import *  # noqa: F403
from .holdout.source_identity import holdout_source_sha256, validate_prior_close_account
from .holdout.service import (
    append_holdout_snapshot as _append_holdout_snapshot,
    generate_future_holdout_replay,
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

for _name in __all__:
    _value = globals()[_name]
    if callable(_value):
        _value.__module__ = __name__
        _value.__qualname__ = _name
