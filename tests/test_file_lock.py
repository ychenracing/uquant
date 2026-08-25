from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import uquant.infrastructure.file_lock as file_lock_module
from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)

ROOT = Path(__file__).resolve().parents[1]


class _FakeWindowsFunction:
    def __init__(self, *, result: int = 1) -> None:
        self.argtypes: list[object] = []
        self.restype: object | None = None
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *arguments: object) -> int:
        self.calls.append(arguments)
        return self.result


class _FakeWindowsRuntime:
    def __init__(self, *, handle: int) -> None:
        self.handle = handle
        self.descriptors: list[int] = []

    def get_osfhandle(self, descriptor: int) -> int:
        self.descriptors.append(descriptor)
        return self.handle


class _MissingWindowsFunctions:
    pass


def _integer(argument: Any) -> int:
    value = argument.value
    assert isinstance(value, int)
    return value


def _overlapped(argument: Any) -> Any:
    return argument._obj


def test_exclusive_file_lock_blocks_a_second_process_until_release(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "contract.lock"
    ready = tmp_path / "ready"
    acquired = tmp_path / "acquired"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
    code = "\n".join(
        (
            "import os, pathlib, sys",
            "from uquant.infrastructure.file_lock import FileLockMode, acquire_file_lock, release_file_lock",
            "descriptor = os.open(sys.argv[1], os.O_RDWR)",
            "pathlib.Path(sys.argv[2]).write_text('ready', encoding='utf-8')",
            "acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)",
            "pathlib.Path(sys.argv[3]).write_text('acquired', encoding='utf-8')",
            "release_file_lock(descriptor)",
            "os.close(descriptor)",
        )
    )
    child: subprocess.Popen[str] | None = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path), str(ready), str(acquired)],
            cwd=ROOT,
            text=True,
        )
        deadline = time.monotonic() + 5.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.read_text(encoding="utf-8") == "ready"
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=0.25)
        assert not acquired.exists()
        release_file_lock(descriptor)
        assert child.wait(timeout=5.0) == 0
        assert acquired.read_text(encoding="utf-8") == "acquired"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5.0)
        with suppress(OSError):
            release_file_lock(descriptor)
        os.close(descriptor)


def test_shared_file_locks_can_overlap(tmp_path: Path) -> None:
    lock_path = tmp_path / "read.lock"
    first = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(lock_path, os.O_RDWR)
    try:
        acquire_file_lock(first, FileLockMode.SHARED)
        acquire_file_lock(second, FileLockMode.SHARED)
        release_file_lock(second)
        release_file_lock(first)
    finally:
        os.close(second)
        os.close(first)


def test_windows_import_smoke_does_not_require_fcntl() -> None:
    code = """
import builtins
import importlib.util
import pathlib

original_import = builtins.__import__
def import_without_fcntl(name, *args, **kwargs):
    if name == "fcntl":
        raise ImportError("fcntl is unavailable on Windows")
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_fcntl
import uquant.execution_journal
import uquant.validation.execution_journal
import uquant.validation.holdout_runtime
path = pathlib.Path("scripts/production_observation.py").resolve()
spec = importlib.util.spec_from_file_location("windows_import_smoke", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("windows-import-ok")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "windows-import-ok"


@pytest.mark.parametrize(
    ("mode", "expected_flags"),
    [
        (FileLockMode.SHARED, 0),
        (FileLockMode.EXCLUSIVE, 0x00000002),
    ],
)
def test_windows_lock_uses_blocking_mode_and_matching_full_file_range(
    monkeypatch: pytest.MonkeyPatch,
    mode: FileLockMode,
    expected_flags: int,
) -> None:
    lock = _FakeWindowsFunction()
    unlock = _FakeWindowsFunction()
    kernel = SimpleNamespace(LockFileEx=lock, UnlockFileEx=unlock)
    runtime = _FakeWindowsRuntime(handle=0x1234)
    monkeypatch.setattr(
        file_lock_module.importlib,
        "import_module",
        lambda name: runtime if name == "msvcrt" else pytest.fail(name),
    )
    monkeypatch.setattr(
        file_lock_module,
        "_windows_lock_function",
        lambda: kernel.LockFileEx,
    )
    monkeypatch.setattr(
        file_lock_module,
        "_windows_unlock_function",
        lambda: kernel.UnlockFileEx,
    )

    file_lock_module._windows_lock(37, mode)
    file_lock_module._windows_unlock(37)

    assert runtime.descriptors == [37, 37]
    assert len(lock.calls) == len(unlock.calls) == 1
    lock_call = lock.calls[0]
    unlock_call = unlock.calls[0]
    assert _integer(lock_call[0]) == _integer(unlock_call[0]) == 0x1234
    assert _integer(lock_call[1]) == expected_flags
    assert expected_flags & 0x00000001 == 0
    assert _integer(lock_call[2]) == _integer(unlock_call[1]) == 0
    assert (_integer(lock_call[3]), _integer(lock_call[4])) == (
        _integer(unlock_call[2]),
        _integer(unlock_call[3]),
    ) == (0xFFFFFFFF, 0xFFFFFFFF)
    lock_overlapped = _overlapped(lock_call[5])
    unlock_overlapped = _overlapped(unlock_call[4])
    assert (lock_overlapped.Offset, lock_overlapped.OffsetHigh) == (0, 0)
    assert (unlock_overlapped.Offset, unlock_overlapped.OffsetHigh) == (0, 0)


@pytest.mark.parametrize("operation", ["lock", "unlock"])
def test_windows_lock_propagates_the_exact_last_error(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    lock = _FakeWindowsFunction(result=0 if operation == "lock" else 1)
    unlock = _FakeWindowsFunction(result=0 if operation == "unlock" else 1)
    kernel = SimpleNamespace(LockFileEx=lock, UnlockFileEx=unlock)
    runtime = _FakeWindowsRuntime(handle=0x5678)
    monkeypatch.setattr(
        file_lock_module.importlib,
        "import_module",
        lambda name: runtime if name == "msvcrt" else pytest.fail(name),
    )
    monkeypatch.setattr(
        file_lock_module,
        "_windows_lock_function",
        lambda: kernel.LockFileEx,
    )
    monkeypatch.setattr(
        file_lock_module,
        "_windows_unlock_function",
        lambda: kernel.UnlockFileEx,
    )
    monkeypatch.setattr(file_lock_module.ctypes, "get_last_error", lambda: 123, raising=False)

    with pytest.raises(OSError) as caught:
        if operation == "lock":
            file_lock_module._windows_lock(41, FileLockMode.EXCLUSIVE)
        else:
            file_lock_module._windows_unlock(41)

    assert caught.value.errno == 123
    assert caught.value.strerror == "Windows file lock operation failed"


@pytest.mark.parametrize(
    "helper_name",
    ["_windows_lock_function", "_windows_unlock_function"],
)
def test_windows_function_loader_reports_unavailable_runtime(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    monkeypatch.delattr(file_lock_module.ctypes, "WinDLL", raising=False)

    with pytest.raises(OSError, match="Windows file locking is unavailable"):
        getattr(file_lock_module, helper_name)()


@pytest.mark.parametrize(
    "helper_name",
    ["_windows_lock_function", "_windows_unlock_function"],
)
def test_windows_function_loader_rejects_noncallable_runtime(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    monkeypatch.setattr(file_lock_module.ctypes, "WinDLL", None, raising=False)

    with pytest.raises(OSError, match="Windows file locking is unavailable"):
        getattr(file_lock_module, helper_name)()


@pytest.mark.parametrize(
    ("helper_name", "symbol"),
    [
        ("_windows_lock_function", "LockFileEx"),
        ("_windows_unlock_function", "UnlockFileEx"),
    ],
)
def test_windows_function_loader_preserves_missing_symbol_error(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    symbol: str,
) -> None:
    monkeypatch.setattr(
        file_lock_module.ctypes,
        "WinDLL",
        lambda *args, **kwargs: _MissingWindowsFunctions(),
        raising=False,
    )

    with pytest.raises(AttributeError) as caught:
        getattr(file_lock_module, helper_name)()

    assert str(caught.value) == (
        f"'_MissingWindowsFunctions' object has no attribute '{symbol}'"
    )


@pytest.mark.parametrize(
    "helper_name",
    ["_windows_lock_function", "_windows_unlock_function"],
)
@pytest.mark.parametrize(
    "failure",
    [
        AttributeError("loader attribute failure"),
        KeyError("loader key failure"),
        TypeError("loader type failure"),
    ],
)
def test_windows_function_loader_preserves_callable_loader_errors(
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
    failure: Exception,
) -> None:
    def failing_loader(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise failure

    monkeypatch.setattr(
        file_lock_module.ctypes,
        "WinDLL",
        failing_loader,
        raising=False,
    )

    with pytest.raises(type(failure)) as caught:
        getattr(file_lock_module, helper_name)()

    assert caught.value is failure
