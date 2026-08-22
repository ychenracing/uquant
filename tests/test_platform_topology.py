from __future__ import annotations

import subprocess
from pathlib import Path


def test_strict_json_compatibility_path_exports_the_canonical_contract() -> None:
    """Breaks if strict JSON has two implementations or drops its old import path."""
    from uquant.contracts import json as compatibility
    from uquant.contracts import strict_json as canonical

    assert compatibility.strict_json_loads is canonical.strict_json_loads
    assert compatibility.canonical_json_bytes is canonical.canonical_json_bytes
    assert compatibility.canonical_json_sha256 is canonical.canonical_json_sha256


def test_atomic_compatibility_paths_export_the_canonical_contract() -> None:
    """Breaks if callers can reach a second atomic-publication implementation."""
    import uquant.atomic_io as legacy
    from uquant.infrastructure import atomic_files as canonical
    from uquant.infrastructure import atomic_io as compatibility

    for name in (
        "atomic_write_bytes",
        "atomic_write_text",
        "validate_atomic_output_boundary",
        "validate_atomic_output_path",
    ):
        expected = getattr(canonical, name)
        assert getattr(compatibility, name) is expected
        assert getattr(legacy, name) is expected


def test_source_provenance_compatibility_path_exports_split_contracts() -> None:
    """Breaks if surface loading and fingerprint framing collapse or diverge."""
    from uquant.provenance import fingerprints, source_surfaces, surfaces

    assert source_surfaces.load_source_surface_registry is (
        surfaces.load_source_surface_registry
    )
    assert source_surfaces.source_surface_fingerprint is (
        fingerprints.source_surface_fingerprint
    )
    assert source_surfaces.git_source_surface_fingerprint is (
        fingerprints.git_source_surface_fingerprint
    )


def test_git_source_reads_worktree_and_resolved_commit_bytes(tmp_path: Path) -> None:
    """Breaks if historical reads mix a resolved commit with worktree bytes."""
    from uquant.infrastructure.git_source import (
        read_git_file_bytes,
        read_worktree_file_bytes,
        resolve_git_commit,
    )

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "uquant-tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "uquant tests"],
        check=True,
    )
    member = tmp_path / "uquant/member.py"
    member.parent.mkdir()
    member.write_bytes(b"committed\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)

    commit = resolve_git_commit(tmp_path, "HEAD")
    member.write_bytes(b"worktree\n")

    assert len(commit) == 40
    assert read_git_file_bytes(tmp_path, commit, "uquant/member.py") == b"committed\n"
    assert read_worktree_file_bytes(tmp_path, "uquant/member.py") == b"worktree\n"
