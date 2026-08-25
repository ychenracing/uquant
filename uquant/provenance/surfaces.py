"""Load reviewed source-surface membership from a worktree or Git commit."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from uquant.contracts.source_surfaces import (
    SOURCE_SURFACE_IDS,
    SourceSurface,
    SourceSurfaceRegistry,
    parse_source_surface_registry,
)
from uquant.infrastructure.git_source import (
    explicit_relative_path,
    read_git_file_bytes,
    read_worktree_file_bytes,
    resolve_git_commit,
)

DEFAULT_SOURCE_SURFACE_REGISTRY: Final = Path(
    "benchmarks/source_surface_registry.json"
)


def _registry_relative_path(path: str | Path) -> str:
    return explicit_relative_path(path, label="source surface registry path")


def load_source_surface_registry(
    repository_root: str | Path,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> SourceSurfaceRegistry:
    """Load the strict current-facing registry from a physical worktree file."""

    relative = _registry_relative_path(registry_path)
    document = read_worktree_file_bytes(
        repository_root,
        relative,
        label="source surface registry",
    )
    return parse_source_surface_registry(document)


def load_git_source_surface_registry(
    repository_root: str | Path,
    revision: str,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> tuple[str, SourceSurfaceRegistry]:
    """Load registry membership and its resolved immutable Git commit."""

    relative = _registry_relative_path(registry_path)
    commit = resolve_git_commit(
        repository_root,
        revision,
        label="cannot resolve source surface Git commit",
    )
    document = read_git_file_bytes(
        repository_root,
        commit,
        relative,
        label=f"cannot read source surface member from Git: {relative}",
    )
    return commit, parse_source_surface_registry(document)


__all__ = (
    "DEFAULT_SOURCE_SURFACE_REGISTRY",
    "SOURCE_SURFACE_IDS",
    "SourceSurface",
    "SourceSurfaceRegistry",
    "load_git_source_surface_registry",
    "load_source_surface_registry",
    "parse_source_surface_registry",
)
