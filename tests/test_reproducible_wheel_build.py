from __future__ import annotations

import hashlib
import importlib
import subprocess
import zipfile
from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_source_export_ignores_long_lived_worktree_build_debris(tmp_path: Path) -> None:
    """A release source export must contain committed bytes, not stale worktree output."""

    module = importlib.import_module("scripts.build_reproducible_wheel")
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "uquant tests")
    (repository / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git(repository, "add", "committed.txt")
    _git(repository, "commit", "-qm", "fixture")

    stale = repository / "build" / "lib" / "stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("STALE = True\n", encoding="utf-8")
    destination = tmp_path / "exported"

    source = module.export_source_archive(repository, "HEAD", destination)

    assert source.commit == subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert (destination / "committed.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (destination / "build").exists()


def test_source_export_rejects_a_non_commit_ref(tmp_path: Path) -> None:
    """A release source reference must resolve to a commit, never an arbitrary tree."""

    module = importlib.import_module("scripts.build_reproducible_wheel")
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repository,
        input="not a commit\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    try:
        module.export_source_archive(repository, blob, tmp_path / "exported")
    except ValueError as exc:
        assert "commit" in str(exc)
    else:
        raise AssertionError("non-commit Git object was accepted as a release source")


def test_wheel_normalization_removes_zip_metadata_variance(tmp_path: Path) -> None:
    """Equivalent payloads must produce one byte identity across ZIP metadata variants."""

    module = importlib.import_module("scripts.build_reproducible_wheel")
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    fixtures = (
        (first, (("package/a.py", b"A\n"), ("package/data.json", b"{}\n")), 0o100664),
        (second, (("package/data.json", b"{}\n"), ("package/a.py", b"A\n")), 0o100600),
    )
    for path, members, mode in fixtures:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (name, content) in enumerate(members):
                info = zipfile.ZipInfo(name, date_time=(2024, 1, index + 1, 12, 0, 0))
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED)

    normalized_first = tmp_path / "normalized-first.whl"
    normalized_second = tmp_path / "normalized-second.whl"
    module.canonicalize_wheel(first, normalized_first)
    module.canonicalize_wheel(second, normalized_second)

    assert hashlib.sha256(normalized_first.read_bytes()).digest() == hashlib.sha256(
        normalized_second.read_bytes()
    ).digest()
    with zipfile.ZipFile(normalized_first) as archive:
        assert archive.namelist() == ["package/a.py", "package/data.json"]
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.external_attr >> 16 == 0o100644
