"""Compatibility facade for the shared production universe contract."""

from __future__ import annotations

from uquant.contracts import universe as _universe

FROZEN_CHAMPION_COMMIT = _universe.FROZEN_CHAMPION_COMMIT
GITHUB_PHASE1_ARTIFACT_SHA256 = _universe.GITHUB_PHASE1_ARTIFACT_SHA256
REQUIRED_FROZEN_CHAMPION_SHA256 = _universe.REQUIRED_FROZEN_CHAMPION_SHA256
REQUIRED_AI_UNIVERSE_SHA256 = _universe.REQUIRED_AI_UNIVERSE_SHA256
CANONICAL_INDUSTRIES = _universe.CANONICAL_INDUSTRIES
FrozenChampion = _universe.FrozenChampion
UniverseMember = _universe.UniverseMember
AIUniverse = _universe.AIUniverse
canonical_sha256 = _universe.canonical_sha256
frozen_champion_bytes = _universe.frozen_champion_bytes
ai_universe_manifest_bytes = _universe.ai_universe_manifest_bytes
load_phase1_frozen_champion = _universe.load_phase1_frozen_champion
load_ai_universe = _universe.load_ai_universe
default_ai_universe = _universe.default_ai_universe

_SHA256 = _universe._SHA256
_SYMBOL = _universe._SYMBOL
_MEMBER_FIELDS = _universe._MEMBER_FIELDS
_DEFAULT_UNIVERSE = _universe._DEFAULT_UNIVERSE
_reject_duplicate_keys = _universe._reject_duplicate_keys
_reject_nonstandard_constant = _universe._reject_nonstandard_constant
_parse_date = _universe._parse_date
_canonical_payload = _universe._canonical_payload
_read_json_bytes = _universe._read_json_bytes
_read_json = _universe._read_json
_resource_bytes = _universe._resource_bytes
_sha256 = _universe._sha256
