"""Native-Git persistence: claim before deciding, then atomic report/state publication."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

REPORT_REPOSITORY = "ychenracing/uquant"
REPORT_BRANCH = "uquant-daily-reports"


def fingerprint(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": digest}


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or ".git" in Path(relative).parts or not path.resolve().is_relative_to(root.resolve())
            or ".git" in path.resolve().relative_to(root.resolve()).parts):
        raise ValueError("unsafe report path")
    return path


def put_json(root: Path, relative: str, value: Any) -> None:
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(root: Path, relative: str) -> Any:
    return json.loads(safe_path(root, relative).read_text(encoding="utf-8"))


def verify_manifest(root: Path, manifest: dict[str, Any]) -> None:
    if not manifest or not isinstance(manifest, dict):
        raise ValueError("missing preservation manifest")
    for relative, identity in manifest.items():
        if fingerprint(safe_path(root, relative)) != identity:
            raise ValueError("preserved report bytes differ: " + relative)


def require_private_destination(token: str) -> None:
    if not token:
        raise RuntimeError("PRIVATE_DELIVERY_UNCONFIGURED")
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPORT_REPOSITORY,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    if metadata.get("full_name") != REPORT_REPOSITORY or metadata.get("private") is not True:
        raise RuntimeError("report destination must remain the approved private repository")


class GitStore:
    """One isolated report branch; push races are rejected, never force-pushed."""

    def __init__(self, root: Path, remote: str, env: dict[str, str] | None = None) -> None:
        self.root = root
        self.remote = remote
        self.env = env
        root.mkdir(parents=True, exist_ok=False)
        self.git("init", "--quiet")
        self.git("config", "user.name", "uquant daily observer")
        self.git("config", "user.email", "uquant-observer@users.noreply.github.com")
        self.git("remote", "add", "origin", remote)
        listing = self.git("ls-remote", "--exit-code", "origin", "refs/heads/" + REPORT_BRANCH, check=False)
        if listing.returncode == 0:
            self.git("fetch", "--quiet", "--depth=1", "origin", "refs/heads/" + REPORT_BRANCH)
            self.git("checkout", "--quiet", "-b", REPORT_BRANCH, "FETCH_HEAD")
        elif listing.returncode == 2:
            self.git("checkout", "--quiet", "--orphan", REPORT_BRANCH)
        else:
            raise RuntimeError("cannot establish existing report state; refusing initialization")

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(["git", *arguments], cwd=self.root, env=self.env,
                                capture_output=True, timeout=120, check=False)
        if check and result.returncode:
            # Do not expose response bodies or credential-bearing process environments.
            raise RuntimeError("report Git operation failed: " + arguments[0])
        return result

    def publish(self, paths: list[str], message: str) -> str:
        for relative in paths:
            safe_path(self.root, relative)
        token = os.environ.get("UQUANT_REPORT_WRITE_TOKEN", "")
        if self.remote.startswith("https://"):
            if self.remote != "https://github.com/" + REPORT_REPOSITORY + ".git":
                raise RuntimeError("unapproved report destination")
            require_private_destination(token)
        self.git("add", "--", *paths)
        self.git("commit", "--quiet", "-m", message)
        expected = self.git("rev-parse", "HEAD").stdout.decode().strip()
        # Unknown push results are reconciled by readback, never by another push.
        with contextlib.suppress(RuntimeError, subprocess.TimeoutExpired):
            self.git("push", "--quiet", "origin", "HEAD:refs/heads/" + REPORT_BRANCH)
        self.git("fetch", "--quiet", "origin", "refs/heads/" + REPORT_BRANCH)
        actual = self.git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
        if actual != expected:
            raise RuntimeError("report push conflict or uncertain result; manual reconciliation required")
        self.git("diff", "--exit-code", "HEAD", "FETCH_HEAD")
        for relative in paths:
            original = safe_path(self.root, relative)
            # Read actual remote blob bytes to disk without routing bodies through the model/logs.
            with tempfile.TemporaryFile() as remote_bytes:
                subprocess.run(["git", "show", actual + ":" + relative], cwd=self.root, env=self.env,
                               stdout=remote_bytes, stderr=subprocess.PIPE, timeout=120, check=True)
                remote_bytes.seek(0)
                with original.open("rb") as local_bytes:
                    while True:
                        block = local_bytes.read(1024 * 1024)
                        if block != remote_bytes.read(len(block)):
                            raise RuntimeError("remote report byte mismatch")
                        if not block:
                            if remote_bytes.read(1):
                                raise RuntimeError("remote report length mismatch")
                            break
                remote_bytes.seek(0)
                if hashlib.file_digest(remote_bytes, "sha256").hexdigest() != fingerprint(original)["sha256"]:
                    raise RuntimeError("remote report SHA-256 mismatch")
            local_oid = self.git("hash-object", "--", relative).stdout.strip()
            remote_oid = self.git("rev-parse", actual + ":" + relative).stdout.strip()
            if local_oid != remote_oid:
                raise RuntimeError("remote Git blob identity mismatch")
        return actual


def open_private_store(root: Path) -> GitStore:
    """The read-only source token is deliberately never a persistence fallback."""
    token = os.environ.get("UQUANT_REPORT_WRITE_TOKEN", "")
    require_private_destination(token)
    env = os.environ.copy()
    authorization = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + authorization})
    return GitStore(root, "https://github.com/" + REPORT_REPOSITORY + ".git", env)


def preserve_execution_logs(log_root: Path, checkout: Path) -> str:
    """Archive only closed, explicitly named logs through the private writer."""
    import shutil

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("Actions run identity required")
    store = open_private_store(checkout)
    prefix = f"runs/{run_id}-{attempt}"
    paths = []
    for name in ("bootstrap.log", "scan.log", "cloud.zip", "cloud.zip.receipt.json"):
        origin = log_root / name
        if origin.is_file():
            before = fingerprint(origin)
            destination = store.root / prefix / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
            if fingerprint(origin) != before or fingerprint(destination) != before:
                raise RuntimeError("log changed during preservation")
            paths.append(f"{prefix}/{name}")
    if not paths:
        raise ValueError("no completed execution logs available")
    put_json(store.root, prefix + "/manifest.json", {path: fingerprint(store.root / path) for path in paths})
    return store.publish([*paths, prefix + "/manifest.json"], "Preserve observer execution logs " + run_id)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Preserve closed observer logs privately")
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    options = parser.parse_args()
    preserve_execution_logs(options.logs, options.checkout)
