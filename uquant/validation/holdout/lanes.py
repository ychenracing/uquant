"""Append-only identities and milestone policy for future-holdout lanes."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from .contract import SCORE_FIELDS, FutureHoldoutContract, load_future_holdout_contract

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT: Final = re.compile(r"^[0-9a-f]{40}$")
_LANE_ID: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_REGISTRY_FIELDS: Final = {
    "schema_version",
    "registry_id",
    "canonical_sha256",
    "contract_sha256",
    "lanes",
}
_LANE_FIELDS: Final = {
    "lane_id",
    "activation_session",
    "source_commit",
    "production_source_sha256",
    "sentinel_source_sha256",
    "effective_config_sha256",
    "data_contract_sha256",
    "data_directory",
    "runtime",
    "parent_lane",
    "economic_behavior",
    "status",
}
_RUNTIME_FIELDS: Final = {
    "python_full_version",
    "numpy_version",
    "pandas_version",
    "uv_version",
    "uv_lock_sha256",
}
_BEHAVIORS: Final = {"IDENTICAL", "FREEZE_ONLY", "GROSS_CAP"}
_STATUSES: Final = (
    "OBSERVING",
    "MILESTONE_20",
    "MILESTONE_40",
    "MILESTONE_60",
    "CLOSED",
)
_LEGACY_LANE_ID: Final = "champion_pre_sentinel"


@dataclass(frozen=True, slots=True)
class HoldoutLane:
    """One immutable candidate identity and its observation status."""

    lane_id: str
    activation_session: str
    source_commit: str
    production_source_sha256: str
    sentinel_source_sha256: str
    effective_config_sha256: str
    data_contract_sha256: str
    data_directory: str
    python_full_version: str
    numpy_version: str
    pandas_version: str
    uv_version: str
    uv_lock_sha256: str
    parent_lane: str | None
    economic_behavior: str
    status: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"lane registry JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"lane registry contains non-standard number: {value}")


def _canonical_bytes(value: object, *, omit_seal: bool = False) -> bytes:
    payload = value
    if omit_seal:
        if not isinstance(value, dict):
            raise TypeError("sealed lane registry payload must be an object")
        payload = {key: item for key, item in value.items() if key != "canonical_sha256"}
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: object, *, omit_seal: bool = False) -> str:
    return hashlib.sha256(_canonical_bytes(value, omit_seal=omit_seal)).hexdigest()


def _decode_lane(raw: object) -> HoldoutLane:
    if not isinstance(raw, dict) or set(raw) != _LANE_FIELDS:
        raise ValueError("future holdout lane schema is malformed")
    runtime = raw.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != _RUNTIME_FIELDS:
        raise ValueError("future holdout lane runtime schema is malformed")
    return HoldoutLane(
        lane_id=raw["lane_id"],
        activation_session=raw["activation_session"],
        source_commit=raw["source_commit"],
        production_source_sha256=raw["production_source_sha256"],
        sentinel_source_sha256=raw["sentinel_source_sha256"],
        effective_config_sha256=raw["effective_config_sha256"],
        data_contract_sha256=raw["data_contract_sha256"],
        data_directory=raw["data_directory"],
        python_full_version=runtime["python_full_version"],
        numpy_version=runtime["numpy_version"],
        pandas_version=runtime["pandas_version"],
        uv_version=runtime["uv_version"],
        uv_lock_sha256=runtime["uv_lock_sha256"],
        parent_lane=raw["parent_lane"],
        economic_behavior=raw["economic_behavior"],
        status=raw["status"],
    )


def load_lane_registry(path: str | Path) -> tuple[HoldoutLane, ...]:
    """Read one sealed registry and reject permissive JSON or schema drift."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("future holdout lane registry must be a regular file")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("future holdout lane registry is corrupt") from exc
    if not isinstance(raw, dict) or set(raw) != _REGISTRY_FIELDS:
        raise ValueError("future holdout lane registry schema is malformed")
    seal = raw.get("canonical_sha256")
    if not isinstance(seal, str) or seal != _canonical_sha256(raw, omit_seal=True):
        raise ValueError("future holdout lane registry hash is invalid")
    contract = load_future_holdout_contract()
    if (
        raw.get("schema_version") != 1
        or raw.get("registry_id") != "phase2-future-holdout-lanes-v1"
        or raw.get("contract_sha256") != contract.sha256
        or not isinstance(raw.get("lanes"), list)
    ):
        raise ValueError("future holdout lane registry identity is malformed")
    lanes = tuple(_decode_lane(item) for item in raw["lanes"])
    validate_lane_registry(lanes, contract)
    return lanes


def _validate_hash(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"future holdout lane {field} must be SHA-256")


def validate_lane_registry(
    lanes: tuple[HoldoutLane, ...],
    contract: FutureHoldoutContract,
) -> None:
    """Validate lane identities without interpreting mutable observations."""

    if not lanes:
        raise ValueError("future holdout lane registry must not be empty")
    identifiers = [lane.lane_id for lane in lanes]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("future holdout lane IDs must be unique")
    prior_ids: set[str] = set()
    for lane in lanes:
        if not isinstance(lane.lane_id, str) or not _LANE_ID.fullmatch(lane.lane_id):
            raise ValueError("future holdout lane ID is malformed")
        if lane.activation_session not in contract.review_sessions:
            raise ValueError("future holdout lane activation must be a contracted session")
        if not isinstance(lane.source_commit, str) or not _COMMIT.fullmatch(lane.source_commit):
            raise ValueError("future holdout lane source commit must be a full Git SHA")
        for field in (
            "production_source_sha256",
            "sentinel_source_sha256",
            "effective_config_sha256",
            "data_contract_sha256",
            "uv_lock_sha256",
        ):
            _validate_hash(getattr(lane, field), field=field)
        if lane.data_contract_sha256 != contract.sha256:
            raise ValueError("future holdout lane data contract is stale")
        if lane.data_directory != contract.data_directory:
            raise ValueError("future holdout lane data directory is stale")
        if any(
            not isinstance(getattr(lane, field), str) or not getattr(lane, field)
            for field in (
                "python_full_version",
                "numpy_version",
                "pandas_version",
                "uv_version",
            )
        ):
            raise ValueError("future holdout lane runtime must be complete")
        if lane.parent_lane is not None and lane.parent_lane not in prior_ids:
            raise ValueError("future holdout lane parent must precede the child")
        if lane.economic_behavior not in _BEHAVIORS:
            raise ValueError("future holdout lane behavior is invalid")
        if lane.status not in _STATUSES:
            raise ValueError("future holdout lane status is invalid")
        prior_ids.add(lane.lane_id)

    legacy = lanes[0]
    if (
        legacy.lane_id != _LEGACY_LANE_ID
        or legacy.activation_session != contract.first_holdout_date
        or legacy.source_commit != contract.strategy_anchor_commit
        or legacy.production_source_sha256 != contract.strategy_source_sha256
        or legacy.sentinel_source_sha256 != contract.strategy_source_sha256
        or legacy.effective_config_sha256 != contract.strategy_config_sha256
        or legacy.parent_lane is not None
        or legacy.economic_behavior != "IDENTICAL"
    ):
        raise ValueError("legacy future holdout lane differs from the sealed strategy anchor")


def _identity(lane: HoldoutLane) -> tuple[object, ...]:
    payload = asdict(lane)
    payload.pop("status")
    return tuple(payload.items())


def validate_lane_registry_transition(
    previous: tuple[HoldoutLane, ...],
    current: tuple[HoldoutLane, ...],
    contract: FutureHoldoutContract,
    *,
    observed_sessions: tuple[str, ...] = (),
) -> None:
    """Permit only status progress and appended, not-yet-observed candidates."""

    validate_lane_registry(previous, contract)
    if len(current) < len(previous):
        raise ValueError("future holdout lane was deleted")
    for old, new in zip(previous, current, strict=False):
        if _identity(old) != _identity(new):
            raise ValueError("observed future holdout lane identity changed")
        if _STATUSES.index(new.status) < _STATUSES.index(old.status):
            raise ValueError("future holdout lane status moved backward")
    validate_lane_registry(current, contract)
    if observed_sessions:
        observed_set = set(observed_sessions)
        if tuple(observed_sessions) != tuple(
            session for session in contract.review_sessions if session in observed_set
        ):
            raise ValueError("future holdout observed sessions are not monotonic")
        last_observed = observed_sessions[-1]
        if any(lane.activation_session <= last_observed for lane in current[len(previous) :]):
            raise ValueError("new future holdout lane would backfill observed sessions")


def build_lane_validation_report(
    *,
    lanes: tuple[HoldoutLane, ...],
    contract: FutureHoldoutContract,
    observed_sessions: tuple[str, ...],
    holdout_data_sha256: str,
) -> dict[str, Any]:
    """Build a sealed identity/sample report without accepting economic scores."""

    validate_lane_registry(lanes, contract)
    _validate_hash(holdout_data_sha256, field="holdout_data_sha256")
    if tuple(observed_sessions) != contract.review_sessions[: len(observed_sessions)]:
        raise ValueError("future holdout observations must be a complete calendar prefix")
    lane_reports: list[dict[str, Any]] = []
    for lane in lanes:
        sessions = tuple(session for session in observed_sessions if session >= lane.activation_session)
        next_milestone = next(
            (value for value in contract.review_milestones if value > len(sessions)),
            None,
        )
        reviewable = len(sessions) >= contract.review_milestones[0]
        lane_reports.append(
            {
                "lane_id": lane.lane_id,
                "activation_session": lane.activation_session,
                "source_commit": lane.source_commit,
                "production_source_sha256": lane.production_source_sha256,
                "sentinel_source_sha256": lane.sentinel_source_sha256,
                "effective_config_sha256": lane.effective_config_sha256,
                "data_contract_sha256": lane.data_contract_sha256,
                "holdout_data_sha256": holdout_data_sha256,
                "runtime": {
                    "python_full_version": lane.python_full_version,
                    "numpy_version": lane.numpy_version,
                    "pandas_version": lane.pandas_version,
                    "uv_version": lane.uv_version,
                    "uv_lock_sha256": lane.uv_lock_sha256,
                },
                "economic_behavior": lane.economic_behavior,
                "status": lane.status,
                "observed_sessions": len(sessions),
                "first_observed_session": sessions[0] if sessions else None,
                "last_observed_session": sessions[-1] if sessions else None,
                "next_milestone": next_milestone,
                "formal_reviewable": reviewable,
                "score_status": "REPLAY_REQUIRED" if reviewable else "NON_REVIEWABLE",
                "scores": {field: None for field in SCORE_FIELDS},
            }
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_id": "phase2-future-holdout-lane-validation-v1",
        "contract_sha256": contract.sha256,
        "holdout_data_sha256": holdout_data_sha256,
        "observed_sessions": len(observed_sessions),
        "review_milestones": list(contract.review_milestones),
        "lanes": lane_reports,
    }
    report["canonical_sha256"] = _canonical_sha256(report)
    return report


def lane_binding_payload(lane: HoldoutLane) -> dict[str, Any]:
    """Return the complete immutable identity embedded in replay evidence."""

    return {
        "lane_id": lane.lane_id,
        "activation_session": lane.activation_session,
        "source_commit": lane.source_commit,
        "production_source_sha256": lane.production_source_sha256,
        "sentinel_source_sha256": lane.sentinel_source_sha256,
        "effective_config_sha256": lane.effective_config_sha256,
        "data_contract_sha256": lane.data_contract_sha256,
        "data_directory": lane.data_directory,
        "runtime": {
            "python_full_version": lane.python_full_version,
            "numpy_version": lane.numpy_version,
            "pandas_version": lane.pandas_version,
            "uv_version": lane.uv_version,
            "uv_lock_sha256": lane.uv_lock_sha256,
        },
        "parent_lane": lane.parent_lane,
        "economic_behavior": lane.economic_behavior,
    }

__all__ = (
    "_SHA256",
    "_COMMIT",
    "_LANE_ID",
    "_REGISTRY_FIELDS",
    "_LANE_FIELDS",
    "_RUNTIME_FIELDS",
    "_BEHAVIORS",
    "_STATUSES",
    "_LEGACY_LANE_ID",
    "HoldoutLane",
    "_reject_duplicate_keys",
    "_reject_nonstandard_constant",
    "_canonical_bytes",
    "_canonical_sha256",
    "_decode_lane",
    "load_lane_registry",
    "_validate_hash",
    "validate_lane_registry",
    "_identity",
    "validate_lane_registry_transition",
    "build_lane_validation_report",
    "lane_binding_payload",
)
