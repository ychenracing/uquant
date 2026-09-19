"""Cloud command evidence, not a ChatGPT service hook. Linux/POSIX only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def now():
    return datetime.now(UTC).isoformat()


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def proc_start(pid):
    # Field 22, accounting for spaces/parentheses in Linux's comm field.
    value = read_text(f"/proc/{pid}/stat")
    if not value:
        return None
    fields = value.rsplit(") ", 1)[-1].split()
    return fields[19] if len(fields) > 19 and fields[0] != "Z" else None


def runtime_id():
    try:
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        namespace = None
    values = [read_text("/proc/sys/kernel/random/boot_id"),
              read_text("/etc/hostname"), proc_start(1), namespace]
    # This is only an available-runtime identity, not a platform task ID.
    return hashlib.sha256(packed(values)).hexdigest()


def resources(root):
    events = read_text("/sys/fs/cgroup/memory.events")
    counts = {}
    for row in (events or "").splitlines():
        key, value = row.split()
        counts[key] = int(value)
    memory = read_text("/sys/fs/cgroup/memory.current")
    return {
        "observed_at_utc": now(), "disk_free_bytes": shutil.disk_usage(root).free,
        "memory_current_bytes": int(memory) if memory and memory.isdigit() else None,
        "memory_limit": read_text("/sys/fs/cgroup/memory.max"),
        "memory_events": counts,
        "memory_scope": "exposed cgroup; may include other processes",
    }


def atomic(path, data):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as target:
            os.chmod(temp, 0o600)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def emit(directory, state, event):
    state["updated_at_utc"] = now()
    record = {"event": event, "state": state}
    with (directory / "events.jsonl").open("ab") as stream:
        os.chmod(stream.name, 0o600)
        stream.write(packed(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    atomic(directory / "latest.json", packed(state) + b"\n")


def checkpoint(path):
    if not path:
        return None
    p = Path(path).resolve(strict=True)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": digest(p),
            "scope": "existing task recovery entry; content not duplicated"}


def git_identity(cwd):
    result = {}
    for key, args in (
        ("head", ["rev-parse", "HEAD"]),
        ("tracked_dirty", ["status", "--porcelain", "--untracked-files=no"]),
    ):
        try:
            done = subprocess.run(["git", "-C", str(cwd), *args],
                                  capture_output=True, timeout=3, check=False)
            if done.returncode == 0:
                result[key] = bool(done.stdout) if key == "tracked_dirty" else done.stdout.decode().strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result  # Not a complete untracked-work inventory or remote verification.


def create(root, name, kind, checkpoint_path=None):
    if not NAME.fullmatch(name):
        raise ValueError("name must be a short non-sensitive identifier")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    directory = root / ident
    directory.mkdir(mode=0o700)
    state = {"schema": 1, "operation_id": ident, "name": name, "kind": kind,
             "status": "STARTED", "started_at_utc": now(),
             "runtime_id": runtime_id(), "checkpoint": checkpoint(checkpoint_path),
             "request_id": None, "platform_error": None,
             "resource_start": resources(root), "raw_logs_private": True,
             "producer_sha256": producer_identity(), "ci": ci_identity()}
    emit(directory, state, "start")
    return directory, state


def load(directory):
    # Use the durable journal, not a possibly older latest.json after a crash.
    state = None
    with (directory / "events.jsonl").open("rb") as source:
        for line in source:
            if len(line) > 128 * 1024 or not line.endswith(b"\n"):
                raise ValueError("incomplete or oversized journal record")
            state = json.loads(line)["state"]
    if state is None:
        raise ValueError("journal has no durable start record")
    return state


def producer_identity():
    """Hash only the recorder source; never relabel it as the strategy identity."""
    return {p.name: digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def ci_identity():
    """Allowlisted correlation identifiers; never collect the environment."""
    result = {}
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_SHA"):
        value = os.environ.get(key, "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            result[key] = value
    return result
