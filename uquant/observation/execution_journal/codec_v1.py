"""Read-only historical v1 execution-journal codec."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .lifecycle import journal_timestamp as _timestamp
from .lifecycle import validate_journal_record as _validate_record
from .lifecycle import validate_plan_id
from .models import SHA256_PATTERN as _SHA256
from .models import V1_FIELDS as _V1_FIELDS
from .models import JournalRecord, JournalStatus


def canonical_record_bytes(
    value: dict[str, Any],
    *,
    omit_hash: bool = False,
) -> bytes:
    """Encode existing v1/v2 record dictionaries without changing their schema."""

    hash_field = "record_hash" if value.get("schema_version") == 2 else "record_sha256"
    payload = {key: item for key, item in value.items() if key != hash_field} if omit_hash else value
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def hash_record(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_record_bytes(value, omit_hash=True)).hexdigest()


def _decode_v1_record(
    raw: Any,
    *,
    previous: str,
    sequence: int,
    legacy_contract: bool,
) -> JournalRecord:
    if legacy_contract:
        if not isinstance(raw, dict) or set(raw) != _V1_FIELDS:
            raise ValueError("execution journal record schema is malformed")
        if raw["schema_version"] != 1 or raw["sequence"] != sequence:
            raise ValueError("execution journal sequence is malformed")
    elif not isinstance(raw, dict) or raw.get("schema_version") != 1 or set(raw) != _V1_FIELDS:
        raise ValueError("execution journal record schema is malformed")
    elif raw["sequence"] != sequence:
        raise ValueError("execution journal sequence is malformed")
    if raw["previous_sha256"] != previous or not _SHA256.fullmatch(str(raw["record_sha256"])):
        raise ValueError("execution journal hash chain is malformed")
    if raw["record_sha256"] != hash_record(raw):
        raise ValueError("execution journal record hash is invalid")
    try:
        status = JournalStatus(raw["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("execution journal status is invalid") from exc
    if legacy_contract:
        validate_plan_id(raw["plan_id"])
    recorded_date = _timestamp(raw["recorded_at"], field="recorded_at").date().isoformat()
    record = JournalRecord(
        **{**raw, "status": status},
        decision_date=recorded_date,
        planned_weight=None,
        broker_order_id=None,
    )
    _validate_record(record, plan_id_validated=legacy_contract)
    return record


def decode_v1_record(raw: Any, *, previous: str, sequence: int) -> JournalRecord:
    """Decode and validate one historical v1 record without exposing a writer."""

    return _decode_v1_record(
        raw,
        previous=previous,
        sequence=sequence,
        legacy_contract=False,
    )


def decode_legacy_v1_record(raw: Any, *, previous: str, sequence: int) -> JournalRecord:
    """Decode with the frozen error precedence of the historical v1 facade."""

    return _decode_v1_record(
        raw,
        previous=previous,
        sequence=sequence,
        legacy_contract=True,
    )
