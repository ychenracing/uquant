"""Locked, durable execution-journal storage and the sole v2 append path."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from uquant.infrastructure.atomic_files import atomic_write_bytes, fsync_directory
from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)

from .checkpoint import verify_checkpoint
from .codec_v1 import decode_legacy_v1_record
from .codec_v2 import decode_record, encode_v2_record, event_payload
from .lifecycle import journal_timestamp as _timestamp
from .lifecycle import positive_journal_number as _positive_number
from .lifecycle import positive_journal_shares as _positive_shares
from .lifecycle import validate_lifecycle
from .models import ZERO_HASH as _ZERO_HASH
from .models import JournalCheckpoint, JournalRecord, JournalStatus


def _decode_journal_text(
    text: str,
    *,
    legacy_v1_contract: bool = False,
) -> tuple[JournalRecord, ...]:
    records: list[JournalRecord] = []
    previous = _ZERO_HASH
    lines = text.splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("execution journal contains an empty record")
    decoder = decode_legacy_v1_record if legacy_v1_contract else decode_record
    for sequence, line in enumerate(lines, start=1):
        try:
            raw = json.loads(
                line,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("execution journal contains invalid JSON") from exc
        record = decoder(raw, previous=previous, sequence=sequence)
        records.append(record)
        previous = record.record_sha256
    validate_lifecycle(records)
    return tuple(records)


def _read_descriptor(
    descriptor: int,
    *,
    legacy_v1_contract: bool = False,
) -> tuple[JournalRecord, ...]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as handle:
            return _decode_journal_text(
                handle.read(),
                legacy_v1_contract=legacy_v1_contract,
            )
    except (OSError, UnicodeError) as exc:
        raise ValueError("execution journal is unreadable") from exc


def _read_execution_journal(
    path: str | Path,
    *,
    trusted_checkpoint: JournalCheckpoint | None,
    legacy_v1_contract: bool,
) -> tuple[JournalRecord, ...]:
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except FileNotFoundError:
        records: tuple[JournalRecord, ...] = ()
        verify_checkpoint(records, trusted_checkpoint)
        return records
    except OSError as exc:
        raise ValueError("execution journal must be a regular file") from exc
    try:
        acquire_file_lock(descriptor, FileLockMode.SHARED)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("execution journal must be a regular file")
        records = _read_descriptor(
            descriptor,
            legacy_v1_contract=legacy_v1_contract,
        )
        verify_checkpoint(records, trusted_checkpoint)
        return records
    finally:
        with suppress(OSError):
            release_file_lock(descriptor)
        os.close(descriptor)


def read_execution_journal(
    path: str | Path,
    *,
    trusted_checkpoint: JournalCheckpoint | None = None,
) -> tuple[JournalRecord, ...]:
    """Validate the journal, plus continuity from an externally retained tail."""

    return _read_execution_journal(
        path,
        trusted_checkpoint=trusted_checkpoint,
        legacy_v1_contract=False,
    )


def read_legacy_v1_execution_journal(
    path: str | Path,
    *,
    trusted_checkpoint: JournalCheckpoint | None = None,
) -> tuple[JournalRecord, ...]:
    """Read through the frozen v1-only facade profile without exposing a writer."""

    return _read_execution_journal(
        path,
        trusted_checkpoint=trusted_checkpoint,
        legacy_v1_contract=True,
    )


PayloadFactory = Callable[[tuple[JournalRecord, ...]], dict[str, Any]]


def _append(path: Path, payload_factory: PayloadFactory) -> JournalRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError("execution journal must be a regular file") from exc
    primary_error: BaseException | None = None
    try:
        acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("execution journal must be a regular file")
        records = _read_descriptor(descriptor)
        previous = records[-1].record_sha256 if records else _ZERO_HASH
        record, encoded = encode_v2_record(
            payload_factory(records),
            previous=previous,
            sequence=len(records) + 1,
        )
        validate_lifecycle([*records, record])
        starting_eof = os.lseek(descriptor, 0, os.SEEK_END)
        written = 0
        try:
            while written < len(encoded):
                appended = os.write(descriptor, encoded[written:])
                if appended <= 0:
                    raise OSError("short execution journal append made no progress")
                written += appended
        except BaseException as primary:
            try:
                os.ftruncate(descriptor, starting_eof)
                os.fsync(descriptor)
            except BaseException as rollback_error:
                primary.add_note(
                    "execution journal rollback also failed: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
            raise
        os.fsync(descriptor)
        fsync_directory(path.parent)
        return record
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[tuple[str, BaseException]] = []
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
                    f"execution journal {label} also failed: {type(failure).__name__}: {failure}"
                )
        elif cleanup_errors:
            _, first_failure = cleanup_errors[0]
            for later_label, later_error in cleanup_errors[1:]:
                first_failure.add_note(
                    f"execution journal {later_label} also failed: "
                    f"{type(later_error).__name__}: {later_error}"
                )
            raise first_failure


def append_planned(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    decision_date: str | None = None,
    symbol: str,
    side: str,
    planned_weight: float | None = None,
    planned_price: float,
    planned_shares: int,
) -> JournalRecord:
    """Append one operator-authored next-open execution plan."""

    normalized_decision_date = (
        decision_date
        or _timestamp(
            recorded_at,
            field="recorded_at",
        )
        .date()
        .isoformat()
    )
    payload = event_payload(
        status=JournalStatus.PLANNED,
        plan_id=plan_id,
        recorded_at=recorded_at,
        decision_date=normalized_decision_date,
        symbol=symbol,
        side=side,
        planned_weight=planned_weight,
        planned_price=planned_price,
        planned_shares=planned_shares,
    )
    return _append(Path(path), lambda _: payload)


def append_filled(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    next_open: float,
    actual_time: str,
    actual_price: float,
    actual_shares: int,
    broker_order_id: str | None = None,
) -> JournalRecord:
    """Append an actual manual fill with slippage versus the observed next open."""

    open_price = _positive_number(next_open, field="next_open")
    fill_price = _positive_number(actual_price, field="actual_price")
    shares = _positive_shares(actual_shares, field="actual_shares")

    def payload(records: tuple[JournalRecord, ...]) -> dict[str, Any]:
        plan = next(
            (item for item in records if item.plan_id == plan_id and item.status is JournalStatus.PLANNED),
            None,
        )
        if plan is None or plan.side is None:
            raise ValueError("execution journal fill references an unknown plan")
        direction = 1.0 if plan.side == "BUY" else -1.0
        per_share = direction * (fill_price - open_price)
        return event_payload(
            status=JournalStatus.FILLED,
            plan_id=plan_id,
            recorded_at=recorded_at,
            decision_date=plan.decision_date,
            symbol=plan.symbol,
            side=plan.side,
            planned_weight=plan.planned_weight,
            planned_price=plan.planned_price,
            planned_shares=plan.planned_shares,
            next_open=open_price,
            actual_time=actual_time,
            actual_price=fill_price,
            actual_shares=shares,
            slippage_per_share=per_share,
            slippage_bps=per_share / open_price * 10_000.0,
            slippage_value=per_share * shares,
            broker_order_id=broker_order_id,
        )

    return _append(Path(path), payload)


def append_skipped(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    next_open: float,
    manual_skip: str,
) -> JournalRecord:
    """Append an explicit manual decision not to execute the remaining plan."""

    def payload(records: tuple[JournalRecord, ...]) -> dict[str, Any]:
        plan = next(
            (item for item in records if item.plan_id == plan_id and item.status is JournalStatus.PLANNED),
            None,
        )
        if plan is None:
            raise ValueError("execution journal skip references an unknown plan")
        return event_payload(
            status=JournalStatus.SKIPPED,
            plan_id=plan_id,
            recorded_at=recorded_at,
            decision_date=plan.decision_date,
            symbol=plan.symbol,
            side=plan.side,
            planned_weight=plan.planned_weight,
            planned_price=plan.planned_price,
            planned_shares=plan.planned_shares,
            next_open=next_open,
            manual_skip=manual_skip,
        )

    return _append(Path(path), payload)


def migrate_v1_journal(
    source: str | Path,
    destination: str | Path,
) -> tuple[JournalRecord, ...]:
    """Explicitly migrate a verified historical v1 journal to canonical v2 bytes."""

    source_path = Path(source)
    records = read_execution_journal(source_path)
    if any(record.schema_version != 1 for record in records):
        raise ValueError("execution journal migration source must contain only v1 records")
    plans: dict[str, JournalRecord] = {}
    migrated: list[JournalRecord] = []
    encoded_rows: list[bytes] = []
    previous = _ZERO_HASH
    for sequence, record in enumerate(records, start=1):
        if record.status is JournalStatus.PLANNED:
            plans[record.plan_id] = record
            plan = record
        else:
            plan = plans[record.plan_id]
        payload = event_payload(
            status=record.status,
            plan_id=record.plan_id,
            recorded_at=record.recorded_at,
            decision_date=plan.decision_date,
            symbol=plan.symbol,
            side=plan.side,
            planned_weight=plan.planned_weight,
            planned_price=plan.planned_price,
            planned_shares=plan.planned_shares,
            next_open=record.next_open,
            actual_time=record.actual_time,
            actual_price=record.actual_price,
            actual_shares=record.actual_shares,
            manual_skip=record.manual_skip,
            slippage_per_share=record.slippage_per_share,
            slippage_bps=record.slippage_bps,
            slippage_value=record.slippage_value,
        )
        migrated_record, encoded = encode_v2_record(
            payload,
            previous=previous,
            sequence=sequence,
        )
        migrated.append(migrated_record)
        encoded_rows.append(encoded)
        previous = migrated_record.record_sha256
    validate_lifecycle(migrated)
    atomic_write_bytes(
        destination,
        b"".join(encoded_rows),
        protected_paths=(source_path,),
    )
    return tuple(migrated)
