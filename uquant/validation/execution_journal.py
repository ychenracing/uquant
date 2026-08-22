"""Append-only observational execution journal with no strategy dependencies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$")
_BROKER_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ZERO_HASH = "0" * 64

_V1_FIELDS = {
    "schema_version", "sequence", "status", "plan_id", "recorded_at",
    "symbol", "side", "planned_price", "planned_shares", "next_open",
    "actual_time", "actual_price", "actual_shares", "manual_skip",
    "slippage_per_share", "slippage_bps", "slippage_value",
    "previous_sha256", "record_sha256",
}
_V2_FIELDS = {
    "schema_version", "sequence", "status", "plan_id", "recorded_at",
    "decision_date", "planned_symbol", "planned_side", "planned_weight",
    "planned_price_reference", "planned_shares", "next_open",
    "actual_fill_time", "actual_fill_price", "actual_fill_shares",
    "manual_skip", "manual_skip_reason", "realized_slippage",
    "slippage_per_share", "slippage_bps", "broker_order_id",
    "previous_record_hash", "record_hash",
}


class JournalStatus(str, Enum):
    """Allowed immutable journal event kinds."""

    PLANNED = "PLANNED"
    FILLED = "FILLED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One validated record in a hash-chained JSONL journal."""

    schema_version: int
    sequence: int
    status: JournalStatus
    plan_id: str
    recorded_at: str
    decision_date: str
    symbol: str | None
    side: str | None
    planned_weight: float | None
    planned_price: float | None
    planned_shares: int | None
    next_open: float | None
    actual_time: str | None
    actual_price: float | None
    actual_shares: int | None
    manual_skip: str | None
    slippage_per_share: float | None
    slippage_bps: float | None
    slippage_value: float | None
    broker_order_id: str | None
    previous_sha256: str
    record_sha256: str


@dataclass(frozen=True, slots=True)
class JournalCheckpoint:
    """Externally retained journal position used to verify later readback."""

    schema_version: int
    sequence: int
    record_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("trusted checkpoint schema is malformed")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("trusted checkpoint sequence is malformed")
        if not _SHA256.fullmatch(self.record_sha256):
            raise ValueError("trusted checkpoint hash is malformed")
        if self.sequence == 0 and self.record_sha256 != _ZERO_HASH:
            raise ValueError("trusted empty checkpoint hash is malformed")


def _canonical_bytes(value: dict[str, Any], *, omit_hash: bool = False) -> bytes:
    hash_field = "record_hash" if value.get("schema_version") == 2 else "record_sha256"
    payload = {key: item for key, item in value.items() if key != hash_field} if omit_hash else value
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash_record(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value, omit_hash=True)).hexdigest()


def _timestamp(value: str, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"journal {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"journal {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"journal {field} must include a UTC offset")
    return parsed


def _date(value: str, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"journal {field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"journal {field} must be an ISO date") from exc


def _positive_number(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"journal {field} must be finite")
    converted = float(value)
    if converted <= 0:
        raise ValueError(f"journal {field} must be positive")
    return converted


def _positive_shares(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"journal {field} must be a positive integer")
    return value


def _decode_record(raw: Any, *, previous: str, sequence: int) -> JournalRecord:
    if not isinstance(raw, dict):
        raise ValueError("execution journal record schema is malformed")
    schema_version = raw.get("schema_version")
    expected_fields = _V2_FIELDS if schema_version == 2 else _V1_FIELDS
    if schema_version not in {1, 2} or set(raw) != expected_fields:
        raise ValueError("execution journal record schema is malformed")
    if raw["sequence"] != sequence:
        raise ValueError("execution journal sequence is malformed")
    previous_field = "previous_record_hash" if schema_version == 2 else "previous_sha256"
    record_field = "record_hash" if schema_version == 2 else "record_sha256"
    if raw[previous_field] != previous or not _SHA256.fullmatch(str(raw[record_field])):
        raise ValueError("execution journal hash chain is malformed")
    if raw[record_field] != _hash_record(raw):
        raise ValueError("execution journal record hash is invalid")
    try:
        status = JournalStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("execution journal status is invalid") from exc
    if schema_version == 1:
        recorded_date = _timestamp(raw["recorded_at"], field="recorded_at").date().isoformat()
        record = JournalRecord(
            **{**raw, "status": status},
            decision_date=recorded_date,
            planned_weight=None,
            broker_order_id=None,
        )
    else:
        record = JournalRecord(
            schema_version=2,
            sequence=raw["sequence"],
            status=status,
            plan_id=raw["plan_id"],
            recorded_at=raw["recorded_at"],
            decision_date=raw["decision_date"],
            symbol=raw["planned_symbol"],
            side=raw["planned_side"],
            planned_weight=raw["planned_weight"],
            planned_price=raw["planned_price_reference"],
            planned_shares=raw["planned_shares"],
            next_open=raw["next_open"],
            actual_time=raw["actual_fill_time"],
            actual_price=raw["actual_fill_price"],
            actual_shares=raw["actual_fill_shares"],
            manual_skip=raw["manual_skip_reason"] if raw["manual_skip"] else None,
            slippage_per_share=raw["slippage_per_share"],
            slippage_bps=raw["slippage_bps"],
            slippage_value=raw["realized_slippage"],
            broker_order_id=raw["broker_order_id"],
            previous_sha256=raw["previous_record_hash"],
            record_sha256=raw["record_hash"],
        )
    _validate_record(record)
    return record


def _validate_record(record: JournalRecord) -> None:
    if not _PLAN_ID.fullmatch(record.plan_id):
        raise ValueError("execution journal plan_id is malformed")
    recorded_at = _timestamp(record.recorded_at, field="recorded_at")
    decision_date = _date(record.decision_date, field="decision_date")
    if decision_date > recorded_at.date():
        raise ValueError("execution journal decision_date follows recorded_at")
    if record.planned_weight is not None and (
        isinstance(record.planned_weight, bool)
        or not isinstance(record.planned_weight, (int, float))
        or not math.isfinite(float(record.planned_weight))
        or not 0.0 <= float(record.planned_weight) <= 1.0
    ):
        raise ValueError("journal planned_weight must be finite and between zero and one")
    if record.broker_order_id is not None and not _BROKER_ORDER_ID.fullmatch(record.broker_order_id):
        raise ValueError("journal broker_order_id is malformed")
    if record.status is JournalStatus.PLANNED:
        if record.symbol is None or not _SYMBOL.fullmatch(record.symbol) or record.side not in {"BUY", "SELL"}:
            raise ValueError("planned journal symbol or side is malformed")
        if record.planned_price is None or record.planned_shares is None:
            raise ValueError("planned journal event lacks price or shares")
        _positive_number(record.planned_price, field="planned_price")
        _positive_shares(record.planned_shares, field="planned_shares")
        expected_null = (
            record.next_open,
            record.actual_time,
            record.actual_price,
            record.actual_shares,
            record.manual_skip,
            record.slippage_per_share,
            record.slippage_bps,
            record.slippage_value,
            record.broker_order_id,
        )
        if any(value is not None for value in expected_null):
            raise ValueError("planned journal event contains execution data")
    elif record.status is JournalStatus.FILLED:
        if record.next_open is None or record.actual_time is None or record.actual_price is None or record.actual_shares is None:
            raise ValueError("filled journal event lacks execution data")
        _positive_number(record.next_open, field="next_open")
        _timestamp(record.actual_time, field="actual_time")
        _positive_number(record.actual_price, field="actual_price")
        _positive_shares(record.actual_shares, field="actual_shares")
        if any(value is None for value in (record.slippage_per_share, record.slippage_bps, record.slippage_value)):
            raise ValueError("filled journal event lacks derived slippage")
        for field in ("slippage_per_share", "slippage_bps", "slippage_value"):
            value = getattr(record, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"journal {field} must be finite")
        if record.schema_version == 1:
            if any(value is not None for value in (record.symbol, record.side, record.planned_price, record.planned_shares, record.manual_skip)):
                raise ValueError("filled journal event duplicates planned or skip data")
        elif (
            record.symbol is None
            or record.side is None
            or record.planned_price is None
            or record.planned_shares is None
            or record.manual_skip is not None
        ):
            raise ValueError("filled journal event lacks its immutable plan identity")
    else:
        if not isinstance(record.manual_skip, str) or not record.manual_skip.strip():
            raise ValueError("skipped journal event requires a manual skip reason")
        if record.next_open is None:
            raise ValueError("skipped journal event requires the observed next open")
        _positive_number(record.next_open, field="next_open")
        unrelated = [
            record.actual_time,
            record.actual_price,
            record.actual_shares,
            record.slippage_per_share,
            record.slippage_bps,
            record.slippage_value,
            record.broker_order_id,
        ]
        if record.schema_version == 1:
            unrelated.extend((record.symbol, record.side, record.planned_price, record.planned_shares))
        elif any(value is None for value in (record.symbol, record.side, record.planned_price, record.planned_shares)):
            raise ValueError("skipped journal event lacks its immutable plan identity")
        if any(
            value is not None
            for value in unrelated
        ):
            raise ValueError("skipped journal event contains unrelated data")


def _decode_journal_text(text: str) -> tuple[JournalRecord, ...]:
    records: list[JournalRecord] = []
    previous = _ZERO_HASH
    lines = text.splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError("execution journal contains an empty record")
    for sequence, line in enumerate(lines, start=1):
        try:
            raw = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("execution journal contains invalid JSON") from exc
        record = _decode_record(raw, previous=previous, sequence=sequence)
        records.append(record)
        previous = record.record_sha256
    _validate_lifecycle(records)
    return tuple(records)


def _validate_lifecycle(records: list[JournalRecord] | tuple[JournalRecord, ...]) -> None:
    plans: dict[str, JournalRecord] = {}
    filled_shares: dict[str, int] = {}
    plan_opens: dict[str, float] = {}
    terminal: set[str] = set()
    prior_recorded: datetime | None = None
    for record in records:
        recorded = _timestamp(record.recorded_at, field="recorded_at")
        if prior_recorded is not None and recorded < prior_recorded:
            raise ValueError("execution journal chronology is not monotonic")
        prior_recorded = recorded
        if record.status is JournalStatus.PLANNED:
            if record.plan_id in plans:
                raise ValueError("execution journal plan_id is duplicated")
            plans[record.plan_id] = record
            filled_shares[record.plan_id] = 0
        else:
            plan = plans.get(record.plan_id)
            if plan is None:
                raise ValueError("execution journal event references an unknown plan")
            if record.plan_id in terminal:
                raise ValueError("execution journal plan is already terminal")
            planned_at = _timestamp(plan.recorded_at, field="recorded_at")
            if recorded < planned_at:
                raise ValueError("execution journal event chronology predates its plan")
            if record.schema_version == 2 and (
                record.decision_date != plan.decision_date
                or record.symbol != plan.symbol
                or record.side != plan.side
                or record.planned_weight != plan.planned_weight
                or record.planned_price != plan.planned_price
                or record.planned_shares != plan.planned_shares
            ):
                raise ValueError("execution journal event plan identity differs from its plan")
            next_open = cast(float, record.next_open)
            prior_open = plan_opens.setdefault(record.plan_id, next_open)
            if next_open != prior_open:
                raise ValueError("execution journal next open differs within one plan")
            if record.status is JournalStatus.FILLED:
                actual_time_raw = cast(str, record.actual_time)
                actual_time = _timestamp(actual_time_raw, field="actual_time")
                if actual_time < planned_at or actual_time > recorded:
                    raise ValueError("execution journal fill chronology is invalid")
                actual_shares = cast(int, record.actual_shares)
                planned_shares = cast(int, plan.planned_shares)
                total = filled_shares[record.plan_id] + actual_shares
                if total > planned_shares:
                    raise ValueError("execution journal fills exceed planned shares")
                filled_shares[record.plan_id] = total
                if total == planned_shares:
                    terminal.add(record.plan_id)
                side = cast(str, plan.side)
                actual_price = cast(float, record.actual_price)
                direction = 1.0 if side == "BUY" else -1.0
                per_share = direction * (actual_price - next_open)
                expected = (
                    per_share,
                    per_share / next_open * 10_000.0,
                    per_share * actual_shares,
                )
                observed = (
                    record.slippage_per_share,
                    record.slippage_bps,
                    record.slippage_value,
                )
                if any(
                    value is None
                    or not math.isclose(float(value), wanted, rel_tol=1e-12, abs_tol=1e-12)
                    for value, wanted in zip(observed, expected, strict=True)
                ):
                    raise ValueError("execution journal derived slippage is invalid")
            else:
                terminal.add(record.plan_id)


def _read_descriptor(descriptor: int) -> tuple[JournalRecord, ...]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as handle:
            return _decode_journal_text(handle.read())
    except (OSError, UnicodeError) as exc:
        raise ValueError("execution journal is unreadable") from exc


def _verify_checkpoint(
    records: tuple[JournalRecord, ...],
    trusted_checkpoint: JournalCheckpoint | None,
) -> None:
    if trusted_checkpoint is None:
        return
    if len(records) < trusted_checkpoint.sequence:
        raise ValueError("execution journal is behind the trusted checkpoint")
    if trusted_checkpoint.sequence == 0:
        return
    retained = records[trusted_checkpoint.sequence - 1]
    if retained.record_sha256 != trusted_checkpoint.record_sha256:
        raise ValueError("execution journal differs from the trusted checkpoint")


def execution_journal_checkpoint(
    records: tuple[JournalRecord, ...],
) -> JournalCheckpoint:
    """Return a tail checkpoint that must be retained outside the journal."""

    if not records:
        return JournalCheckpoint(
            schema_version=1,
            sequence=0,
            record_sha256=_ZERO_HASH,
        )
    return JournalCheckpoint(
        schema_version=1,
        sequence=len(records),
        record_sha256=records[-1].record_sha256,
    )


def read_execution_journal(
    path: str | Path,
    *,
    trusted_checkpoint: JournalCheckpoint | None = None,
) -> tuple[JournalRecord, ...]:
    """Validate the journal, plus continuity from an externally retained tail."""

    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except FileNotFoundError:
        records: tuple[JournalRecord, ...] = ()
        _verify_checkpoint(records, trusted_checkpoint)
        return records
    except OSError as exc:
        raise ValueError("execution journal must be a regular file") from exc
    try:
        acquire_file_lock(descriptor, FileLockMode.SHARED)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("execution journal must be a regular file")
        records = _read_descriptor(descriptor)
        _verify_checkpoint(records, trusted_checkpoint)
        return records
    finally:
        with suppress(OSError):
            release_file_lock(descriptor)
        os.close(descriptor)


PayloadFactory = Callable[[tuple[JournalRecord, ...]], dict[str, Any]]


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
        payload = payload_factory(records)
        payload.update(
            schema_version=2,
            sequence=len(records) + 1,
            previous_record_hash=records[-1].record_sha256 if records else _ZERO_HASH,
        )
        payload["record_hash"] = _hash_record(payload)
        record = _decode_record(
            payload,
            previous=payload["previous_record_hash"],
            sequence=payload["sequence"],
        )
        _validate_lifecycle([*records, record])
        encoded = _canonical_bytes(payload) + b"\n"
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
        _fsync_directory(path.parent)
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
                    f"execution journal {label} also failed: "
                    f"{type(failure).__name__}: {failure}"
                )
        elif cleanup_errors:
            _, first_failure = cleanup_errors[0]
            for later_label, later_error in cleanup_errors[1:]:
                first_failure.add_note(
                    f"execution journal {later_label} also failed: "
                    f"{type(later_error).__name__}: {later_error}"
                )
            raise first_failure


def _event_payload(
    *,
    status: JournalStatus,
    plan_id: str,
    recorded_at: str,
    decision_date: str,
    symbol: str | None = None,
    side: str | None = None,
    planned_weight: float | None = None,
    planned_price: float | None = None,
    planned_shares: int | None = None,
    next_open: float | None = None,
    actual_time: str | None = None,
    actual_price: float | None = None,
    actual_shares: int | None = None,
    manual_skip: str | None = None,
    slippage_per_share: float | None = None,
    slippage_bps: float | None = None,
    slippage_value: float | None = None,
    broker_order_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status.value,
        "plan_id": plan_id,
        "recorded_at": recorded_at,
        "decision_date": decision_date,
        "planned_symbol": symbol,
        "planned_side": side,
        "planned_weight": planned_weight,
        "planned_price_reference": planned_price,
        "planned_shares": planned_shares,
        "next_open": next_open,
        "actual_fill_time": actual_time,
        "actual_fill_price": actual_price,
        "actual_fill_shares": actual_shares,
        "manual_skip": manual_skip is not None,
        "manual_skip_reason": manual_skip,
        "realized_slippage": slippage_value,
        "slippage_per_share": slippage_per_share,
        "slippage_bps": slippage_bps,
        "broker_order_id": broker_order_id,
    }


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

    normalized_decision_date = decision_date or _timestamp(
        recorded_at,
        field="recorded_at",
    ).date().isoformat()
    payload = _event_payload(
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
            (
                item
                for item in records
                if item.plan_id == plan_id and item.status is JournalStatus.PLANNED
            ),
            None,
        )
        if plan is None or plan.side is None:
            raise ValueError("execution journal fill references an unknown plan")
        direction = 1.0 if plan.side == "BUY" else -1.0
        per_share = direction * (fill_price - open_price)
        return _event_payload(
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
            (
                item
                for item in records
                if item.plan_id == plan_id and item.status is JournalStatus.PLANNED
            ),
            None,
        )
        if plan is None:
            raise ValueError("execution journal skip references an unknown plan")
        return _event_payload(
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


def record_to_dict(record: JournalRecord) -> dict[str, Any]:
    """Return a stable JSON-compatible record for CLI output."""

    if record.schema_version == 1:
        return {
            "schema_version": 1,
            "sequence": record.sequence,
            "status": record.status.value,
            "plan_id": record.plan_id,
            "recorded_at": record.recorded_at,
            "symbol": record.symbol,
            "side": record.side,
            "planned_price": record.planned_price,
            "planned_shares": record.planned_shares,
            "next_open": record.next_open,
            "actual_time": record.actual_time,
            "actual_price": record.actual_price,
            "actual_shares": record.actual_shares,
            "manual_skip": record.manual_skip,
            "slippage_per_share": record.slippage_per_share,
            "slippage_bps": record.slippage_bps,
            "slippage_value": record.slippage_value,
            "previous_sha256": record.previous_sha256,
            "record_sha256": record.record_sha256,
        }
    return {
        "schema_version": 2,
        "sequence": record.sequence,
        "status": record.status.value,
        "plan_id": record.plan_id,
        "recorded_at": record.recorded_at,
        "decision_date": record.decision_date,
        "planned_symbol": record.symbol,
        "planned_side": record.side,
        "planned_weight": record.planned_weight,
        "planned_price_reference": record.planned_price,
        "planned_shares": record.planned_shares,
        "next_open": record.next_open,
        "actual_fill_time": record.actual_time,
        "actual_fill_price": record.actual_price,
        "actual_fill_shares": record.actual_shares,
        "manual_skip": record.manual_skip is not None,
        "manual_skip_reason": record.manual_skip,
        "realized_slippage": record.slippage_value,
        "slippage_per_share": record.slippage_per_share,
        "slippage_bps": record.slippage_bps,
        "broker_order_id": record.broker_order_id,
        "previous_record_hash": record.previous_sha256,
        "record_hash": record.record_sha256,
    }


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render v1/v2 observational events without deriving strategy intent."""

    lines = [
        "# Future Holdout Manual Execution Journal",
        "",
        "| Seq | Decision | Plan | Status | Symbol | Side | Weight | Planned | Next open | Actual | Shares | Slippage | Broker/Note |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    plans: dict[str, JournalRecord] = {}

    def markdown_cell(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r\n", "<br>")
            .replace("\n", "<br>")
            .replace("\r", "<br>")
        )

    for item in records:
        if item.status is JournalStatus.PLANNED:
            plans[item.plan_id] = item
        plan = plans.get(item.plan_id)
        slippage = "" if item.slippage_bps is None else f"{item.slippage_bps:.4f} bps"
        note = item.manual_skip or item.broker_order_id or ""
        lines.append(
            "| "
            + " | ".join(
                (
                    str(item.sequence),
                    item.decision_date,
                    item.plan_id,
                    item.status.value,
                    "" if plan is None or plan.symbol is None else plan.symbol,
                    "" if plan is None or plan.side is None else plan.side,
                    "" if plan is None or plan.planned_weight is None else f"{plan.planned_weight:.4f}",
                    "" if plan is None or plan.planned_price is None else f"{plan.planned_price:.4f}",
                    "" if item.next_open is None else f"{item.next_open:.4f}",
                    "" if item.actual_price is None else f"{item.actual_price:.4f}",
                    "" if item.actual_shares is None else str(item.actual_shares),
                    slippage,
                    markdown_cell(note),
                )
            )
            + " |"
        )
    if not records:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)
