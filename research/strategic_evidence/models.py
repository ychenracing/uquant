"""Shared immutable primitives for strategic evidence artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_sha256(payload: dict[str, Any]) -> str:
    """Seal canonical JSON while excluding the self-referential envelope field."""

    normalized = {key: value for key, value in payload.items() if key != "payload_sha256"}
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_sha256(value: object, *, field: str) -> str:
    """Return a lowercase SHA-256 string or fail closed."""

    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def require_git_sha(value: object, *, field: str) -> str:
    """Return a full 40-character Git object ID or fail closed."""

    if not isinstance(value, str) or len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a full lowercase Git SHA")
    return value


__all__ = ("canonical_sha256", "require_git_sha", "require_sha256")
