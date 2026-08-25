"""Compatibility facade for split source-surface provenance contracts."""

from __future__ import annotations

from .fingerprints import (
    git_source_surface_fingerprint,
    source_surface_fingerprint,
)
from .surfaces import (
    DEFAULT_SOURCE_SURFACE_REGISTRY,
    SOURCE_SURFACE_IDS,
    SourceSurface,
    SourceSurfaceRegistry,
    load_git_source_surface_registry,
    load_source_surface_registry,
    parse_source_surface_registry,
)

__all__ = (
    "DEFAULT_SOURCE_SURFACE_REGISTRY",
    "SOURCE_SURFACE_IDS",
    "SourceSurface",
    "SourceSurfaceRegistry",
    "git_source_surface_fingerprint",
    "load_git_source_surface_registry",
    "load_source_surface_registry",
    "parse_source_surface_registry",
    "source_surface_fingerprint",
)
