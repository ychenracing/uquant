"""Canonical execution-journal models and schema constants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


_PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


_SYMBOL = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$")


_BROKER_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


_ZERO_HASH = "0" * 64


_V1_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "status",
        "plan_id",
        "recorded_at",
        "symbol",
        "side",
        "planned_price",
        "planned_shares",
        "next_open",
        "actual_time",
        "actual_price",
        "actual_shares",
        "manual_skip",
        "slippage_per_share",
        "slippage_bps",
        "slippage_value",
        "previous_sha256",
        "record_sha256",
    }
)


_V2_FIELDS = frozenset(
    {
        "schema_version",
        "sequence",
        "status",
        "plan_id",
        "recorded_at",
        "decision_date",
        "planned_symbol",
        "planned_side",
        "planned_weight",
        "planned_price_reference",
        "planned_shares",
        "next_open",
        "actual_fill_time",
        "actual_fill_price",
        "actual_fill_shares",
        "manual_skip",
        "manual_skip_reason",
        "realized_slippage",
        "slippage_per_share",
        "slippage_bps",
        "broker_order_id",
        "previous_record_hash",
        "record_hash",
    }
)

BROKER_ORDER_ID_PATTERN = _BROKER_ORDER_ID
PLAN_ID_PATTERN = _PLAN_ID
SHA256_PATTERN = _SHA256
SYMBOL_PATTERN = _SYMBOL
V1_FIELDS = _V1_FIELDS
V2_FIELDS = _V2_FIELDS
ZERO_HASH = _ZERO_HASH


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


class LegacyJournalStatus(str, Enum):
    """Frozen status type exposed by the historical v1 facade."""

    PLANNED = "PLANNED"
    FILLED = "FILLED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class LegacyJournalRecord:
    """Frozen v1 record shape exposed by the historical facade."""

    schema_version: int
    sequence: int
    status: LegacyJournalStatus
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
class LegacyJournalCheckpoint:
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
