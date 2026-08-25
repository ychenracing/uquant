from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from uquant.contracts.strict_json import canonical_json_sha256


def assert_trace_seals(
    payload: dict[str, object],
    *,
    baseline_commit: str,
    baseline_tree: str,
    checkpoint_names: tuple[str, ...],
) -> None:
    assert payload["baseline_commit"] == baseline_commit
    assert payload["baseline_tree"] == baseline_tree
    assert payload["checkpoint_names"] == list(checkpoint_names)
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    assert payload["payload_sha256"] == canonical_json_sha256(unsigned)
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    assert sum(int(scenario["record_count"]) for scenario in scenarios) == 60
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        records = scenario["records"]
        assert isinstance(records, list)
        assert scenario["record_count"] == len(records)
        assert scenario["records_sha256"] == canonical_json_sha256(records)
        for record in records:
            assert isinstance(record, dict)
            checkpoints = record["checkpoint_sha256"]
            assert isinstance(checkpoints, list)
            assert [checkpoint["name"] for checkpoint in checkpoints] == list(
                checkpoint_names
            )
            assert all(len(str(checkpoint["sha256"])) == 64 for checkpoint in checkpoints)


def immutable_trace_from_archive(
    *,
    root: Path,
    destination: Path,
    baseline_commit: str,
    implementation_identities: dict[str, tuple[str, int]],
    runner: Path,
    runner_sha256: str,
    logic_commit: str | None = None,
    logic_blob: str | None = None,
) -> dict[str, object]:
    """Replay with baseline sources/data and a byte-pinned independent trace runner."""

    runner_source = runner.read_bytes()
    assert hashlib.sha256(runner_source).hexdigest() == runner_sha256
    if logic_commit is not None or logic_blob is not None:
        assert logic_commit is not None and logic_blob is not None
        observed_blob = subprocess.run(
            ["git", "rev-parse", f"{logic_commit}:tests/architecture/_task8_portfolio_trace.py"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert observed_blob == logic_blob
        immutable_runner = subprocess.run(
            ["git", "cat-file", "blob", logic_blob],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        assert immutable_runner == runner_source

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
    for relative_path, (expected_sha256, expected_size) in implementation_identities.items():
        source = (destination / relative_path).read_bytes()
        assert len(source) == expected_size
        assert hashlib.sha256(source).hexdigest() == expected_sha256
    assert (destination / "data" / "frozen" / "SHA256SUMS").is_file()

    injected_runner = destination / "_task8_immutable_portfolio_trace_runner.py"
    injected_runner.write_bytes(runner_source)
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
        [sys.executable, "-I", "-c", launcher, str(destination), str(injected_runner)],
        cwd=destination,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


__all__ = ("assert_trace_seals", "immutable_trace_from_archive")
