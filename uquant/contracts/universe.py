"""Shared production contract for the frozen champion and point-in-time universe."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, Final

FROZEN_CHAMPION_COMMIT: Final = "cf8fecff76564fd4ed87faa0da336a06d433fd93"
GITHUB_PHASE1_ARTIFACT_SHA256: Final = "86d894f46a22740cb4bc59a279cb2150927f312947859ad2559e3a17b45f5deb"
REQUIRED_FROZEN_CHAMPION_SHA256: Final = "8475a5da6f67db8c9ebf1b0aa5949a3484d75897e75ef8b4c4ef73c1c4d22a8f"
REQUIRED_AI_UNIVERSE_SHA256: Final = "03f42c5066fb8e1c7b2f8e1b7dd38d508d8053f548ebb5596317ce587d7cffd0"
CANONICAL_INDUSTRIES: Final = frozenset(
    {
        "advanced_packaging",
        "compute",
        "datacenter",
        "design",
        "foundry",
        "materials",
        "optical",
        "passives",
        "pcb",
        "semicap",
        "semiconductor",
        "storage",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$")
_MEMBER_FIELDS = {
    "symbol",
    "ai_domain",
    "industry",
    "effective_from",
    "effective_to",
    "tradable",
    "evidence",
    "reviewed_at",
}


@dataclass(frozen=True, slots=True)
class FrozenChampion:
    """Exact immutable identities accepted for the Phase 1 production champion."""

    production_commit: str
    production_source_sha256: str
    effective_config_sha256: str
    data_snapshot_id: str
    data_manifest_sha256: str
    data_checksums_sha256: str
    data_files_verified: int
    python_full_version: str
    numpy_version: str
    pandas_version: str
    uv_version: str
    uv_lock_sha256: str
    github_artifact_sha256: str


@dataclass(frozen=True, slots=True)
class UniverseMember:
    """One approved point-in-time AI supply-chain membership interval."""

    symbol: str
    ai_domain: str
    industry: str
    effective_from: date
    effective_to: date | None
    tradable: bool
    evidence: str
    reviewed_at: date

    def active(self, as_of: date) -> bool:
        return self.effective_from <= as_of and (self.effective_to is None or as_of < self.effective_to)


@dataclass(frozen=True, slots=True)
class AIUniverse:
    """Canonical production membership, immutable manifest hash, and PIT accessors."""

    members: tuple[UniverseMember, ...]
    sha256: str

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({member.symbol for member in self.members if member.tradable}))

    @property
    def industries(self) -> frozenset[str]:
        return frozenset(member.industry for member in self.members)

    def symbols_as_of(self, as_of: str | date) -> tuple[str, ...]:
        point = _parse_date(as_of, label="as_of")
        return tuple(sorted(member.symbol for member in self.members if member.tradable and member.active(point)))

    def industry_of(self, symbol: str, as_of: str | date) -> str:
        point = _parse_date(as_of, label="as_of")
        for member in self.members:
            if member.symbol == symbol and member.active(point):
                return member.industry
        return "unknown"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"universe contract contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"universe contract contains non-standard number: {value}")


def _parse_date(value: str | date, *, label: str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"universe {label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"universe {label} must be an ISO date") from exc


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in sorted(payload) if key != "canonical_sha256"}


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    """Return the deterministic SHA-256 over a manifest excluding its seal."""
    encoded = json.dumps(
        _canonical_payload(payload),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is corrupt") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{label} is missing or not a regular file: {source}")
    try:
        return _read_json_bytes(source.read_bytes(), label=label)
    except OSError as exc:
        raise ValueError(f"{label} is missing or corrupt: {source}") from exc


def _resource_bytes(name: str) -> bytes:
    return resources.files("uquant.contracts").joinpath("resources", name).read_bytes()


def frozen_champion_bytes() -> bytes:
    """Return the immutable champion artifact packaged with production code."""
    return _resource_bytes("phase1_frozen_champion.json")


def ai_universe_manifest_bytes() -> bytes:
    """Return the immutable AI-universe artifact packaged with production code."""
    return _resource_bytes("ai_universe_manifest.json")


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be SHA-256")
    return value


def load_phase1_frozen_champion(path: str | Path | None = None) -> FrozenChampion:
    """Load the reviewed Phase 1 identity without accepting partial provenance."""
    payload = (
        _read_json_bytes(frozen_champion_bytes(), label="frozen champion")
        if path is None
        else _read_json(path, label="frozen champion")
    )
    if set(payload) != {
        "schema_version",
        "contract_id",
        "production",
        "effective_config_sha256",
        "data",
        "environment",
        "github_phase1_artifact_sha256",
    } or payload["schema_version"] != 1 or payload["contract_id"] != "phase1-frozen-champion-v1":
        raise ValueError("frozen champion schema is malformed")
    production = payload["production"]
    data = payload["data"]
    environment = payload["environment"]
    if not isinstance(production, dict) or set(production) != {"repository", "commit", "source_sha256"}:
        raise ValueError("frozen champion production provenance is malformed")
    if production["repository"] != "ychenracing/uquant" or production["commit"] != FROZEN_CHAMPION_COMMIT:
        raise ValueError("frozen champion production identity differs from Phase 1")
    if canonical_sha256(payload) != REQUIRED_FROZEN_CHAMPION_SHA256:
        raise ValueError("frozen champion differs from the reviewed Phase 1 contract")
    if not isinstance(data, dict) or set(data) != {
        "snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"
    }:
        raise ValueError("frozen champion data provenance is malformed")
    if not isinstance(environment, dict) or set(environment) != {
        "python_full_version", "numpy_version", "pandas_version", "uv_version", "uv_lock_sha256"
    }:
        raise ValueError("frozen champion environment provenance is malformed")
    if not isinstance(data["snapshot_id"], str) or not data["snapshot_id"]:
        raise ValueError("frozen champion data snapshot is malformed")
    if isinstance(data["files_verified"], bool) or not isinstance(data["files_verified"], int):
        raise ValueError("frozen champion data file count is malformed")
    for field in ("python_full_version", "numpy_version", "pandas_version", "uv_version"):
        if not isinstance(environment[field], str) or not environment[field]:
            raise ValueError(f"frozen champion environment is malformed: {field}")
    artifact = _sha256(payload["github_phase1_artifact_sha256"], label="frozen champion GitHub artifact")
    if artifact != GITHUB_PHASE1_ARTIFACT_SHA256:
        raise ValueError("frozen champion GitHub artifact differs from Phase 1")
    return FrozenChampion(
        production_commit=production["commit"],
        production_source_sha256=_sha256(production["source_sha256"], label="frozen champion source"),
        effective_config_sha256=_sha256(payload["effective_config_sha256"], label="frozen champion config"),
        data_snapshot_id=data["snapshot_id"],
        data_manifest_sha256=_sha256(data["manifest_sha256"], label="frozen champion data manifest"),
        data_checksums_sha256=_sha256(data["checksums_sha256"], label="frozen champion data checksums"),
        data_files_verified=data["files_verified"],
        python_full_version=environment["python_full_version"],
        numpy_version=environment["numpy_version"],
        pandas_version=environment["pandas_version"],
        uv_version=environment["uv_version"],
        uv_lock_sha256=_sha256(environment["uv_lock_sha256"], label="frozen champion uv lock"),
        github_artifact_sha256=artifact,
    )


def load_ai_universe(path: str | Path | None = None) -> AIUniverse:
    """Load the one reviewed AI universe, rejecting stale or resealed membership."""
    payload = (
        _read_json_bytes(ai_universe_manifest_bytes(), label="AI universe manifest")
        if path is None
        else _read_json(path, label="AI universe manifest")
    )
    if set(payload) != {"schema_version", "manifest_id", "canonical_sha256", "members"}:
        raise ValueError("AI universe manifest schema is malformed")
    if payload["schema_version"] != 1 or payload["manifest_id"] != "phase1-ai-universe-v1":
        raise ValueError("AI universe manifest identity is malformed")
    seal = _sha256(payload["canonical_sha256"], label="AI universe canonical SHA-256")
    observed = canonical_sha256(payload)
    if seal != observed or seal != REQUIRED_AI_UNIVERSE_SHA256:
        raise ValueError("AI universe manifest canonical SHA-256 differs from the reviewed production reference")
    raw_members = payload["members"]
    if not isinstance(raw_members, list) or not raw_members:
        raise ValueError("AI universe manifest members are malformed")
    members: list[UniverseMember] = []
    for raw in raw_members:
        if not isinstance(raw, dict) or set(raw) != _MEMBER_FIELDS:
            raise ValueError("AI universe manifest member is malformed")
        symbol = raw["symbol"]
        industry = raw["industry"]
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("AI universe manifest symbol is malformed")
        if not isinstance(industry, str) or industry not in CANONICAL_INDUSTRIES:
            raise ValueError("AI universe manifest industry is malformed")
        if not isinstance(raw["ai_domain"], str) or not raw["ai_domain"]:
            raise ValueError("AI universe manifest AI-domain assertion is malformed")
        if not isinstance(raw["tradable"], bool) or not raw["tradable"]:
            raise ValueError("AI universe manifest production members must be tradable")
        if not isinstance(raw["evidence"], str) or not raw["evidence"]:
            raise ValueError("AI universe manifest evidence is malformed")
        start = _parse_date(raw["effective_from"], label="effective_from")
        end = _parse_date(raw["effective_to"], label="effective_to") if raw["effective_to"] is not None else None
        if end is not None and end <= start:
            raise ValueError("AI universe manifest interval is malformed")
        members.append(
            UniverseMember(
                symbol=symbol,
                ai_domain=raw["ai_domain"],
                industry=industry,
                effective_from=start,
                effective_to=end,
                tradable=True,
                evidence=raw["evidence"],
                reviewed_at=_parse_date(raw["reviewed_at"], label="reviewed_at"),
            )
        )
    if len({member.symbol for member in members}) != 34 or len(members) != 34:
        raise ValueError("AI universe manifest must contain exactly 34 unique production symbols")
    if tuple(member.symbol for member in members) != tuple(sorted(member.symbol for member in members)):
        raise ValueError("AI universe manifest members must have canonical symbol ordering")
    return AIUniverse(members=tuple(members), sha256=seal)


_DEFAULT_UNIVERSE: Final = load_ai_universe()


def default_ai_universe() -> AIUniverse:
    """Return the immutable reviewed universe shared by production helpers."""
    return _DEFAULT_UNIVERSE
