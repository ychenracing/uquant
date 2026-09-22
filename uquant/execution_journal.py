"""Read-only compatibility facade for historical v1 execution journals."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from .observation.execution_journal import models as _models
from .observation.execution_journal import store as _journal_store
from .observation.execution_journal.checkpoint import (
    execution_journal_checkpoint as _canonical_checkpoint,
)
from .observation.execution_journal.models import (
    JournalCheckpoint as _CanonicalCheckpoint,
)

_PLAN_ID = _models.PLAN_ID_PATTERN
_SHA256 = _models.SHA256_PATTERN
_SYMBOL = _models.SYMBOL_PATTERN
_ZERO_HASH = _models.ZERO_HASH

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


class JournalStatus(str, Enum):
    """Frozen status type exposed by the historical v1 facade."""

    PLANNED = "PLANNED"
    FILLED = "FILLED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """Frozen v1 record shape exposed by the historical facade."""

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


@dataclass(frozen=True, slots=True)
class JournalCheckpoint:
    """Frozen checkpoint shape exposed by the historical v1 facade."""

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
    records = _journal_store.read_legacy_v1_execution_journal(
        path,
        trusted_checkpoint=canonical_checkpoint,
    )
    return tuple(_legacy_record(record) for record in records)


def record_to_dict(record: JournalRecord) -> dict[str, Any]:
    """Return a stable JSON-compatible historical v1 record."""

    payload = asdict(record)
    payload["status"] = record.status.value
    return payload
