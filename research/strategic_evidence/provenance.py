"""Canonical seals and deterministic shard transport for research evidence."""

from __future__ import annotations

import gzip
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from uquant.atomic_io import atomic_write_bytes

from .contract import StrategicEvidenceContract
from .models import canonical_sha256, require_sha256

_SHARD_SCHEMA_VERSION = 1
_PROVENANCE_FIELDS = frozenset(
    {
        "base_commit",
        "experiment_commit",
        "production_source_sha256",
        "research_source_sha256",
        "config_sha256",
        "data_manifest_sha256",
        "universe_sha256",
        "industry_mapping_sha256",
        "window_sha256",
        "scenario_sha256",
        "python",
        "numpy",
        "pandas",
        "uv",
        "uv_lock_sha256",
        "generated_at",
    }
)


def canonical_json_bytes(value: object) -> bytes:
    """Encode JSON deterministically, rejecting non-finite evidence values."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def seal_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy and canonical-seal one evidence payload."""

    sealed = dict(payload)
    sealed["payload_sha256"] = canonical_sha256(sealed)
    return sealed


def verify_sealed_payload(value: object, *, label: str = "payload") -> dict[str, Any]:
    """Return a seal-checked payload or fail closed."""

    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    seal = require_sha256(value.get("payload_sha256"), field=f"{label} payload_sha256")
    if seal != canonical_sha256(value):
        raise ValueError(f"{label} is unsealed or altered")
    return dict(value)


def build_provenance(
    contract: StrategicEvidenceContract,
    *,
    experiment_commit: str,
    research_source_sha256: str,
    scenario: Mapping[str, Any],
    generated_at: str,
    observed_identities: Mapping[str, str] | None = None,
    runtime_metadata: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the required compact binding without altering the frozen contract."""

    scenario_sha256 = canonical_sha256(dict(scenario))
    identities = contract.raw["identities"] if observed_identities is None else observed_identities
    required_identities = {
        "production_source_sha256",
        "research_source_sha256",
        "config_sha256",
        "data_manifest_sha256",
        "universe_sha256",
        "industry_mapping_sha256",
        "window_sha256",
        "uv_lock_sha256",
    }
    if observed_identities is not None and set(identities) != required_identities:
        raise ValueError("observed strategic evidence identity fields differ")
    if observed_identities is not None and identities["research_source_sha256"] != research_source_sha256:
        raise ValueError("observed strategic evidence research source differs")
    runtime = (
        {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "uv": "pinned-by-uv-lock",
            "generated_at": generated_at,
        }
        if runtime_metadata is None
        else dict(runtime_metadata)
    )
    if set(runtime) != {"python", "numpy", "pandas", "uv", "generated_at"}:
        raise ValueError("strategic evidence runtime metadata fields differ")
    if runtime["generated_at"] != generated_at:
        raise ValueError("strategic evidence generated_at differs from runtime metadata")
    return {
        "base_commit": contract.base_commit,
        "experiment_commit": experiment_commit,
        "production_source_sha256": identities["production_source_sha256"],
        "research_source_sha256": research_source_sha256,
        "config_sha256": identities["config_sha256"],
        "data_manifest_sha256": identities["data_manifest_sha256"],
        "universe_sha256": identities["universe_sha256"],
        "industry_mapping_sha256": identities["industry_mapping_sha256"],
        "window_sha256": identities["window_sha256"],
        "scenario_sha256": scenario_sha256,
        "python": runtime["python"],
        "numpy": runtime["numpy"],
        "pandas": runtime["pandas"],
        "uv": runtime["uv"],
        "uv_lock_sha256": identities["uv_lock_sha256"],
        "generated_at": runtime["generated_at"],
    }


def validate_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless every preregistered shard identity is present and well formed."""

    if set(value) != _PROVENANCE_FIELDS:
        raise ValueError("shard provenance fields differ from the preregistered contract")
    result = dict(value)
    for field in ("base_commit", "experiment_commit"):
        raw = result[field]
        if not isinstance(raw, str) or len(raw) != 40 or any(char not in "0123456789abcdef" for char in raw):
            raise ValueError(f"shard provenance {field} is malformed")
    for field in _PROVENANCE_FIELDS - {
        "base_commit",
        "experiment_commit",
        "python",
        "numpy",
        "pandas",
        "uv",
        "generated_at",
    }:
        require_sha256(result[field], field=f"shard provenance {field}")
    for field in ("python", "numpy", "pandas", "uv", "generated_at"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise ValueError(f"shard provenance {field} is empty")
    return result


def write_gzip_shard(
    path: str | Path,
    *,
    rows: Iterable[Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Atomically write a deterministic, self-sealed JSONL-gzip shard.

    The compressed member has a fixed timestamp.  The JSONL body starts with a
    sealed envelope and then emits its rows; this keeps readback strict while
    preserving stream-friendly row transport.
    """

    materialized_rows = [dict(row) for row in rows]
    validated_provenance = validate_provenance(provenance)
    envelope = seal_payload(
        {
            "schema_version": _SHARD_SCHEMA_VERSION,
            "provenance": validated_provenance,
            "row_count": len(materialized_rows),
            "rows_sha256": canonical_sha256({"rows": materialized_rows}),
        }
    )
    lines = [canonical_json_bytes(envelope), *(canonical_json_bytes(row) for row in materialized_rows)]
    payload = b"\n".join(lines) + b"\n"
    atomic_write_bytes(path, gzip.compress(payload, compresslevel=9, mtime=0))
    return envelope


def read_gzip_shard(path: str | Path) -> dict[str, Any]:
    """Read one deterministic shard and verify its decompression and seal."""

    try:
        raw = gzip.decompress(Path(path).read_bytes())
        records = raw.splitlines()
        if not records:
            raise ValueError("shard JSONL is empty")
        decoded = json.loads(records[0])
        rows = [json.loads(record) for record in records[1:]]
    except (OSError, UnicodeError, gzip.BadGzipFile, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("gzip shard is unreadable") from exc
    payload = verify_sealed_payload(decoded, label="gzip shard")
    if payload.get("schema_version") != _SHARD_SCHEMA_VERSION:
        raise ValueError("gzip shard schema differs")
    if not isinstance(payload.get("provenance"), dict):
        raise ValueError("gzip shard shape differs")
    validate_provenance(payload["provenance"])
    if payload.get("row_count") != len(rows) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("gzip shard rows are malformed")
    if payload.get("rows_sha256") != canonical_sha256({"rows": rows}):
        raise ValueError("gzip shard rows are unsealed or altered")
    payload["rows"] = tuple(dict(row) for row in rows)
    return payload


write_shard = write_gzip_shard
read_shard = read_gzip_shard

__all__ = (
    "build_provenance",
    "canonical_json_bytes",
    "read_gzip_shard",
    "read_shard",
    "seal_payload",
    "validate_provenance",
    "verify_sealed_payload",
    "write_gzip_shard",
    "write_shard",
)
