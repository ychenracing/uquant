"""Crash-safe text output with explicit alias protection."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_symlink_path(path: Path) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise ValueError(f"atomic output path contains a symlink: {current}")
        if current == current.parent:
            return
        current = current.parent


def _aliases(left: Path, right: Path) -> bool:
    if left.absolute() == right.absolute():
        return True
    if left.exists() and right.exists():
        try:
            return os.path.samefile(left, right)
        except OSError:
            return False
    return False


def atomic_write_text(
    destination: str | Path,
    text: str,
    *,
    protected_paths: Iterable[str | Path] = (),
) -> None:
    """Atomically write UTF-8 text without overwriting protected aliases."""

    target = Path(destination)
    _reject_symlink_path(target)
    protected = tuple(Path(path) for path in protected_paths)
    for path in protected:
        if _aliases(target, path):
            raise ValueError(f"atomic output aliases a protected path: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
