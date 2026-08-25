"""Fail-closed worktree and immutable Git path-byte access."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess  # nosec B404 - fixed Git executable and argument vectors
from pathlib import Path, PurePosixPath
from typing import Final

_GIT_OBJECT: Final = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def explicit_relative_path(path: str | Path, *, label: str = "source path") -> str:
    """Return one normalized explicit repository-relative POSIX path."""

    value = path.as_posix() if isinstance(path, Path) else path
    normalized = PurePosixPath(value)
    if (
        not value
        or normalized.is_absolute()
        or normalized.as_posix() != value
        or any(part in {"", ".", ".."} for part in normalized.parts)
        or any(character in value for character in "*?[\\")
    ):
        raise ValueError(f"{label} must be explicit and relative")
    return value


def read_worktree_file_bytes(
    repository_root: str | Path,
    relative_path: str | Path,
    *,
    label: str = "source file",
) -> bytes:
    """Read one physical regular file without traversing a symlink."""

    root = Path(repository_root).resolve()
    relative = explicit_relative_path(relative_path, label=f"{label} path")
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"{label} is missing or unsafe: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe: {relative}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} is missing or unsafe: {relative}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe: {relative}") from exc
    finally:
        os.close(descriptor)


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required for source provenance")
    return executable


def _git_bytes(root: Path, arguments: tuple[str, ...], *, label: str) -> bytes:
    try:
        completed = subprocess.run(
            [_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return completed.stdout


def resolve_git_commit(
    repository_root: str | Path,
    revision: str,
    *,
    label: str = "cannot resolve Git commit",
) -> str:
    """Resolve one revision to an exact SHA-1 or SHA-256 commit object."""

    root = Path(repository_root).resolve()
    resolved = _git_bytes(
        root,
        ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"),
        label=label,
    ).decode("ascii").strip()
    if not _GIT_OBJECT.fullmatch(resolved):
        raise RuntimeError(label)
    return resolved


def read_git_file_bytes(
    repository_root: str | Path,
    commit: str,
    relative_path: str | Path,
    *,
    label: str | None = None,
) -> bytes:
    """Read one explicit path from one already-resolved Git commit."""

    root = Path(repository_root).resolve()
    if not _GIT_OBJECT.fullmatch(commit):
        raise ValueError("Git source commit must be a resolved object ID")
    relative = explicit_relative_path(relative_path, label="Git source path")
    failure = label or f"cannot read Git source file: {relative}"
    return _git_bytes(root, ("show", f"{commit}:{relative}"), label=failure)
