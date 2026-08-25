from __future__ import annotations

import os
from pathlib import Path

import pytest

import uquant.atomic_io as legacy_atomic_io
import uquant.infrastructure.atomic_files as platform_atomic_io
import uquant.infrastructure.atomic_io as compatibility_atomic_io


def test_legacy_atomic_io_exports_the_platform_contract() -> None:
    for name in (
        "atomic_write_bytes",
        "atomic_write_text",
        "validate_atomic_output_boundary",
        "validate_atomic_output_path",
    ):
        canonical = getattr(platform_atomic_io, name)
        assert getattr(legacy_atomic_io, name) is canonical
        assert getattr(compatibility_atomic_io, name) is canonical


def test_atomic_write_fsyncs_payload_before_replace_and_parent_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "durable.txt"
    events: list[str] = []
    real_replace = os.replace

    def record_replace(source: Path, target: Path) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(platform_atomic_io.os, "fsync", lambda _: events.append("file-fsync"))
    monkeypatch.setattr(platform_atomic_io.os, "replace", record_replace)
    monkeypatch.setattr(
        platform_atomic_io,
        "_fsync_directory",
        lambda _: events.append("directory-fsync"),
    )

    platform_atomic_io.atomic_write_text(destination, "published\n")

    assert destination.read_bytes() == b"published\n"
    assert events == ["file-fsync", "replace", "directory-fsync"]


def test_atomic_replace_failure_preserves_destination_and_removes_staging_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.json"
    destination.write_bytes(b"prior\n")
    primary = OSError("injected replace failure")
    monkeypatch.setattr(
        platform_atomic_io.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(primary),
    )

    with pytest.raises(OSError, match="injected replace failure") as caught:
        platform_atomic_io.atomic_write_bytes(destination, b"candidate\n")

    assert caught.value is primary
    assert destination.read_bytes() == b"prior\n"
    assert tuple(tmp_path.glob(".state.json.*")) == ()
