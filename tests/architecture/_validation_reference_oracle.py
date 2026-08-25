from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, cast

_RUNNER_RELATIVE = "tests/architecture/_validation_oracle.py"
_CANDIDATE_RUNNER_RELATIVE = "tests/architecture/_validation_candidate_oracle.py"


def _run_isolated(*, snapshot: Path, runner: Path) -> dict[str, Any]:
    launcher = "\n".join(
        (
            "import runpy, sys",
            "snapshot, runner = sys.argv[1:]",
            "sys.path[:] = [snapshot] + [entry for entry in sys.path "
            "if '__editable__.uquant' not in entry]",
            "sys.meta_path[:] = [finder for finder in sys.meta_path "
            "if not finder.__class__.__module__.startswith('__editable___uquant_')]",
            "sys.argv[:] = [runner, snapshot]",
            "runpy.run_path(runner, run_name='__main__')",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", launcher, str(snapshot), str(runner)],
        cwd=snapshot,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def immutable_oracle_from_archive(
    *,
    root: Path,
    destination: Path,
    baseline_commit: str,
    baseline_tree: str,
    evidence_commit: str,
    runner_blob: str,
    runner_sha256: str,
    source_identities: list[dict[str, object]],
) -> dict[str, Any]:
    runner = root / _RUNNER_RELATIVE
    runner_source = runner.read_bytes()
    assert hashlib.sha256(runner_source).hexdigest() == runner_sha256
    observed_blob = subprocess.run(
        ["git", "rev-parse", f"{evidence_commit}:{_RUNNER_RELATIVE}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert observed_blob == runner_blob
    immutable_runner = subprocess.run(
        ["git", "cat-file", "blob", runner_blob],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    assert immutable_runner == runner_source
    observed_tree = subprocess.run(
        ["git", "rev-parse", f"{baseline_commit}^{{tree}}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert observed_tree == baseline_tree
    archive = subprocess.run(
        ["git", "archive", "--format=tar", baseline_commit],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as snapshot:
        snapshot.extractall(destination, filter="data")
    assert not (destination / ".git").exists()
    for identity in source_identities:
        source = (destination / str(identity["path"])).read_bytes()
        assert len(source) == identity["size_bytes"]
        assert hashlib.sha256(source).hexdigest() == identity["sha256"]
    assert (destination / "data/frozen/SHA256SUMS").is_file()
    injected = destination / "_task9_immutable_validation_oracle.py"
    injected.write_bytes(runner_source)
    return _run_isolated(snapshot=destination, runner=injected)


def candidate_oracle_from_subprocess(*, root: Path) -> dict[str, Any]:
    return _run_isolated(
        snapshot=root.resolve(),
        runner=(root / _RUNNER_RELATIVE).resolve(),
    )


def candidate_behavior_from_subprocess(
    *,
    root: Path,
    evidence_commit: str,
    runner_blob: str,
    runner_sha256: str,
) -> dict[str, Any]:
    """Run the source-projected collector after binding its implementation."""

    runner = root / _CANDIDATE_RUNNER_RELATIVE
    runner_source = runner.read_bytes()
    assert hashlib.sha256(runner_source).hexdigest() == runner_sha256
    observed_blob = subprocess.run(
        ["git", "rev-parse", f"{evidence_commit}:{_CANDIDATE_RUNNER_RELATIVE}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert observed_blob == runner_blob
    immutable_runner = subprocess.run(
        ["git", "cat-file", "blob", runner_blob],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    assert immutable_runner == runner_source
    launcher = "\n".join(
        (
            "import runpy, sys",
            "snapshot = sys.argv[1]",
            "sys.path[:] = [snapshot + '/tests', snapshot] + [entry for entry in sys.path "
            "if '__editable__.uquant' not in entry]",
            "sys.meta_path[:] = [finder for finder in sys.meta_path "
            "if not finder.__class__.__module__.startswith('__editable___uquant_')]",
            "sys.argv[:] = ['_validation_candidate_oracle.py', snapshot]",
            "runpy.run_module('architecture._validation_candidate_oracle', run_name='__main__')",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", launcher, str(root.resolve())],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


__all__ = (
    "candidate_behavior_from_subprocess",
    "candidate_oracle_from_subprocess",
    "immutable_oracle_from_archive",
)
