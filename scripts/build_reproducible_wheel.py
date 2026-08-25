"""Build a production wheel from a committed, clean Git source archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SOURCE_DATE_EPOCH = "315532800"
REQUIRED_BUILD_FRONTEND = "1.5.0"


@dataclass(frozen=True)
class ExportedSource:
    """Resolved Git identity for a clean source export."""

    commit: str
    tree: str


def _git(repository: Path, *arguments: str, text: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=text,
    )


def _resolve_commit(repository: Path, source_ref: str) -> ExportedSource:
    try:
        commit = _git(repository, "rev-parse", "--verify", f"{source_ref}^{{commit}}").stdout.strip()
        tree = _git(repository, "rev-parse", "--verify", f"{commit}^{{tree}}").stdout.strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        raise ValueError(f"source ref must resolve to a Git commit: {source_ref!r}: {detail}") from exc
    return ExportedSource(commit=commit, tree=tree)


def export_source_archive(repository: Path, source_ref: str, destination: Path) -> ExportedSource:
    """Export one commit without copying untracked files or worktree build output."""

    source = _resolve_commit(repository, source_ref)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"source export destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", source.commit],
        cwd=repository,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")
    return source


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonicalize_wheel(source: Path, destination: Path) -> None:
    """Write one deterministic ZIP container for an existing wheel payload."""

    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate member paths")
        for name in names:
            parts = Path(name).parts
            if not name or name.startswith(("/", "\\")) or ".." in parts or "\\" in name:
                raise ValueError(f"wheel contains an unsafe member path: {name!r}")
        payloads = {name: archive.read(name) for name in names}

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(payloads):
            is_directory = name.endswith("/")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o40755 if is_directory else 0o100644) << 16
            archive.writestr(info, payloads[name])


def _wheel_summary(path: Path, source: ExportedSource) -> dict[str, object]:
    members: list[dict[str, object]] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            content = archive.read(info.filename)
            members.append(
                {
                    "path": info.filename,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
    members.sort(key=lambda member: str(member["path"]))
    encoded = json.dumps(members, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    uquant_members = sum(str(member["path"]).startswith("uquant/") for member in members)
    dist_info_members = sum(".dist-info/" in str(member["path"]) for member in members)
    return {
        "bytes": path.stat().st_size,
        "dist_info_members": dist_info_members,
        "filename": path.name,
        "members": len(members),
        "payload_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "sha256": _sha256(path),
        "source_commit": source.commit,
        "source_tree": source.tree,
        "unexpected_members": [
            member["path"]
            for member in members
            if not (
                str(member["path"]).startswith("uquant/")
                or ".dist-info/" in str(member["path"])
            )
        ],
        "uquant_members": uquant_members,
    }


def build_reproducible_wheel(
    repository: Path,
    source_ref: str,
    output_directory: Path,
    *,
    force: bool = False,
) -> dict[str, object]:
    """Build exactly one wheel from a temporary clean source export."""

    observed_frontend = importlib.metadata.version("build")
    if observed_frontend != REQUIRED_BUILD_FRONTEND:
        raise RuntimeError(
            f"build frontend must be {REQUIRED_BUILD_FRONTEND}, observed {observed_frontend}"
        )
    with tempfile.TemporaryDirectory(prefix="uquant-wheel-") as temporary:
        root = Path(temporary)
        source_directory = root / "source"
        built_directory = root / "dist"
        source = export_source_archive(repository, source_ref, source_directory)
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(built_directory),
                str(source_directory),
            ],
            cwd=repository,
            env=environment,
            check=True,
        )
        wheels = sorted(built_directory.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected exactly one wheel, observed {len(wheels)}")
        canonical_wheel = root / "canonical" / wheels[0].name
        canonicalize_wheel(wheels[0], canonical_wheel)
        output_directory.mkdir(parents=True, exist_ok=True)
        destination = output_directory / wheels[0].name
        if destination.exists() and not force:
            raise FileExistsError(f"refusing to overwrite existing wheel: {destination}")
        shutil.copyfile(canonical_wheel, destination)
        return _wheel_summary(destination, source)


def _repository_root() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    )
    return Path(completed.stdout.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.build_reproducible_wheel",
        description="Build a byte-reproducible uquant wheel from a committed Git archive.",
    )
    parser.add_argument("--source-ref", required=True, help="Git commit or commit-resolving ref")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing same-name wheel")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    summary = build_reproducible_wheel(
        _repository_root(),
        arguments.source_ref,
        arguments.output_dir,
        force=arguments.force,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
