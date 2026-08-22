"""Fingerprint explicit source surfaces from a worktree or immutable Git tree."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed Git executable and argument vectors
from pathlib import Path, PurePosixPath
from typing import Final

from uquant.contracts.source_surfaces import (
    SourceSurface,
    SourceSurfaceRegistry,
    parse_source_surface_registry,
)

DEFAULT_SOURCE_SURFACE_REGISTRY: Final = Path(
    "benchmarks/source_surface_registry.json"
)

_GIT_OBJECT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _registry_relative_path(path: str | Path) -> str:
    value = path.as_posix() if isinstance(path, Path) else path
    normalized = PurePosixPath(value)
    if (
        not value
        or normalized.is_absolute()
        or normalized.as_posix() != value
        or any(part in {"", ".", ".."} for part in normalized.parts)
        or any(character in value for character in "*?[\\")
    ):
        raise ValueError("source surface registry path must be explicit and relative")
    return value


def _read_worktree_member(root: Path, relative: str) -> bytes:
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"source surface member is missing or unsafe: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"source surface member is missing or unsafe: {relative}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"source surface member is missing or unsafe: {relative}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise ValueError(f"source surface member is missing or unsafe: {relative}") from exc
    finally:
        os.close(descriptor)


def load_source_surface_registry(
    repository_root: str | Path,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> SourceSurfaceRegistry:
    """Load the strict current-facing registry from a physical worktree file."""

    root = Path(repository_root).resolve()
    relative = _registry_relative_path(registry_path)
    return parse_source_surface_registry(_read_worktree_member(root, relative))


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


def _worktree_entries(root: Path, surface: SourceSurface) -> tuple[tuple[str, bytes], ...]:
    return tuple((relative, _read_worktree_member(root, relative)) for relative in surface.paths)


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


def _source_surface_git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required for source surface provenance")
    return executable


def _git_bytes(root: Path, arguments: tuple[str, ...], *, label: str) -> bytes:
    try:
        completed = subprocess.run(
            [_source_surface_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return completed.stdout


def _resolve_source_surface_commit(root: Path, revision: str) -> str:
    resolved = _git_bytes(
        root,
        ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"),
        label="cannot resolve source surface Git commit",
    ).decode("ascii").strip()
    if not _GIT_OBJECT.fullmatch(resolved):
        raise RuntimeError("cannot resolve source surface Git commit")
    return resolved


def _git_member(root: Path, commit: str, relative: str) -> bytes:
    return _git_bytes(
        root,
        ("show", f"{commit}:{relative}"),
        label=f"cannot read source surface member from Git: {relative}",
    )


def git_source_surface_fingerprint(
    repository_root: str | Path,
    revision: str,
    surface_id: str,
    *,
    registry_path: str | Path = DEFAULT_SOURCE_SURFACE_REGISTRY,
) -> str:
    """Hash membership and member bytes exactly as defined by one Git commit."""

    root = Path(repository_root).resolve()
    relative_registry = _registry_relative_path(registry_path)
    commit = _resolve_source_surface_commit(root, revision)
    registry = parse_source_surface_registry(
        _git_member(root, commit, relative_registry)
    )
    surface = registry.surface(surface_id)
    entries = tuple(
        (relative, _git_member(root, commit, relative)) for relative in surface.paths
    )
    return _fingerprint_entries(entries)
