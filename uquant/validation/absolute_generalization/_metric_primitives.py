"""Strict scalar and replay-payload parsing shared by validation owners."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import cast

from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads

from .replay import AbsoluteGeneralizationReplayPayload


def metric_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Mapping[str, object], value)


def metric_sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Sequence[object], value)


def metric_text(value: object, *, label: str, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"absolute generalization {label} is malformed")
    return value


def metric_integer(value: object, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"absolute generalization {label} is malformed")
    return value


def metric_number(value: object, *, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"absolute generalization {label} is malformed")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"absolute generalization {label} is malformed")
    return number


def metric_iso_session(value: object, *, label: str, empty: bool = False) -> str:
    text = metric_text(value, label=label, empty=empty)
    if not text and empty:
        return text
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"absolute generalization {label} is malformed") from exc
    return text


def metric_rows(value: object, *, label: str) -> tuple[Mapping[str, object], ...]:
    return tuple(
        metric_mapping(item, label=label)
        for item in metric_sequence(value, label=label)
    )


def metric_payload_mapping(
    payload: AbsoluteGeneralizationReplayPayload,
    *,
    label: str,
) -> Mapping[str, object]:
    if type(payload) is not AbsoluteGeneralizationReplayPayload:
        raise ValueError(f"absolute generalization {label} payload type differs")
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise ValueError(f"absolute generalization {label} payload digest differs")
    raw = strict_json_loads(payload.canonical_json)
    if canonical_json_bytes(raw) != payload.canonical_json:
        raise ValueError(f"absolute generalization {label} payload is not canonical")
    return metric_mapping(raw, label=label)


def metric_positive_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) and number > 0.0 else 0.0


def metric_stable_ids(
    rows: Sequence[Mapping[str, object]],
    *,
    field: str,
    label: str,
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        stable_id = metric_text(row.get(field), label=f"{label} {field}")
        if stable_id in result:
            raise ValueError(f"absolute generalization duplicate {label} identity")
        result[stable_id] = row
    return result


def metric_trace_row(
    *,
    session: str,
    decision: Mapping[str, object],
    qualification_coverage: float,
) -> Mapping[str, object]:
    return {
        "session": session,
        "opportunity": metric_text(decision.get("opportunity"), label="opportunity"),
        "risk": metric_mapping(
            decision.get("risk_summary", {}), label="decision risk summary"
        ),
        "target_gross": metric_number(
            decision.get("target_gross"), label="target gross"
        ),
        "targets": metric_rows(decision.get("targets", ()), label="decision targets"),
        "orders": metric_rows(
            decision.get("pending_orders", ()), label="decision orders"
        ),
        "qualification_coverage": qualification_coverage,
    }


__all__ = (
    "metric_integer",
    "metric_iso_session",
    "metric_mapping",
    "metric_number",
    "metric_payload_mapping",
    "metric_positive_number",
    "metric_rows",
    "metric_sequence",
    "metric_stable_ids",
    "metric_text",
    "metric_trace_row",
)
