"""Immutable contracts and provenance for Risk Differential Closure.

This module is intentionally research-only.  Production packages do not import it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

SystemId = Literal["uquant_base", "uquant_sentinel", "trade"]
TraceStatus = Literal["READY", "DEGRADED", "NOT_READY", "UNOBSERVABLE"]

TRACE_STATUSES = frozenset({"READY", "DEGRADED", "NOT_READY", "UNOBSERVABLE"})
MAPPING_STATUSES = frozenset(
    {
        "ABSORBED_BASE",
        "ABSORBED_SENTINEL",
        "ABSORBED_ARCHITECTURALLY",
        "PARTIAL_EQUIVALENT",
        "INCREMENTAL_OBSERVATIONAL",
        "INCREMENTAL_EXECUTION_POLICY",
        "REJECTED_PREVIOUSLY",
        "UNOBSERVABLE",
    }
)
ACTION_CLASSIFICATIONS = frozenset(
    {"DIRECTLY_REPLAYABLE", "TRANSLATABLE", "HYBRID_DIAGNOSTIC", "NON_TRANSFERABLE"}
)
CAPABILITY_CATEGORIES = frozenset(
    {
        "OBSERVATION",
        "RISK_STATE",
        "ADMISSION_GATE",
        "EXPOSURE_POLICY",
        "SYMBOL_EXIT_POLICY",
        "COOLDOWN_POLICY",
        "EXECUTION_OWNERSHIP",
        "OFFLINE_CALIBRATION",
    }
)


def canonical_sha256(payload: dict[str, object]) -> str:
    """Hash canonical JSON while excluding the envelope's own seal."""

    normalized = {key: value for key, value in payload.items() if key != "payload_sha256"}
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def hash_python_sources(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Python source is missing or unsafe: {path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def hash_selected_sources(root: Path, relative_paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(relative_paths):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"source file is missing or unsafe: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def hash_lock_files(root: Path, relative_paths: tuple[str, ...]) -> str:
    return hash_selected_sources(root, relative_paths)


@dataclass(frozen=True, slots=True)
class RiskTraceRow:
    date: str
    system: SystemId
    status: TraceStatus
    confidence: float | None
    severity_rank: int | None
    level: str | None
    market_velocity: bool | None
    breadth_structure: bool | None
    covariance_stress: bool | None
    leadership_damage: bool | None
    live_book_damage: bool | None
    capital_damage: bool | None
    concentration_damage: bool | None
    block_new_entries: bool | None
    block_pyramiding: bool | None
    recommended_gross_cap: float | None
    weakest_clusters: tuple[str, ...]
    action_candidates: tuple[str, ...]
    execution_owner: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in TRACE_STATUSES:
            raise ValueError("trace status is unknown")
        if self.status in {"NOT_READY", "UNOBSERVABLE"} and self.severity_rank is not None:
            raise ValueError("severity is unavailable for a non-ready trace")
        if self.severity_rank is not None and self.severity_rank not in range(4):
            raise ValueError("severity rank must be 0..3")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        if self.recommended_gross_cap is not None and not 0 <= self.recommended_gross_cap <= 1:
            raise ValueError("gross cap must be in [0, 1]")

    @classmethod
    def empty(
        cls,
        date: str,
        system: str,
        *,
        status: str = "UNOBSERVABLE",
        severity_rank: int | None = None,
    ) -> RiskTraceRow:
        return cls(
            date=date,
            system=cast(SystemId, system),
            status=cast(TraceStatus, status),
            confidence=None,
            severity_rank=severity_rank,
            level=None
            if severity_rank is None
            else ("NORMAL", "CAUTION", "DEFENSIVE", "CRITICAL")[severity_rank],
            market_velocity=None,
            breadth_structure=None,
            covariance_stress=None,
            leadership_damage=None,
            live_book_damage=None,
            capital_damage=None,
            concentration_damage=None,
            block_new_entries=None,
            block_pyramiding=None,
            recommended_gross_cap=None,
            weakest_clusters=(),
            action_candidates=(),
            execution_owner=None,
            reasons=(),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RiskDifferentialEvent:
    date: str
    axis: str
    classification: str
    trade_value: bool | str | float | None
    base_value: bool | str | float | None
    sentinel_value: bool | str | float | None
    actionable_buy_intents: int
    actionable_pyramid_intents: int
    base_already_protected: bool
    existing_gross_exposure: float = 0.0


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability_id: str
    trade_source: tuple[str, ...]
    category: str
    uquant_base_equivalent: tuple[str, ...]
    sentinel_equivalent: tuple[str, ...]
    mapping_status: str
    action_classification: str
    exact_transfer_possible: bool
    economic_counterfactual_supported: bool
    production_promotion_allowed_this_phase: bool
    rationale: str

    @classmethod
    def from_value(cls, value: CapabilityRecord | dict[str, Any]) -> CapabilityRecord:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("capability record must be a mapping")
        normalized = dict(value)
        for key in ("trade_source", "uquant_base_equivalent", "sentinel_equivalent"):
            normalized[key] = tuple(normalized.get(key, ()))
        return cls(**normalized)


def validate_capabilities(
    values: tuple[CapabilityRecord | dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[CapabilityRecord, ...]:
    records = tuple(CapabilityRecord.from_value(value) for value in values)
    identifiers = [item.capability_id for item in records]
    if not records or len(identifiers) != len(set(identifiers)):
        raise ValueError("capability identifiers must be nonempty and unique")
    for item in records:
        if item.mapping_status not in MAPPING_STATUSES:
            raise ValueError(f"unknown capability mapping: {item.capability_id}")
        if item.action_classification not in ACTION_CLASSIFICATIONS:
            raise ValueError(f"unknown action classification: {item.capability_id}")
        if item.category not in CAPABILITY_CATEGORIES:
            raise ValueError(f"unknown capability category: {item.capability_id}")
        if item.production_promotion_allowed_this_phase:
            raise ValueError("production promotion is forbidden in this phase")
        if (
            item.action_classification in {"HYBRID_DIAGNOSTIC", "NON_TRANSFERABLE"}
            and item.exact_transfer_possible
        ):
            raise ValueError("hybrid/non-transferable capability cannot be exact")
    return records


def validate_registry_checkout(root: Path, identity: dict[str, Any]) -> None:
    marker = root / ".frozen_commit"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != identity["commit"]:
        raise ValueError("challenger commit mismatch")
    required = tuple(str(item) for item in identity["risk_source_files"])
    if hash_selected_sources(root, required) != identity["risk_source_sha256"]:
        raise ValueError("challenger Python risk-source hash mismatch")
