"""Exercise the recovery CLI with real multipart bytes, including Python -O."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

RESTORE = (
    Path(__file__).resolve().parents[1]
    / "artifacts/branch-integration/start-date-research/pause-20260920/restore_snapshot.py"
)
CONTENT = b"preserved evidence\x00\xff\n"


def _fixture(tmp_path: Path, fault: str = "") -> tuple[Path, Path, Path]:
    script = tmp_path / "restore_snapshot.py"
    shutil.copyfile(RESTORE, script)
    digest = hashlib.sha256(CONTENT).hexdigest()
    row = {"path": "workspace/evidence.bin", "bytes": len(CONTENT), "sha256": digest, "mode": 0o644}
    if fault == "unsafe_path":
        row["path"] = "../escaped.bin"
    elif fault == "object_size":
        row["bytes"] += 1
    elif fault == "reserved_path":
        row["path"] = "workspace-snapshot.zip"
    rows = [row, row.copy()] if fault == "duplicate_path" else [row]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("MANIFEST.json", json.dumps({"files": rows}))
        archive.writestr("objects/" + digest, b"x" * len(CONTENT) if fault == "object_hash" else CONTENT)
    payload = buffer.getvalue()
    parts = tmp_path / "parts"
    parts.mkdir()
    split = len(payload) // 2
    metadata = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "parts": []}
    for index, (offset, data) in enumerate(((0, payload[:split]), (split, payload[split:]))):
        name = f"part{index:03d}"
        (parts / name).write_bytes(data)
        metadata["parts"].append({
            "name": name, "offset": offset, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if fault in {"part_size", "part_hash", "offset"}:
        first = metadata["parts"][0]
        if fault == "part_size":
            first["bytes"] += 1
        elif fault == "part_hash":
            first["sha256"] = "0" * 64
        else:
            first["offset"] = 1
    elif fault == "archive_size":
        metadata["bytes"] += 1
    elif fault == "archive_hash":
        metadata["sha256"] = "0" * 64
    (tmp_path / "SNAPSHOT.json").write_text(json.dumps(metadata), encoding="utf-8")
    return script, parts, tmp_path / "restored"


def _run(script: Path, parts: Path, output: Path, optimized: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *(["-O"] if optimized else []), str(script),
         "--parts", str(parts), "--output", str(output)],
        capture_output=True, text=True, timeout=20, check=False,
    )


@pytest.mark.parametrize("optimized", [False, True])
def test_restore_keeps_exact_bytes_and_does_not_launch_jobs(tmp_path: Path, optimized: bool) -> None:
    script, parts, output = _fixture(tmp_path)
    result = _run(script, parts, output, optimized)
    assert result.returncode == 0, result.stderr
    assert (output / "workspace/evidence.bin").read_bytes() == CONTENT
    assert (output / "RESTORED_MANIFEST.json").is_file()
    assert "Jobs remain paused" in result.stdout


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("fault", [
    "part_size", "part_hash", "offset", "archive_size", "archive_hash",
    "object_size", "object_hash", "unsafe_path", "reserved_path", "duplicate_path",
])
def test_restore_rejects_invalid_evidence(tmp_path: Path, optimized: bool, fault: str) -> None:
    script, parts, output = _fixture(tmp_path, fault)
    result = _run(script, parts, output, optimized)
    assert result.returncode != 0
    assert not (output / "RESTORED_MANIFEST.json").exists()
    assert not (tmp_path / "escaped.bin").exists()
    assert "Verified and restored" not in result.stdout


def test_restore_preserves_existing_work(tmp_path: Path) -> None:
    script, parts, output = _fixture(tmp_path)
    output.mkdir()
    original = output / "keep.bin"
    original.write_bytes(CONTENT)
    result = _run(script, parts, output, True)
    assert result.returncode != 0
    assert original.read_bytes() == CONTENT
    assert not (output / "workspace-snapshot.zip").exists()
