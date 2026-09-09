"""Recover actual saved replay observations without repeating economic execution."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil

import pytest

from research.cross_ai_strategy import rebuild_observation_archive, run_production_case
from uquant.contracts.strict_json import canonical_json_bytes


@pytest.fixture(scope="module")
def native_days(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-observations") / "case"
    result = run_production_case(case_id="champion", start="2023-01-03", end="2023-01-10",
                                 output_dir=directory)
    assert result["status"] == "COMPLETE", result["error"]
    assert result["sessions"] == 6 and result["accounting"]["reconciled"]
    assert len(list((directory / "observations").iterdir())) == 6
    return directory


def test_corrupted_final_gzip_rebuilds_exact_native_days_without_overwriting(native_days, tmp_path):
    directory = tmp_path / "copy"
    shutil.copytree(native_days, directory)
    raw = directory / "observations.jsonl.gz"
    original = gzip.decompress(raw.read_bytes())
    raw.write_bytes(raw.read_bytes()[:40])
    damaged = raw.read_bytes()
    result_before = (directory / "result.json").read_bytes()
    with pytest.raises(EOFError):
        gzip.decompress(damaged)
    recovered = directory / "recovered.jsonl.gz"
    rebuild_observation_archive(directory, recovered)
    assert gzip.decompress(recovered.read_bytes()) == original
    assert raw.read_bytes() == damaged
    assert (directory / "result.json").read_bytes() == result_before
    with pytest.raises(FileExistsError):
        rebuild_observation_archive(directory, raw)
    assert raw.read_bytes() == damaged


@pytest.mark.parametrize("damage", ["missing", "extra_duplicate", "duplicate_expected", "date", "identity", "content"])
def test_incomplete_or_mismatched_days_never_publish(native_days, tmp_path, damage):
    directory = tmp_path / "copy"
    shutil.copytree(native_days, directory)
    days = directory / "observations"
    first = sorted(days.iterdir())[0]
    envelope = json.loads(first.read_bytes())
    if damage == "missing":
        first.unlink()  # Only this test's private copy.
    elif damage == "extra_duplicate":
        (days / "duplicate.json").write_bytes(first.read_bytes())
    elif damage == "duplicate_expected":
        identity = json.loads((directory / "identity.json").read_bytes())
        identity["session_dates"].append(identity["session_dates"][-1])
        (directory / "identity.json").write_bytes(canonical_json_bytes(identity))
    else:
        if damage == "date":
            envelope["observation"]["date"] = "2023-01-04"
            envelope["observation_sha256"] = hashlib.sha256(canonical_json_bytes(envelope["observation"])).hexdigest()
        elif damage == "identity":
            envelope["identity_sha256"] = "0" * 64
        else:
            envelope["observation"]["equity"] += 1
        first.write_bytes(canonical_json_bytes(envelope))
    before = {p.name: p.read_bytes() for p in days.iterdir()}
    with pytest.raises(ValueError):
        rebuild_observation_archive(directory, directory / "recovered.jsonl.gz")
    assert not (directory / "recovered.jsonl.gz").exists()
    assert {p.name: p.read_bytes() for p in days.iterdir()} == before


def test_packaging_failure_keeps_all_saved_days_and_can_retry(native_days, tmp_path, monkeypatch):
    directory = tmp_path / "copy"
    shutil.copytree(native_days, directory)
    days = directory / "observations"
    before = {p.name: p.read_bytes() for p in days.iterdir()}
    original_write = gzip.GzipFile.write

    def fail_write(self, data):
        raise OSError("injected packaging failure")

    monkeypatch.setattr(gzip.GzipFile, "write", fail_write)
    destination = directory / "recovered.jsonl.gz"
    with pytest.raises(OSError, match="injected packaging failure"):
        rebuild_observation_archive(directory, destination)
    assert not destination.exists()
    assert {p.name: p.read_bytes() for p in days.iterdir()} == before
    monkeypatch.setattr(gzip.GzipFile, "write", original_write)
    rebuild_observation_archive(directory, destination)
    assert gzip.decompress(destination.read_bytes()) == gzip.decompress((native_days / "observations.jsonl.gz").read_bytes())


def test_atomic_publish_without_directory_flag_preserves_file_sync(tmp_path, monkeypatch):
    import os

    from research.cross_ai_strategy import _atomic_evidence_file

    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    original_fsync = os.fsync
    synced = []

    def record_sync(fd):
        synced.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", record_sync)
    destination = tmp_path / "day.json"
    with _atomic_evidence_file(destination) as stream:
        stream.write(b"real saved bytes")
    assert destination.read_bytes() == b"real saved bytes"
    assert len(synced) == 1
    with pytest.raises(FileExistsError), _atomic_evidence_file(destination) as stream:
        stream.write(b"must not replace")
    assert destination.read_bytes() == b"real saved bytes"
