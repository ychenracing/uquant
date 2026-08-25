"""Canonical v2 execution-journal codec and sole append encoding."""

from __future__ import annotations

from typing import Any

from .codec_v1 import canonical_record_bytes, decode_v1_record, hash_record
from .lifecycle import validate_journal_record as _validate_record
from .models import SHA256_PATTERN as _SHA256
from .models import V1_FIELDS as _V1_FIELDS
from .models import V2_FIELDS as _V2_FIELDS
from .models import JournalRecord, JournalStatus


def decode_v2_record(raw: Any, *, previous: str, sequence: int) -> JournalRecord:
    if not isinstance(raw, dict) or raw.get("schema_version") != 2 or set(raw) != _V2_FIELDS:
        raise ValueError("execution journal record schema is malformed")
    if raw["sequence"] != sequence:
        raise ValueError("execution journal sequence is malformed")
    if raw["previous_record_hash"] != previous or not _SHA256.fullmatch(str(raw["record_hash"])):
        raise ValueError("execution journal hash chain is malformed")
    if raw["record_hash"] != hash_record(raw):
        raise ValueError("execution journal record hash is invalid")
    try:
        status = JournalStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("execution journal status is invalid") from exc
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


def decode_record(raw: Any, *, previous: str, sequence: int) -> JournalRecord:
    if not isinstance(raw, dict):
        raise ValueError("execution journal record schema is malformed")
    schema_version = raw.get("schema_version")
    expected_fields = _V2_FIELDS if schema_version == 2 else _V1_FIELDS
    if schema_version not in {1, 2} or set(raw) != expected_fields:
        raise ValueError("execution journal record schema is malformed")
    if schema_version == 1:
        return decode_v1_record(raw, previous=previous, sequence=sequence)
    return decode_v2_record(raw, previous=previous, sequence=sequence)


def event_payload(
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


def encode_v2_record(
    payload: dict[str, Any],
    *,
    previous: str,
    sequence: int,
) -> tuple[JournalRecord, bytes]:
    """Seal and validate one v2 record; no v1 encoding path exists."""

    encoded_payload = dict(payload)
    encoded_payload.update(
        schema_version=2,
        sequence=sequence,
        previous_record_hash=previous,
    )
    encoded_payload["record_hash"] = hash_record(encoded_payload)
    record = decode_v2_record(
        encoded_payload,
        previous=previous,
        sequence=sequence,
    )
    return record, canonical_record_bytes(encoded_payload) + b"\n"


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
