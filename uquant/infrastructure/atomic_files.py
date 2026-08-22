"""Crash-safe atomic output with explicit alias protection."""

from __future__ import annotations

import os
import secrets
import stat
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
        except OSError as exc:
            raise ValueError(
                f"cannot verify protected path identity: {left} and {right}"
            ) from exc
    return False


def validate_atomic_output_path(
    destination: str | Path,
    *,
    protected_paths: Iterable[str | Path] = (),
) -> Path:
    """Validate one output identity before a caller performs side effects."""

    target = Path(destination)
    _reject_symlink_path(target)
    for path in (Path(item) for item in protected_paths):
        if _aliases(target, path):
            raise ValueError(f"atomic output aliases a protected path: {path}")
    return target


def validate_atomic_output_boundary(
    destination: str | Path,
    *,
    protected_paths: Iterable[str | Path] = (),
    protected_roots: Iterable[str | Path] = (),
) -> tuple[Path, ...]:
    """Preflight an output against exact inputs and consumed input trees."""

    target = Path(destination)
    try:
        canonical_target = target.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve atomic output path: {target}") from exc
    protected = [Path(path) for path in protected_paths]
    for item in protected_roots:
        root = Path(item)
        try:
            canonical_root = root.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"cannot resolve protected input tree: {root}") from exc
        if canonical_target == canonical_root or canonical_root in canonical_target.parents:
            raise ValueError(f"atomic output is inside a protected input tree: {root}")
        protected.append(root)
        if root.is_dir():
            try:
                protected.extend(sorted(root.rglob("*")))
            except OSError as exc:
                raise ValueError(f"cannot inventory protected input tree: {root}") from exc
    validate_atomic_output_path(target, protected_paths=protected)
    return tuple(protected)


def _existing_destination_mode(target: Path) -> int | None:
    """Return the exact existing POSIX mode, or let new files honor umask."""

    if os.name == "nt":
        return None
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot inspect atomic output mode: {target}") from exc


def _open_temporary(target: Path, *, existing_mode: int | None) -> tuple[int, Path]:
    """Create a same-directory file and tighten an existing mode before writes."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}"
        try:
            descriptor = os.open(temporary, flags, 0o666)
        except FileExistsError:
            continue
        try:
            if existing_mode is not None:
                os.fchmod(descriptor, existing_mode)
        except BaseException:
            os.close(descriptor)
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise
        return descriptor, temporary
    raise FileExistsError(f"cannot allocate atomic temporary output for {target}")


def atomic_write_text(
    destination: str | Path,
    text: str,
    *,
    protected_paths: Iterable[str | Path] = (),
) -> None:
    """Atomically write UTF-8 text without overwriting protected aliases."""

    target = validate_atomic_output_path(
        destination,
        protected_paths=protected_paths,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = _existing_destination_mode(target)
    descriptor, temporary = _open_temporary(target, existing_mode=existing_mode)
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


def atomic_write_bytes(
    destination: str | Path,
    payload: bytes,
    *,
    protected_paths: Iterable[str | Path] = (),
) -> None:
    """Atomically write exact bytes without overwriting protected aliases."""

    target = validate_atomic_output_path(
        destination,
        protected_paths=protected_paths,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = _existing_destination_mode(target)
    descriptor, temporary = _open_temporary(target, existing_mode=existing_mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
