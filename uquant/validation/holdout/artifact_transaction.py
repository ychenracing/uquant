"""Fail-closed holdout path safety and atomic artifact transactions."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from ...infrastructure.file_lock import FileLockMode, acquire_file_lock, release_file_lock
from .capabilities import holdout_runtime_capabilities

_AUTHORITATIVE_REPOSITORY_RELATIVES = (
    ".git",
    ".github",
    "AGENTS.md",
    "artifacts/phase2",
    "benchmarks",
    "pyproject.toml",
    "requirements.txt",
    "research",
    "scripts",
    "tests",
    "uquant",
    "uv.lock",
)


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    payload: bytes | None
    mode: int | None


def _reject_output_in_protected_data(
    output: str | Path,
    *,
    protected_directories: Sequence[str | Path],
) -> None:
    target = Path(output).resolve(strict=False)
    for protected in protected_directories:
        directory = Path(protected).resolve(strict=False)
        if target == directory or target.is_relative_to(directory):
            raise ValueError(f"holdout output is inside a protected data directory: {directory}")


def _holdout_artifact_paths_overlap(left: str | Path, right: str | Path) -> bool:
    first = Path(left).resolve(strict=False)
    second = Path(right).resolve(strict=False)
    if first == second or first.is_relative_to(second) or second.is_relative_to(first):
        return True
    if first.exists() and second.exists():
        try:
            return os.path.samefile(first, second)
        except OSError:
            return False
    return False


_paths_overlap = _holdout_artifact_paths_overlap


def _resolved_path_text(path: str | Path) -> str:
    return str(Path(path).resolve(strict=False))


def _read_protected_artifact(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    current = source.absolute()
    while True:
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink")
        if current == current.parent:
            break
        current = current.parent
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} is missing or unsafe")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _git_metadata_paths(repository_root: Path) -> tuple[Path, ...]:
    marker = repository_root / ".git"
    paths = [marker]
    if marker.is_file() and not marker.is_symlink():
        try:
            prefix, raw_git_dir = marker.read_text(encoding="utf-8").strip().split(":", 1)
        except (OSError, UnicodeError, ValueError):
            return tuple(paths)
        if prefix != "gitdir" or not raw_git_dir.strip():
            return tuple(paths)
        git_dir = Path(raw_git_dir.strip())
        if not git_dir.is_absolute():
            git_dir = repository_root / git_dir
        git_dir = git_dir.resolve(strict=False)
    elif marker.is_dir() and not marker.is_symlink():
        git_dir = marker.resolve(strict=False)
    else:
        return tuple(paths)
    paths.append(git_dir)
    common_marker = git_dir / "commondir"
    if common_marker.is_file() and not common_marker.is_symlink():
        try:
            raw_common = common_marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raw_common = ""
        if raw_common:
            common_dir = Path(raw_common)
            if not common_dir.is_absolute():
                common_dir = git_dir / common_dir
            paths.append(common_dir.resolve(strict=False))
    return tuple(dict.fromkeys(paths))


def _tracked_repository_paths(repository_root: Path) -> tuple[Path, ...]:
    if not (repository_root / ".git").exists():
        return ()
    git = shutil.which("git")
    if git is None:
        raise ValueError("cannot resolve tracked repository paths")
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(repository_root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
        names = completed.stdout.decode("utf-8").split("\0")
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot resolve tracked repository paths") from exc
    paths: list[Path] = []
    for name in names:
        if not name:
            continue
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("tracked repository path escapes its root")
        paths.append(repository_root / relative)
    return tuple(paths)


def _reject_authoritative_output_paths(
    *,
    repository_root: Path,
    output_path: str | Path,
    decision_output_path: str | Path | None,
    account_path: str | Path,
    journal_path: str | Path | None,
    holdout_data_directory: str,
    checkpoint_path: Path,
    lock_paths: Sequence[Path],
) -> None:
    outputs = [Path(output_path)]
    if decision_output_path is not None:
        outputs.append(Path(decision_output_path))
    if len(outputs) == 2 and _paths_overlap(outputs[0], outputs[1]):
        raise ValueError("holdout outputs overlap")
    git_metadata = _git_metadata_paths(repository_root)
    tracked_paths = _tracked_repository_paths(repository_root)
    protected: list[Path] = [
        Path(account_path),
        checkpoint_path,
        *lock_paths,
        repository_root / "data/frozen",
        repository_root / holdout_data_directory,
        *(repository_root / relative for relative in _AUTHORITATIVE_REPOSITORY_RELATIVES),
        *git_metadata,
        *tracked_paths,
    ]
    if journal_path is not None:
        protected.append(Path(journal_path))
    for output in outputs:
        for authoritative in protected:
            if _paths_overlap(output, authoritative):
                raise ValueError(f"holdout output overlaps an authoritative path: {authoritative}")
    carrier_protected = [
        Path(account_path),
        *outputs,
        *lock_paths,
        repository_root / "data/frozen",
        repository_root / holdout_data_directory,
        *(repository_root / relative for relative in _AUTHORITATIVE_REPOSITORY_RELATIVES),
        *git_metadata,
        *tracked_paths,
    ]
    if journal_path is not None:
        carrier_protected.append(Path(journal_path))
    for authoritative in carrier_protected:
        if _paths_overlap(checkpoint_path, authoritative):
            raise ValueError(f"holdout checkpoint overlaps an authoritative path: {authoritative}")


def _canonical_carrier_path(path: str | Path) -> Path:
    """Resolve lexical aliases only after rejecting every visible symlink component."""

    current = Path(path).absolute()
    while True:
        if current.is_symlink():
            raise ValueError("future holdout evidence artifact contains a symlink")
        if current == current.parent:
            break
        current = current.parent
    return Path(path).resolve(strict=False)


def _artifact_snapshots(paths: Sequence[Path]) -> dict[Path, _ArtifactSnapshot]:
    """Capture exact carrier bytes and modes before an evidence update."""

    capabilities = holdout_runtime_capabilities()
    read_artifact = (
        _read_protected_artifact
        if capabilities is None
        else capabilities.read_protected_artifact
    )
    os_adapter = os if capabilities is None else capabilities.os_adapter
    snapshots: dict[Path, _ArtifactSnapshot] = {}
    for path in paths:
        if not path.is_absolute():
            raise ValueError("future holdout evidence artifact path is not canonical")
        current = path.absolute()
        while True:
            if current.is_symlink():
                raise ValueError("future holdout evidence artifact contains a symlink")
            if current == current.parent:
                break
            current = current.parent
        if not path.exists():
            snapshots[path] = _ArtifactSnapshot(payload=None, mode=None)
            continue
        payload = read_artifact(
            path,
            label="future holdout evidence artifact",
        )
        mode: int | None = None
        if os_adapter.name != "nt":
            try:
                status = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError("cannot inspect future holdout evidence artifact mode") from exc
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("future holdout evidence artifact is unsafe")
            mode = stat.S_IMODE(status.st_mode)
        snapshots[path] = _ArtifactSnapshot(payload=payload, mode=mode)
    return snapshots


def _link_bytes_if_absent(
    path: Path,
    payload: bytes,
    *,
    mode: int | None = None,
) -> bool:
    """Publish exact bytes without replacing a concurrently installed generation."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.rollback-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    preserve_temporary = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except BaseException as exc:
            preserve_temporary = True
            exc.add_note(f"rollback bytes preserved for recovery at {temporary}")
            raise
        return True
    finally:
        if not preserve_temporary:
            temporary.unlink(missing_ok=True)


def _restore_owned_artifact(
    path: Path,
    payload: bytes | None,
    expected: bytes,
    *,
    mode: int | None = None,
) -> None:
    """Atomically claim one generation, then restore without overwriting a successor."""

    capabilities = holdout_runtime_capabilities()
    read_artifact = (
        _read_protected_artifact
        if capabilities is None
        else capabilities.read_protected_artifact
    )
    descriptor, quarantine_name = tempfile.mkstemp(
        prefix=f".{path.name}.claimed-",
        dir=path.parent,
    )
    os.close(descriptor)
    quarantine = Path(quarantine_name)
    quarantine.unlink()
    claimed = False
    preserve_quarantine = False
    try:
        try:
            os.replace(path, quarantine)
            claimed = True
        except FileNotFoundError:
            return
        try:
            current = read_artifact(
                quarantine,
                label="future holdout rollback artifact",
            )
        except ValueError:
            with suppress(FileExistsError):
                os.link(quarantine, path, follow_symlinks=False)
            return
        if current != expected:
            with suppress(FileExistsError):
                os.link(quarantine, path, follow_symlinks=False)
            return
        if payload is not None:
            _link_bytes_if_absent(path, payload, mode=mode)
    except BaseException as exc:
        if claimed:
            preserve_quarantine = True
            exc.add_note(f"claimed carrier preserved for recovery at {quarantine}")
        raise
    finally:
        if not preserve_quarantine:
            quarantine.unlink(missing_ok=True)


def _restore_artifact_snapshots(
    snapshots: Mapping[Path, _ArtifactSnapshot],
    owned: Mapping[Path, bytes],
) -> tuple[BaseException, ...]:
    """Restore only carriers that still contain this transaction's bytes."""

    failures: list[BaseException] = []
    for path, snapshot in snapshots.items():
        expected = owned.get(path)
        if expected is None:
            continue
        try:
            _restore_owned_artifact(
                path,
                snapshot.payload,
                expected,
                mode=snapshot.mode,
            )
        except BaseException as exc:
            failures.append(exc)
    return tuple(failures)


def _artifact_bundle_lock_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    """Return globally stable lock identities for every canonical carrier."""

    locks = {
        Path(tempfile.gettempdir())
        / f"uquant-future-holdout-carrier-{hashlib.sha256(str(path).encode('utf-8')).hexdigest()}.lock"
        for path in paths
    }
    return tuple(sorted(locks, key=str))


@contextmanager
def _artifact_bundle_lock(
    repository_root: Path,
    carrier_paths: Sequence[Path] = (),
) -> Iterator[None]:
    """Serialize complete replay/decision/checkpoint evidence transactions."""

    lock_paths = tuple(
        sorted(
            {
                _artifact_bundle_lock_path(repository_root),
                *_artifact_bundle_lock_paths(carrier_paths),
            },
            key=str,
        )
    )
    descriptors: list[int] = []
    primary: BaseException | None = None
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            current = lock_path.absolute()
            while True:
                if current.is_symlink():
                    raise ValueError("future holdout evidence lock contains a symlink")
                if current == current.parent:
                    break
                current = current.parent
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise ValueError("future holdout evidence lock is unsafe") from exc
            descriptors.append(descriptor)
            acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("future holdout evidence lock is unsafe")
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_failures: list[OSError] = []
        for descriptor in reversed(descriptors):
            try:
                release_file_lock(descriptor)
            except OSError as exc:
                cleanup_failures.append(exc)
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_failures.append(exc)
        if cleanup_failures:
            notes = tuple(
                f"future holdout lock cleanup also failed: {type(exc).__name__}: {exc}"
                for exc in cleanup_failures
            )
            if primary is not None:
                for note in notes:
                    primary.add_note(note)
            else:
                failure = RuntimeError("future holdout evidence lock cleanup failed")
                for note in notes:
                    failure.add_note(note)
                raise failure from cleanup_failures[0]


def _artifact_bundle_lock_path(repository_root: Path) -> Path:
    """Place the stable lock outside every repository evidence inventory."""

    identity = hashlib.sha256(str(repository_root.resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / f"uquant-future-holdout-{identity}.lock"


AUTHORITATIVE_REPOSITORY_RELATIVES = _AUTHORITATIVE_REPOSITORY_RELATIVES
ArtifactSnapshot = _ArtifactSnapshot
artifact_bundle_lock = _artifact_bundle_lock
artifact_bundle_lock_path = _artifact_bundle_lock_path
artifact_bundle_lock_paths = _artifact_bundle_lock_paths
artifact_snapshots = _artifact_snapshots
canonical_carrier_path = _canonical_carrier_path
git_metadata_paths = _git_metadata_paths
link_bytes_if_absent = _link_bytes_if_absent
paths_overlap = _paths_overlap
read_protected_artifact = _read_protected_artifact
reject_authoritative_output_paths = _reject_authoritative_output_paths
reject_output_in_protected_data = _reject_output_in_protected_data
resolved_path_text = _resolved_path_text
restore_artifact_snapshots = _restore_artifact_snapshots
restore_owned_artifact = _restore_owned_artifact
tracked_repository_paths = _tracked_repository_paths

__all__ = (
    "_AUTHORITATIVE_REPOSITORY_RELATIVES",
    "_ArtifactSnapshot",
    "_reject_output_in_protected_data",
    "_paths_overlap",
    "_resolved_path_text",
    "_read_protected_artifact",
    "_git_metadata_paths",
    "_tracked_repository_paths",
    "_reject_authoritative_output_paths",
    "_canonical_carrier_path",
    "_artifact_snapshots",
    "_link_bytes_if_absent",
    "_restore_owned_artifact",
    "_restore_artifact_snapshots",
    "_artifact_bundle_lock_paths",
    "_artifact_bundle_lock",
    "_artifact_bundle_lock_path",
)
