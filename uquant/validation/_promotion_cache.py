"""Atomic raw Performance units; every reuse requires the exact replay identity."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate cache key: {key}")
        result[key] = value
    return result


def replay_unit(
    directory: Path,
    *,
    name: str,
    identity: Mapping[str, Any],
    replay: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """One writer per unit. Invalid records fail closed and are never overwritten."""
    if directory.is_symlink():
        raise RuntimeError(f"promotion cache directory must not be a symlink: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(name.encode()).hexdigest()}.json"
    if path.is_symlink():
        raise RuntimeError(f"promotion cache unit must not be a symlink: {path}")
    if path.exists():
        try:
            record = json.loads(path.read_bytes(), object_pairs_hook=_object)
            payload = record["payload"]
            if (
                set(record) != {"payload", "sha256"}
                or set(payload) != {"schema_version", "name", "identity", "raw"}
                or payload["schema_version"] != 1
                or payload["name"] != name
                or payload["identity"] != identity
                or not isinstance(payload["raw"], dict)
                or hashlib.sha256(_encode(payload)).hexdigest() != record["sha256"]
            ):
                raise ValueError("identity, schema, or digest mismatch")
            return dict(payload["raw"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(f"invalid promotion cache unit: {path}") from exc
    raw = replay()
    payload = {"schema_version": 1, "name": name, "identity": dict(identity), "raw": raw}
    content = _encode({"payload": payload, "sha256": hashlib.sha256(_encode(payload)).hexdigest()})
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".promotion-", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # Linking publishes complete bytes without replacing another writer's work.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return raw
