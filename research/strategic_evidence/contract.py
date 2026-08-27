"""Fail-closed loader for the preregistered strategic evidence contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import canonical_sha256, require_git_sha, require_sha256


@dataclass(frozen=True, slots=True)
class StrategicEvidenceContract:
    """Typed projection of fields needed to construct the frozen matrices."""

    schema_version: str
    base_commit: str
    window: dict[str, str]
    future_holdout_boundary: str
    canonical_universe: tuple[str, ...]
    positive_controls: tuple[str, ...]
    initial_state_ids: tuple[str, ...]
    path_ids: tuple[str, ...]
    random_seed: int
    replay_error_policy: str
    insufficient_sample_policy: str
    absolute_thresholds: dict[str, dict[str, float | int]]
    payload_sha256: str
    raw: dict[str, Any]


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} must be a non-empty string list")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicates")
    return result


def load_contract(path: str | Path) -> StrategicEvidenceContract:
    """Load, seal-check, and structurally validate the immutable v1 contract."""

    contract_path = Path(path)
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("strategic evidence contract is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("strategic evidence contract must be an object")
    seal = require_sha256(payload.get("payload_sha256"), field="payload_sha256")
    if seal != canonical_sha256(payload):
        raise ValueError("strategic evidence contract seal differs")
    base_commit = require_git_sha(payload.get("base_commit"), field="base_commit")
    window = payload.get("window")
    if window != {"start": "2023-01-03", "end": "2026-08-05"}:
        raise ValueError("strategic evidence contract window differs from v1")
    identities = payload.get("identities")
    if not isinstance(identities, dict):
        raise ValueError("strategic evidence identities are missing")
    for field in (
        "production_source_sha256",
        "config_sha256",
        "data_manifest_sha256",
        "uv_lock_sha256",
        "universe_sha256",
        "industry_mapping_sha256",
        "window_sha256",
    ):
        require_sha256(identities.get(field), field=field)
    matrix = payload.get("matrix")
    policies = payload.get("failure_semantics")
    thresholds = payload.get("absolute_thresholds")
    if not isinstance(matrix, dict) or not isinstance(policies, dict) or not isinstance(thresholds, dict):
        raise ValueError("strategic evidence matrix, failure semantics, or thresholds are missing")
    seed = payload.get("random_seed")
    if not isinstance(seed, int):
        raise ValueError("strategic evidence random seed must be an integer")
    return StrategicEvidenceContract(
        schema_version=str(payload.get("schema_version", "")),
        base_commit=base_commit,
        window=dict(window),
        future_holdout_boundary=str(payload.get("future_holdout_boundary", "")),
        canonical_universe=_string_tuple(matrix.get("canonical_universe"), field="canonical_universe"),
        positive_controls=_string_tuple(matrix.get("positive_controls"), field="positive_controls"),
        initial_state_ids=_string_tuple(matrix.get("initial_state_ids"), field="initial_state_ids"),
        path_ids=_string_tuple(matrix.get("path_ids"), field="path_ids"),
        random_seed=seed,
        replay_error_policy=str(policies.get("replay_error", "")),
        insufficient_sample_policy=str(policies.get("insufficient_sample", "")),
        absolute_thresholds={
            str(group): {str(name): value for name, value in values.items()}
            for group, values in thresholds.items()
            if isinstance(values, dict)
        },
        payload_sha256=seal,
        raw=payload,
    )


__all__ = ("StrategicEvidenceContract", "load_contract")
