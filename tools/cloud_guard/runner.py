"""Cloud command evidence, not a ChatGPT service hook. Linux/POSIX only."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .export import export_bundle
from .journal import create, digest, emit, git_identity, packed, proc_start, resources
from .report import summarize


def stop_child(proc):
    # Only the process group we created, never a platform or unrelated process.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def command(args):
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        raise ValueError("a command after -- is required")
    cwd = Path(args.cwd).resolve(strict=True)
    directory, state = create(args.root, args.name, args.kind, args.checkpoint)
    state.update(argv_sha256=hashlib.sha256(packed(argv)).hexdigest(),
                 git=git_identity(cwd), supervisor_pid=os.getpid(),
                 supervisor_start=proc_start(os.getpid()), timeout_seconds=args.timeout)
    outpath, errpath = directory / "stdout.private.log", directory / "stderr.private.log"
    child = None
    interrupted = []
    old_handlers = {}
    started = time.monotonic()
    for signum in (signal.SIGTERM, signal.SIGINT):
        old_handlers[signum] = signal.signal(signum, lambda sig, frame: interrupted.append(sig))
    try:
        with outpath.open("xb") as out, errpath.open("xb") as err:
            os.chmod(outpath, 0o600)
            os.chmod(errpath, 0o600)
            try:
                child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=err, start_new_session=True,
                                         pass_fds=(args.lock_fd,))
            except OSError as exc:
                state.update(status="SPAWN_ERROR", error_type=type(exc).__name__, errno=exc.errno)
            if child is not None:
                state.update(child_pid=child.pid, child_start=proc_start(child.pid), status="RUNNING")
                emit(directory, state, "spawned")
                while child.poll() is None:
                    sample = resources(directory)
                    state["resource_latest"] = sample
                    elapsed = time.monotonic() - started
                    size = outpath.stat().st_size + errpath.stat().st_size
                    reason = ("SUPERVISOR_SIGNAL" if interrupted else
                              "SUPERVISOR_TIMEOUT" if elapsed >= args.timeout else
                              "DISK_FLOOR" if sample["disk_free_bytes"] < args.disk_floor_mib * 1024**2 else
                              "LOG_BUDGET" if size > args.log_budget_mib * 1024**2 else None)
                    if reason:
                        state.update(stop_reason=reason, supervisor_signals=interrupted)
                        emit(directory, state, "stop_requested")
                        stop_child(child)
                        break
                    emit(directory, state, "heartbeat")
                    time.sleep(min(args.heartbeat, max(0.01, args.timeout - elapsed)))
                state.update(status="EXITED", returncode=child.wait(), elapsed_seconds=round(time.monotonic()-started, 3))
            out.flush()
            err.flush()
            os.fsync(out.fileno())
            os.fsync(err.fileno())
        state["resource_end"] = resources(directory)
        state["logs"] = {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in (outpath, errpath)}
        emit(directory, state, "finished")
        receipt = export_bundle(Path(args.root), directory / "checkpoint.zip", True,
                                operation=directory.name)
        result = summarize(state)
        result["local_checkpoint"] = receipt
        print(json.dumps(result, ensure_ascii=False))
        return 0 if state.get("returncode") == 0 and not state.get("stop_reason") else 1
    except BaseException:
        if child is not None and child.poll() is None:
            stop_child(child)
        # Preserve STARTED/RUNNING journal if an unexpected recorder error occurs.
        raise
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
