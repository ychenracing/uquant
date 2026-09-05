"""Fail-closed primitive decoding for acceptance evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, is_dataclass
from datetime import date
from typing import cast

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTITY_ID = re.compile(r"^(?:epoch|fill|grant|order|target|rearm)_[0-9a-f]{64}$")


def evidence_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return cast(Mapping[str, object], value)


def evidence_json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return evidence_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): evidence_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [evidence_json_value(item) for item in value]
    return value


def evidence_sequence(value: object, *, label: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return cast(Sequence[object], value)


def evidence_fields(raw: Mapping[str, object], expected: Set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization {label} evidence fields differ")


def evidence_text(value: object, *, label: str, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return value


def evidence_integer(value: object, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return value


def evidence_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return number


def evidence_date(value: object, *, label: str) -> str:
    text = evidence_text(value, label=label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"absolute generalization {label} session is malformed") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"absolute generalization {label} session is malformed")
    return text


def evidence_sha(value: object, *, label: str) -> str:
    text = evidence_text(value, label=label)
    if not _SHA256.fullmatch(text):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return text


def entity(value: object, *, label: str, empty: bool = False) -> str:
    text = evidence_text(value, label=label, empty=empty)
    if text and not _ENTITY_ID.fullmatch(text):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return text


def predicate_rows(value: object, *, label: str) -> tuple[tuple[str, bool], ...]:
    rows = evidence_sequence(value, label=label)
    if not rows:
        raise ValueError(f"absolute generalization {label} evidence is empty")
    result: list[tuple[str, bool]] = []
    for item in rows:
        row = evidence_mapping(item, label=label)
        evidence_fields(row, {"code", "satisfied"}, label=label)
        code = evidence_text(row["code"], label=f"{label} predicate")
        satisfied = row["satisfied"]
        if type(satisfied) is not bool:
            raise ValueError(f"absolute generalization {label} predicate is malformed")
        result.append((code, satisfied))
    if len({code for code, _passed in result}) != len(result):
        raise ValueError(f"absolute generalization {label} predicate is duplicated")
    return tuple(result)


def strict_sessions(values: Sequence[str], *, label: str) -> None:
    if not values or tuple(values) != tuple(sorted(set(values))):
        raise ValueError(f"absolute generalization {label} sessions are not observed order")


