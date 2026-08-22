"""Read-only compatibility facade for historical v1 execution journals."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .observation.execution_journal import models as _models
from .observation.execution_journal.checkpoint import (
    execution_journal_checkpoint as _canonical_checkpoint,
)
from .observation.execution_journal.models import (
    JournalCheckpoint as _CanonicalCheckpoint,
)
from .observation.execution_journal.store import (
    read_execution_journal as _read_canonical_journal,
)

JournalCheckpoint = _models.LegacyJournalCheckpoint
JournalRecord = _models.LegacyJournalRecord
JournalStatus = _models.LegacyJournalStatus
_PLAN_ID = _models._PLAN_ID
_SHA256 = _models._SHA256
_SYMBOL = _models._SYMBOL
_ZERO_HASH = _models._ZERO_HASH

__all__ = (  # noqa: RUF022 - frozen public-name order
    "JournalCheckpoint",
    "JournalRecord",
    "JournalStatus",
    "_PLAN_ID",
    "_SHA256",
    "_SYMBOL",
    "_ZERO_HASH",
    "append_filled",
    "append_planned",
    "append_skipped",
    "execution_journal_checkpoint",
    "read_execution_journal",
    "record_to_dict",
)


for _type, _name in (
    (JournalCheckpoint, "JournalCheckpoint"),
    (JournalRecord, "JournalRecord"),
    (JournalStatus, "JournalStatus"),
):
    _type.__name__ = _name
    _type.__qualname__ = _name
    _type.__module__ = __name__
JournalRecord.__dataclass_fields__["status"].type = "JournalStatus"
JournalRecord.__init__.__annotations__["status"] = "JournalStatus"


def _legacy_record(record: Any) -> JournalRecord:
    return JournalRecord(
        schema_version=record.schema_version,
        sequence=record.sequence,
        status=JournalStatus(record.status.value),
        plan_id=record.plan_id,
        recorded_at=record.recorded_at,
        symbol=record.symbol,
        side=record.side,
        planned_price=record.planned_price,
        planned_shares=record.planned_shares,
        next_open=record.next_open,
        actual_time=record.actual_time,
        actual_price=record.actual_price,
        actual_shares=record.actual_shares,
        manual_skip=record.manual_skip,
        slippage_per_share=record.slippage_per_share,
        slippage_bps=record.slippage_bps,
        slippage_value=record.slippage_value,
        previous_sha256=record.previous_sha256,
        record_sha256=record.record_sha256,
    )


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
    """Reject historical v1 writes; use the canonical v2 journal."""

    raise RuntimeError("v1 execution journal is read-only")


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
    """Reject historical v1 writes; use the canonical v2 journal."""

    raise RuntimeError("v1 execution journal is read-only")


def append_skipped(
    path: str | Path,
    *,
    plan_id: str,
    recorded_at: str,
    next_open: float,
    manual_skip: str,
) -> JournalRecord:
    """Reject historical v1 writes; use the canonical v2 journal."""

    raise RuntimeError("v1 execution journal is read-only")


def execution_journal_checkpoint(
    records: tuple[JournalRecord, ...],
) -> JournalCheckpoint:
    """Return a frozen-shape checkpoint for historical v1 records."""

    canonical = _canonical_checkpoint(cast("tuple[_models.JournalRecord, ...]", records))
    return JournalCheckpoint(
        schema_version=canonical.schema_version,
        sequence=canonical.sequence,
        record_sha256=canonical.record_sha256,
    )


def read_execution_journal(
    path: str | Path,
    *,
    trusted_checkpoint: JournalCheckpoint | None = None,
) -> tuple[JournalRecord, ...]:
    """Read and verify a historical v1 journal without permitting mutation."""

    canonical_checkpoint = (
        None
        if trusted_checkpoint is None
        else _CanonicalCheckpoint(
            schema_version=trusted_checkpoint.schema_version,
            sequence=trusted_checkpoint.sequence,
            record_sha256=trusted_checkpoint.record_sha256,
        )
    )
    records = _read_canonical_journal(path, trusted_checkpoint=canonical_checkpoint)
    if any(record.schema_version != 1 for record in records):
        raise ValueError("v1 execution journal facade cannot read v2 records")
    return tuple(_legacy_record(record) for record in records)


def record_to_dict(record: JournalRecord) -> dict[str, Any]:
    """Return a stable JSON-compatible historical v1 record."""

    payload = asdict(record)
    payload["status"] = record.status.value
    return payload
