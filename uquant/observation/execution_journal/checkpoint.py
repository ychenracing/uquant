"""Execution-journal checkpoint creation and continuity verification."""

from __future__ import annotations

from .models import ZERO_HASH as _ZERO_HASH
from .models import JournalCheckpoint, JournalRecord


def verify_checkpoint(
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
