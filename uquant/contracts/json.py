"""Strict JSON decoding and one compact canonical UTF-8 encoding."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Never, cast


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_contract_json_constant(value: str) -> Never:
    raise ValueError(f"nonstandard JSON constant: {value}")


def strict_json_loads(document: str | bytes | bytearray) -> object:
    """Decode RFC JSON while rejecting duplicate keys and non-finite constants."""

    return cast(
        object,
        json.loads(
            document,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_contract_json_constant,
        ),
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode a JSON value to deterministic compact UTF-8 bytes."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    """Hash the canonical UTF-8 representation of a JSON value."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
