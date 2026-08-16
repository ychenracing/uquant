"""Append-only observational execution journal with no strategy dependencies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$")
_ZERO_HASH = "0" * 64


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
    symbol: str | None
    side: str | None
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
    previous_sha256: str
    record_sha256: str


def _canonical_bytes(value: dict[str, Any], *, omit_hash: bool = False) -> bytes:
    payload = {key: item for key, item in value.items() if key != "record_sha256"} if omit_hash else value
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash_record(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value, omit_hash=True)).hexdigest()


def _timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"journal {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"journal {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"journal {field} must include a UTC offset")
    return value


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
    if not isinstance(raw, dict) or set(raw) != set(JournalRecord.__dataclass_fields__):
        raise ValueError("execution journal record schema is malformed")
    if raw["schema_version"] != 1 or raw["sequence"] != sequence:
        raise ValueError("execution journal sequence is malformed")
    if raw["previous_sha256"] != previous or not _SHA256.fullmatch(str(raw["record_sha256"])):
        raise ValueError("execution journal hash chain is malformed")
    if raw["record_sha256"] != _hash_record(raw):
        raise ValueError("execution journal record hash is invalid")
    try:
        status = JournalStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("execution journal status is invalid") from exc
    record = JournalRecord(**{**raw, "status": status})
    _validate_record(record)
    return record


def _validate_record(record: JournalRecord) -> None:
    if not _PLAN_ID.fullmatch(record.plan_id):
        raise ValueError("execution journal plan_id is malformed")
    _timestamp(record.recorded_at, field="recorded_at")
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
        if any(value is not None for value in (record.symbol, record.side, record.planned_price, record.planned_shares, record.manual_skip)):
            raise ValueError("filled journal event duplicates planned or skip data")
    else:
        if not isinstance(record.manual_skip, str) or not record.manual_skip.strip():
            raise ValueError("skipped journal event requires a manual skip reason")
        if record.next_open is None:
            raise ValueError("skipped journal event requires the observed next open")
        _positive_number(record.next_open, field="next_open")
        if any(
            value is not None
            for value in (
                record.symbol,
                record.side,
                record.planned_price,
                record.planned_shares,
                record.actual_time,
                record.actual_price,
                record.actual_shares,
                record.slippage_per_share,
                record.slippage_bps,
                record.slippage_value,
            )
        ):
            raise ValueError("skipped journal event contains unrelated data")


def read_execution_journal(path: str | Path) -> tuple[JournalRecord, ...]:
    """Read and validate the complete append-only hash chain and lifecycle."""

    source = Path(path)
    if not source.exists():
        return ()
    if source.is_symlink() or not source.is_file():
        raise ValueError("execution journal must be a regular file")
    records: list[JournalRecord] = []
    previous = _ZERO_HASH
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("execution journal is unreadable") from exc
    if any(not line.strip() for line in lines):
        raise ValueError("execution journal contains an empty record")
    plans: dict[str, JournalRecord] = {}
    filled_shares: dict[str, int] = {}
    terminal: set[str] = set()
    for sequence, line in enumerate(lines, start=1):
        try:
            raw = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("execution journal contains invalid JSON") from exc
        record = _decode_record(raw, previous=previous, sequence=sequence)
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
            if record.status is JournalStatus.FILLED:
                assert record.actual_shares is not None
                total = filled_shares[record.plan_id] + record.actual_shares
                assert plan.planned_shares is not None
                if total > plan.planned_shares:
                    raise ValueError("execution journal fills exceed planned shares")
                filled_shares[record.plan_id] = total
                if total == plan.planned_shares:
                    terminal.add(record.plan_id)
            else:
                terminal.add(record.plan_id)
        records.append(record)
        previous = record.record_sha256
    return tuple(records)


def _append(path: Path, payload: dict[str, Any]) -> JournalRecord:
    records = read_execution_journal(path)
    payload.update(
        schema_version=1,
        sequence=len(records) + 1,
        previous_sha256=records[-1].record_sha256 if records else _ZERO_HASH,
    )
    payload["record_sha256"] = _hash_record(payload)
    record = _decode_record(
        payload,
        previous=payload["previous_sha256"],
        sequence=payload["sequence"],
    )
    matching = tuple(item for item in records if item.plan_id == record.plan_id)
    if record.status is JournalStatus.PLANNED:
        if matching:
            raise ValueError("execution journal plan_id is duplicated")
    else:
        plan = next(
            (item for item in matching if item.status is JournalStatus.PLANNED),
            None,
        )
        if plan is None:
            raise ValueError("execution journal event references an unknown plan")
        if any(item.status is JournalStatus.SKIPPED for item in matching):
            raise ValueError("execution journal plan is already terminal")
        prior_fills = tuple(item for item in matching if item.status is JournalStatus.FILLED)
        assert plan.planned_shares is not None
        filled = sum(item.actual_shares or 0 for item in prior_fills)
        if filled == plan.planned_shares:
            raise ValueError("execution journal plan is already terminal")
        if record.status is JournalStatus.FILLED:
            assert record.actual_shares is not None
            if filled + record.actual_shares > plan.planned_shares:
                raise ValueError("execution journal fills exceed planned shares")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "ab", closefd=True) as handle:
            handle.write(_canonical_bytes(payload) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    return record


def _event_payload(
    *,
    status: JournalStatus,
    plan_id: str,
    recorded_at: str,
    symbol: str | None = None,
    side: str | None = None,
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
) -> dict[str, Any]:
    return {
        "status": status.value,
        "plan_id": plan_id,
        "recorded_at": recorded_at,
        "symbol": symbol,
        "side": side,
        "planned_price": planned_price,
        "planned_shares": planned_shares,
        "next_open": next_open,
        "actual_time": actual_time,
        "actual_price": actual_price,
        "actual_shares": actual_shares,
        "manual_skip": manual_skip,
        "slippage_per_share": slippage_per_share,
        "slippage_bps": slippage_bps,
        "slippage_value": slippage_value,
    }


def append_planned(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    symbol: str,
    side: str,
    planned_price: float,
    planned_shares: int,
) -> JournalRecord:
    """Append one operator-authored next-open execution plan."""

    return _append(
        Path(path),
        _event_payload(
            status=JournalStatus.PLANNED,
            plan_id=plan_id,
            recorded_at=recorded_at,
            symbol=symbol,
            side=side,
            planned_price=planned_price,
            planned_shares=planned_shares,
        ),
    )


def append_filled(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    next_open: float,
    actual_time: str,
    actual_price: float,
    actual_shares: int,
) -> JournalRecord:
    """Append an actual manual fill with slippage versus the observed next open."""

    records = read_execution_journal(path)
    plan = next(
        (item for item in records if item.plan_id == plan_id and item.status is JournalStatus.PLANNED),
        None,
    )
    if plan is None or plan.side is None:
        raise ValueError("execution journal fill references an unknown plan")
    open_price = _positive_number(next_open, field="next_open")
    fill_price = _positive_number(actual_price, field="actual_price")
    shares = _positive_shares(actual_shares, field="actual_shares")
    direction = 1.0 if plan.side == "BUY" else -1.0
    per_share = direction * (fill_price - open_price)
    return _append(
        Path(path),
        _event_payload(
            status=JournalStatus.FILLED,
            plan_id=plan_id,
            recorded_at=recorded_at,
            next_open=open_price,
            actual_time=actual_time,
            actual_price=fill_price,
            actual_shares=shares,
            slippage_per_share=per_share,
            slippage_bps=per_share / open_price * 10_000.0,
            slippage_value=per_share * shares,
        ),
    )


def append_skipped(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    next_open: float,
    manual_skip: str,
) -> JournalRecord:
    """Append an explicit manual decision not to execute the remaining plan."""

    return _append(
        Path(path),
        _event_payload(
            status=JournalStatus.SKIPPED,
            plan_id=plan_id,
            recorded_at=recorded_at,
            next_open=next_open,
            manual_skip=manual_skip,
        ),
    )


def record_to_dict(record: JournalRecord) -> dict[str, Any]:
    """Return a stable JSON-compatible record for CLI output."""

    payload = asdict(record)
    payload["status"] = record.status.value
    return payload
