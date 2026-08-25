"""Compatibility facade for the shared AI-era runtime identity contract."""

from __future__ import annotations

from uquant.contracts import runtime_identity as _runtime_identity

AI_ERA_START = _runtime_identity.AI_ERA_START
AI_ERA_WINDOWS = _runtime_identity.AI_ERA_WINDOWS
AI_ERA_ACUTE_WINDOWS = _runtime_identity.AI_ERA_ACUTE_WINDOWS
require_ai_era_interval = _runtime_identity.require_ai_era_interval
runtime_environment_provenance = _runtime_identity.runtime_environment_provenance
