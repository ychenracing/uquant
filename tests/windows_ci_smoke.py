"""Minimal native-Windows smoke coverage for imports, CLIs, journals, and locks."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from uquant.execution_journal import (
    append_planned,
    execution_journal_checkpoint,
    read_execution_journal,
)
from uquant.infrastructure.file_lock import (
    FileLockMode,
    acquire_file_lock,
    release_file_lock,
)

ROOT = Path(__file__).resolve().parents[1]


def _require_help(*arguments: str) -> None:
    completed = subprocess.run(
        [sys.executable, *arguments, "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLI help failed for {arguments}: {completed.stdout}{completed.stderr}"
        )


def _import_observation_script() -> None:
    source = ROOT / "scripts/production_observation.py"
    spec = importlib.util.spec_from_file_location("windows_observation_smoke", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load production observation CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def _journal_smoke(directory: Path) -> None:
    journal = directory / "execution_journal.jsonl"
    append_planned(
        journal,
        plan_id="windows-smoke",
        recorded_at="2026-08-22T09:00:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    records = read_execution_journal(journal)
    checkpoint = execution_journal_checkpoint(records)
    if checkpoint.sequence != 1:
        raise RuntimeError("journal append/checkpoint smoke did not retain one record")
    if read_execution_journal(journal, trusted_checkpoint=checkpoint) != records:
        raise RuntimeError("journal checkpoint verification changed the record stream")


def _contention_smoke(directory: Path) -> None:
    lock_path = directory / "contention.lock"
    ready = directory / "child-ready"
    acquired = directory / "child-acquired"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    child: subprocess.Popen[str] | None = None
    locked = False
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
    try:
        acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
        locked = True
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path), str(ready), str(acquired)],
            cwd=ROOT,
            text=True,
        )
        deadline = time.monotonic() + 10.0
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            raise RuntimeError("lock contention child did not reach acquisition")
        try:
            child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise RuntimeError("exclusive Windows lock did not block the child process")
        if acquired.exists():
            raise RuntimeError("child acquired an exclusive lock before parent release")
        release_file_lock(descriptor)
        locked = False
        if child.wait(timeout=10.0) != 0:
            raise RuntimeError("lock contention child failed after parent release")
        if acquired.read_text(encoding="utf-8") != "acquired":
            raise RuntimeError("lock contention child did not record acquisition")
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10.0)
        if locked:
            with suppress(OSError):
                release_file_lock(descriptor)
        os.close(descriptor)


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("the Windows CI smoke must run on native Windows")
    _import_observation_script()
    _require_help("-m", "uquant")
    _require_help("-m", "uquant.validation")
    _require_help("-m", "uquant.risk_sentinel")
    _require_help("scripts/production_observation.py")
    with tempfile.TemporaryDirectory(prefix="uquant-windows-smoke-") as raw_directory:
        directory = Path(raw_directory)
        _journal_smoke(directory)
        _contention_smoke(directory)
    print("windows-smoke-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
