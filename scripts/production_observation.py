"""One operator entry point for evidence-only daily production observation."""

# ruff: noqa: F401, RUF022 - finite aliases and frozen public seam order

from __future__ import annotations

import argparse
import contextlib
import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from functools import wraps
from typing import Any

from uquant.atomic_io import atomic_write_bytes, atomic_write_text
from uquant.cli import main as uquant_main
from uquant.infrastructure.file_lock import acquire_file_lock, release_file_lock
from uquant.validation.holdout.cli_operations import (
    CANONICAL_JOURNAL_CHECKPOINT_PATH,
    CANONICAL_JOURNAL_PATH,
    CANONICAL_LOCAL_LANE_REPORT_PATH,
    build_local_lane_report,
    load_journal_checkpoint,
    read_trusted_execution_journal,
    write_journal_checkpoint,
)
from uquant.validation.holdout_runtime import (
    append_holdout_snapshot,
    generate_future_holdout_replay,
)
from uquant.validation.production_observation import (
    DEFAULT_BACKUP_ROOT as _DEFAULT_BACKUP_ROOT,
)
from uquant.validation.production_observation import (
    DEFAULT_DECISION_OUTPUT as _DEFAULT_DECISION_OUTPUT,
)
from uquant.validation.production_observation import (
    DEFAULT_REPLAY_OUTPUT as _DEFAULT_REPLAY_OUTPUT,
)
from uquant.validation.production_observation import (
    add_backup_evidence as _owner_add_backup_evidence,
)
from uquant.validation.production_observation import (
    create_backup_checkpoint as _owner_create_backup_checkpoint,
)
from uquant.validation.production_observation import (
    fsync_checkpoint_directory as _fsync_checkpoint_directory,
)
from uquant.validation.production_observation import (
    observation_lock as _owner_observation_lock,
)
from uquant.validation.production_observation import production_observation_cli_scope
from uquant.validation.production_observation import (
    run_production_observation as _owner_run_production_observation,
)
from uquant.validation.production_observation import (
    seal_backup_receipt as _owner_seal_backup_receipt,
)
from uquant.validation.production_observation import (
    verify_backup_checkpoint as _owner_verify_backup_checkpoint,
)
from uquant.validation.production_observation_contract import (
    ProductionObservationCliSeams,
)

__all__ = (
    "create_backup_checkpoint",
    "add_backup_evidence",
    "seal_backup_receipt",
    "verify_backup_checkpoint",
    "run_production_observation",
    "main",
)


def _owner_scope() -> AbstractContextManager[None]:
    return production_observation_cli_scope(
        ProductionObservationCliSeams(
            atomic_write_bytes=atomic_write_bytes,
            atomic_write_text=atomic_write_text,
            fsync_checkpoint_directory=_fsync_checkpoint_directory,
            acquire_file_lock=acquire_file_lock,
            release_file_lock=release_file_lock,
            append_holdout_snapshot=append_holdout_snapshot,
            generate_future_holdout_replay=generate_future_holdout_replay,
            uquant_main=uquant_main,
            build_local_lane_report=build_local_lane_report,
            create_backup_checkpoint=create_backup_checkpoint,
        )
    )


@wraps(_owner_create_backup_checkpoint)
def create_backup_checkpoint(*args: Any, **kwargs: Any) -> Any:
    with _owner_scope():
        return _owner_create_backup_checkpoint(*args, **kwargs)


@wraps(_owner_add_backup_evidence)
def add_backup_evidence(*args: Any, **kwargs: Any) -> Any:
    with _owner_scope():
        return _owner_add_backup_evidence(*args, **kwargs)


@wraps(_owner_seal_backup_receipt)
def seal_backup_receipt(*args: Any, **kwargs: Any) -> Any:
    with _owner_scope():
        return _owner_seal_backup_receipt(*args, **kwargs)


@wraps(_owner_verify_backup_checkpoint)
def verify_backup_checkpoint(*args: Any, **kwargs: Any) -> Any:
    with _owner_scope():
        return _owner_verify_backup_checkpoint(*args, **kwargs)


@wraps(_owner_run_production_observation)
def run_production_observation(*args: Any, **kwargs: Any) -> Any:
    with _owner_scope():
        return _owner_run_production_observation(*args, **kwargs)


@contextlib.contextmanager
@wraps(_owner_observation_lock)
def _observation_lock(*args: Any, **kwargs: Any) -> Iterator[Any]:
    with _owner_scope(), _owner_observation_lock(*args, **kwargs) as value:
        yield value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.production_observation",
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
