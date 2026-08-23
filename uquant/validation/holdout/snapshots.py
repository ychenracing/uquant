"""Append-only future data capture, validation, and replay overlays."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .contract import (
    _CHECKPOINT_RELATIVE,
    FutureHoldoutContract,
    _closed_csv_files,
    _csv_dates_from_text,
    _session_dates,
    holdout_data_identity,
    load_future_holdout_contract,
)


@dataclass(frozen=True, slots=True)
class _HoldoutDataSnapshot:
    sessions: tuple[str, ...]
    sha256: str
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    payload: bytes | None
    mode: int | None


def _snapshot_files_sha256(files: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, content in files:
        relative_bytes = relative.encode()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _capture_holdout_data(root: Path) -> _HoldoutDataSnapshot:
    try:
        paths = _closed_csv_files(root, label="future holdout", missing_ok=False)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    sessions: set[str] = set()
    files: list[tuple[str, bytes]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        try:
            decoded = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"cannot inspect market data: {path}") from exc
        sessions.update(_csv_dates_from_text(decoded, path=path))
        files.append((relative, content))
    return _HoldoutDataSnapshot(
        sessions=tuple(sorted(sessions)),
        sha256=_snapshot_files_sha256(files),
        files=tuple(files),
    )


def _validated_snapshot_prefix_sha256(
    snapshot: _HoldoutDataSnapshot,
    *,
    prefix_sessions: Sequence[str],
) -> str:
    inventories: dict[str, set[str]] = {}
    for relative, content in snapshot.files:
        path = Path(relative)
        if len(path.parts) != 2 or path.parts[0] not in snapshot.sessions:
            raise ValueError("future holdout data is not stored as daily snapshots")
        session, name = path.parts
        try:
            dates = _csv_dates_from_text(content.decode("utf-8"), path=path)
        except (RuntimeError, UnicodeError) as exc:
            raise ValueError("future holdout daily snapshot is malformed") from exc
        if dates != (session,):
            raise ValueError("future holdout daily snapshot must contain its one session")
        inventories.setdefault(session, set()).add(name)
    if set(inventories) != set(snapshot.sessions) or any(
        inventory != next(iter(inventories.values())) for inventory in inventories.values()
    ):
        raise ValueError("future holdout daily snapshot inventory is incomplete")
    prefix = set(prefix_sessions)
    selected = tuple(item for item in snapshot.files if Path(item[0]).parts[0] in prefix)
    if prefix and {Path(relative).parts[0] for relative, _ in selected} != prefix:
        raise ValueError("future holdout checkpointed data prefix is incomplete")
    return _snapshot_files_sha256(selected)

def _csv_inventory(root: Path, *, label: str) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} must be a physical directory")
    paths = tuple(sorted(root.glob("*.csv")))
    if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError(f"{label} CSV inventory is missing or unsafe")
    return {path.name: path for path in paths}


def _one_snapshot_row(path: Path, *, expected_header: tuple[str, ...]) -> tuple[str, bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout snapshot contains an unsafe file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        reader = csv.DictReader(text.splitlines())
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"cannot read holdout snapshot: {path}") from exc
    if header != expected_header:
        raise ValueError(f"holdout snapshot header differs from frozen data: {path.name}")
    if len(rows) != 1 or not rows[0].get("date"):
        raise ValueError("each holdout snapshot CSV must contain exactly one market row")
    try:
        session = pd.Timestamp(rows[0]["date"]).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"holdout snapshot contains an invalid date: {path.name}") from exc
    if session != rows[0]["date"]:
        raise ValueError(f"holdout snapshot contains a non-canonical date: {path.name}")
    numeric_columns = tuple(
        column for column in ("open", "high", "low", "close", "volume", "amount") if column in header
    )
    try:
        numeric_values = tuple(float(rows[0][column]) for column in numeric_columns)
    except (TypeError, ValueError) as exc:
        raise ValueError("holdout snapshot requires finite OHLCV and amount values") from exc
    if not numeric_columns or any(not math.isfinite(value) for value in numeric_values):
        raise ValueError("holdout snapshot requires finite OHLCV and amount values")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    writer.writerow(rows[0])
    return session, output.getvalue().encode("utf-8")


def append_holdout_snapshot(
    *,
    repository_root: str | Path,
    snapshot_dir: str | Path,
    contract: FutureHoldoutContract | None = None,
    _read_checkpoint: Callable[..., tuple[dict[str, Any], object] | None],
    _verify_checkpoint: Callable[..., object],
) -> dict[str, object]:
    """Atomically append one complete daily snapshot outside the frozen prefix."""

    root = Path(repository_root).resolve()
    reviewed = load_future_holdout_contract() if contract is None else contract
    frozen = _csv_inventory(root / "data/frozen", label="frozen market data")
    snapshot_root = Path(snapshot_dir)
    snapshot = _csv_inventory(snapshot_root, label="holdout snapshot")
    unsupported = tuple(
        path
        for path in snapshot_root.iterdir()
        if path.is_symlink() or path.is_dir() or path.suffix.lower() != ".csv"
    )
    if unsupported:
        raise ValueError("holdout snapshot contains unsupported entries")
    if set(snapshot) != set(frozen):
        raise ValueError("holdout snapshot files must exactly match the frozen CSV inventory")

    encoded: dict[str, bytes] = {}
    sessions: set[str] = set()
    for name in sorted(frozen):
        frozen_header = tuple(next(csv.reader(frozen[name].read_text(encoding="utf-8").splitlines())))
        session, content = _one_snapshot_row(
            snapshot[name],
            expected_header=frozen_header,
        )
        sessions.add(session)
        encoded[name] = content
    if len(sessions) != 1:
        raise ValueError("holdout snapshot files must contain one common session")
    session = next(iter(sessions))

    holdout_root = root / reviewed.data_directory
    existing_sessions, _ = holdout_data_identity(holdout_root)
    try:
        _session_dates(existing_sessions, contract=reviewed)
    except ValueError as exc:
        raise ValueError("existing holdout data is not a contracted session prefix") from exc
    destination = holdout_root / session
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("holdout daily append destination is unsafe")
        observed = {
            path.name: path.read_bytes()
            for path in destination.iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if observed != encoded or len(tuple(destination.iterdir())) != len(encoded):
            raise ValueError("holdout snapshot conflicts with the immutable daily append")
        return {"session": session, "files": len(encoded), "idempotent": True}
    if (
        len(existing_sessions) >= len(reviewed.review_sessions)
        or session != (reviewed.review_sessions[len(existing_sessions)])
    ):
        raise ValueError("holdout snapshot must be the next contracted exchange session")
    if existing_sessions:
        checkpoint_path = root / _CHECKPOINT_RELATIVE
        prior_checkpoint = _read_checkpoint(
            checkpoint_path,
            contract=reviewed,
        )
        if prior_checkpoint is None:
            raise ValueError("prior daily replay checkpoint is required before the next holdout append")
        prior_payload, _ = prior_checkpoint
        _verify_checkpoint(prior_payload, contract=reviewed)
        if (
            tuple(prior_payload["sessions"]) != existing_sessions
            or prior_payload["holdout_data_sha256"] != holdout_data_identity(holdout_root)[1]
        ):
            raise ValueError("prior daily replay checkpoint does not match the current holdout prefix")

    holdout_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{session}-", dir=holdout_root))
    try:
        for name, content in encoded.items():
            path = staging / name
            path.write_bytes(content)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        os.replace(staging, destination)
        directory_fd = os.open(
            holdout_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"session": session, "files": len(encoded), "idempotent": False}


def _merged_csv_text(
    frozen: Path,
    future_files: Sequence[tuple[str, bytes]],
) -> str:
    try:
        frozen_text = frozen.read_text(encoding="utf-8")
        frozen_reader = csv.DictReader(frozen_text.splitlines())
        header = tuple(frozen_reader.fieldnames or ())
        rows = {str(row["date"]): dict(row) for row in frozen_reader}
    except (OSError, UnicodeError, csv.Error, KeyError) as exc:
        raise ValueError(f"cannot materialize frozen market data: {frozen}") from exc
    for relative, content in future_files:
        try:
            reader = csv.DictReader(content.decode("utf-8").splitlines())
            if tuple(reader.fieldnames or ()) != header:
                raise ValueError(f"future market header differs: {relative}")
            for row in reader:
                session = str(row.get("date", ""))
                if not session:
                    raise ValueError(f"future market row lacks a date: {relative}")
                if session in rows and rows[session] != dict(row):
                    raise ValueError(f"future market row conflicts with an existing date: {relative}")
                rows[session] = dict(row)
        except (UnicodeError, csv.Error) as exc:
            raise ValueError(f"cannot materialize future market data: {relative}") from exc
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for session in sorted(rows):
        writer.writerow(rows[session])
    return output.getvalue()


def _materialize_overlay(
    root: Path,
    destination: Path,
    snapshot: _HoldoutDataSnapshot,
) -> None:
    frozen = _csv_inventory(root / "data/frozen", label="frozen market data")
    by_name: dict[str, list[tuple[str, bytes]]] = {name: [] for name in frozen}
    for relative, content in snapshot.files:
        name = Path(relative).name
        if name not in by_name:
            raise ValueError(f"future holdout contains an unknown market file: {name}")
        by_name[name].append((relative, content))
    if any(not paths for paths in by_name.values()):
        raise ValueError("future holdout is incomplete for deterministic replay")
    destination.mkdir(parents=True, exist_ok=False)
    for name in sorted(frozen):
        (destination / name).write_text(
            _merged_csv_text(frozen[name], sorted(by_name[name])),
            encoding="utf-8",
        )

__all__ = (
    "_HoldoutDataSnapshot",
    "_snapshot_files_sha256",
    "_capture_holdout_data",
    "_validated_snapshot_prefix_sha256",
    "_csv_inventory",
    "_one_snapshot_row",
    "_merged_csv_text",
    "_materialize_overlay",
)
