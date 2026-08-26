"""Compatibility facade for the shared production universe contract."""

from __future__ import annotations

from uquant.contracts import universe as _universe

FROZEN_CHAMPION_COMMIT = _universe.FROZEN_CHAMPION_COMMIT
GITHUB_PERFORMANCE_ARTIFACT_SHA256 = _universe.GITHUB_PERFORMANCE_ARTIFACT_SHA256
REQUIRED_FROZEN_CHAMPION_SHA256 = _universe.REQUIRED_FROZEN_CHAMPION_SHA256
REQUIRED_AI_UNIVERSE_SHA256 = _universe.REQUIRED_AI_UNIVERSE_SHA256
CANONICAL_INDUSTRIES = _universe.CANONICAL_INDUSTRIES
FrozenChampion = _universe.FrozenChampion
UniverseMember = _universe.UniverseMember
AIUniverse = _universe.AIUniverse
canonical_sha256 = _universe.canonical_sha256
frozen_champion_bytes = _universe.frozen_champion_bytes
ai_universe_manifest_bytes = _universe.ai_universe_manifest_bytes
load_performance_frozen_champion = _universe.load_performance_frozen_champion
load_ai_universe = _universe.load_ai_universe
default_ai_universe = _universe.default_ai_universe

_SHA256 = _universe.SHA256_PATTERN
_SYMBOL = _universe.SYMBOL_PATTERN
_MEMBER_FIELDS = _universe.MEMBER_FIELDS
_DEFAULT_UNIVERSE = _universe.DEFAULT_UNIVERSE
_reject_duplicate_keys = _universe.reject_duplicate_keys
_reject_nonstandard_constant = _universe.reject_nonstandard_constant
_parse_date = _universe.parse_date
_canonical_payload = _universe.canonical_payload
_read_json_bytes = _universe.read_json_bytes
_read_json = _universe.read_json
_resource_bytes = _universe.resource_bytes
_sha256 = _universe.sha256_bytes

__all__ = (  # noqa: RUF022 - frozen public-name order
    "AIUniverse",
    "CANONICAL_INDUSTRIES",
    "FROZEN_CHAMPION_COMMIT",
    "FrozenChampion",
    "GITHUB_PERFORMANCE_ARTIFACT_SHA256",
    "REQUIRED_AI_UNIVERSE_SHA256",
    "REQUIRED_FROZEN_CHAMPION_SHA256",
    "UniverseMember",
    "_DEFAULT_UNIVERSE",
    "_MEMBER_FIELDS",
    "_SHA256",
    "_SYMBOL",
    "ai_universe_manifest_bytes",
    "canonical_sha256",
    "default_ai_universe",
    "frozen_champion_bytes",
    "load_ai_universe",
    "load_performance_frozen_champion",
)
