"""Fingerprint exact reviewed source-surface names and bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from uquant.contracts.source_surfaces import SourceSurface
from uquant.infrastructure.git_source import (
    read_git_file_bytes,
    read_worktree_file_bytes,
)

from .surfaces import (
    DEFAULT_SOURCE_SURFACE_REGISTRY,
    load_git_source_surface_registry,
    load_source_surface_registry,
)


def _fingerprint_entries(entries: tuple[tuple[str, bytes], ...]) -> str:
    if not entries:
        raise ValueError("source surface has no members")
    digest = hashlib.sha256()
    for relative, content in sorted(entries):
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _worktree_entries(
    root: Path,
    surface: SourceSurface,
) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (
            relative,
            read_worktree_file_bytes(root, relative, label="source surface member"),
        )
        for relative in surface.paths
    )


def source_surface_fingerprint(
    repository_root: str | Path,
    surface_id: str,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> str:
    """Hash one reviewed surface from current physical worktree bytes."""

    root = Path(repository_root).resolve()
    registry = load_source_surface_registry(root, registry_path=registry_path)
    return _fingerprint_entries(_worktree_entries(root, registry.surface(surface_id)))


def git_source_surface_fingerprint(
    repository_root: str | Path,
    revision: str,
    surface_id: str,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> str:
    """Hash membership and member bytes exactly as defined by one Git commit."""

    root = Path(repository_root).resolve()
    commit, registry = load_git_source_surface_registry(
        root,
        revision,
        registry_path=registry_path,
    )
    surface = registry.surface(surface_id)
    entries = tuple(
        (
            relative,
            read_git_file_bytes(
                root,
                commit,
                relative,
                label=f"cannot read source surface member from Git: {relative}",
            ),
        )
        for relative in surface.paths
    )
    return _fingerprint_entries(entries)


__all__ = ("git_source_surface_fingerprint", "source_surface_fingerprint")
