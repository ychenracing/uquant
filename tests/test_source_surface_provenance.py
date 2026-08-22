from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uquant.contracts.json import canonical_json_bytes, canonical_json_sha256
from uquant.provenance.source_surfaces import (
    git_source_surface_fingerprint,
    load_source_surface_registry,
    source_surface_fingerprint,
)


def _write_registry(
    root: Path,
    *,
    economic_sources: tuple[str, ...] = ("uquant/engine.py",),
    economic_resources: tuple[str, ...] = (
        "benchmarks/config_parameter_governance.json",
    ),
) -> Path:
    surfaces = [
        {
            "id": "economic_decision_v1",
            "source_paths": list(economic_sources),
            "resource_paths": list(economic_resources),
        },
        {
            "id": "execution_account_v1",
            "source_paths": ["uquant/account.py"],
            "resource_paths": [],
        },
        {
            "id": "sentinel_v1",
            "source_paths": ["uquant/risk_sentinel/service.py"],
            "resource_paths": [],
        },
        {
            "id": "validation_runner_v1",
            "source_paths": ["uquant/validation/cli.py"],
            "resource_paths": ["uv.lock"],
        },
        {
            "id": "full_package_v1",
            "source_paths": ["uquant/__init__.py"],
            "resource_paths": ["pyproject.toml", "requirements.txt", "uv.lock"],
        },
    ]
    unsealed: dict[str, object] = {
        "registry_version": 2,
        "surfaces": surfaces,
    }
    payload = {
        **unsealed,
        "canonical_sha256": canonical_json_sha256(unsealed),
    }
    path = root / "benchmarks/source_surface_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    return path


def _write_selected_members(root: Path) -> None:
    members = {
        "benchmarks/config_parameter_governance.json": b"{}\n",
        "uquant/engine.py": b"decision-v1\n",
    }
    for relative, payload in members.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_source_surface_fingerprint_frames_exact_reviewed_names_and_bytes(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    _write_selected_members(tmp_path)
    ignored = tmp_path / "ignored.txt"
    ignored.write_text("first\n", encoding="utf-8")

    first = source_surface_fingerprint(tmp_path, "economic_decision_v1")

    assert first == "44c05f1f1316ca6992b85ad0b388179da12165cbc7020e935515080548f14b62"
    ignored.write_text("second\n", encoding="utf-8")
    assert source_surface_fingerprint(tmp_path, "economic_decision_v1") == first
    (tmp_path / "uquant/engine.py").write_text("decision-v2\n", encoding="utf-8")
    assert source_surface_fingerprint(tmp_path, "economic_decision_v1") != first


def test_source_surface_fingerprint_rejects_missing_and_symlinked_members(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    _write_selected_members(tmp_path)
    engine = tmp_path / "uquant/engine.py"
    engine.unlink()

    with pytest.raises(ValueError, match="source surface member is missing or unsafe"):
        source_surface_fingerprint(tmp_path, "economic_decision_v1")

    outside = tmp_path / "outside.py"
    outside.write_text("decision-v1\n", encoding="utf-8")
    engine.symlink_to(outside)
    with pytest.raises(ValueError, match="source surface member is missing or unsafe"):
        source_surface_fingerprint(tmp_path, "economic_decision_v1")


def test_git_fingerprint_uses_the_registry_and_member_bytes_from_that_commit(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "uquant-tests@example.invalid")
    _git(tmp_path, "config", "user.name", "uquant tests")
    _write_registry(tmp_path)
    _write_selected_members(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "sealed source")
    expected = source_surface_fingerprint(tmp_path, "economic_decision_v1")

    replacement = tmp_path / "uquant/replacement.py"
    replacement.write_text("different-membership\n", encoding="utf-8")
    _write_registry(
        tmp_path,
        economic_sources=("uquant/replacement.py",),
        economic_resources=(),
    )
    (tmp_path / "uquant/engine.py").write_text("dirty working tree\n", encoding="utf-8")

    assert git_source_surface_fingerprint(
        tmp_path,
        "HEAD",
        "economic_decision_v1",
    ) == expected
    assert source_surface_fingerprint(tmp_path, "economic_decision_v1") != expected


def test_registry_loader_is_strict_for_worktree_and_git_documents(tmp_path: Path) -> None:
    registry_path = _write_registry(tmp_path)
    _write_selected_members(tmp_path)
    assert load_source_surface_registry(tmp_path).registry_version == 2

    text = registry_path.read_text(encoding="utf-8")
    registry_path.write_text(text.replace('"registry_version":2', '"registry_version":NaN'))
    with pytest.raises(ValueError, match="nonstandard JSON constant"):
        load_source_surface_registry(tmp_path)
