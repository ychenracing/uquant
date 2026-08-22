from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)

ROOT = Path(__file__).resolve().parents[1]


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
