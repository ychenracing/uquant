"""Immutable source identity for the read-only Risk Sentinel surface."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from uquant.provenance.fingerprints import source_surface_fingerprint

from .source_identity_archive import immutable_source_identity_archive


def _legacy_cli_source_bytes(*, cli_source: bytes, provenance_source: bytes) -> bytes:
    """Project the relocated fingerprint body into its frozen CLI identity."""

    cli_text = cli_source.decode("utf-8")
    provenance_text = provenance_source.decode("utf-8")
    cli_tree = ast.parse(cli_text, filename="uquant/risk_sentinel/cli.py")
    provenance_tree = ast.parse(
        provenance_text,
        filename="uquant/risk_sentinel/provenance.py",
    )
    cli_function = next(
        node
        for node in cli_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "sentinel_source_fingerprint"
    )
    provenance_function = next(
        node
        for node in provenance_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_legacy_sentinel_source_fingerprint"
    )
    cli_lines = cli_text.splitlines(keepends=True)
    provenance_lines = provenance_text.splitlines(keepends=True)
    legacy_function = provenance_lines[
        provenance_function.lineno - 1 : provenance_function.end_lineno
    ]
    legacy_function[0] = legacy_function[0].replace(
        "def _legacy_sentinel_source_fingerprint",
        "def sentinel_source_fingerprint",
        1,
    )
    cli_lines[cli_function.lineno - 1 : cli_function.end_lineno] = legacy_function
    legacy = "".join(cli_lines).replace(
        "from .provenance import legacy_sentinel_source_fingerprint as _legacy_sentinel_source_fingerprint\n",
        "",
        1,
    )
    return legacy.encode("utf-8")


def _legacy_cli_bytes(*, cli_path: Path, provenance_path: Path) -> bytes:
    """Project the relocated fingerprint body into its frozen CLI identity."""
    return _legacy_cli_source_bytes(
        cli_source=cli_path.read_bytes(),
        provenance_source=provenance_path.read_bytes(),
    )


def _legacy_validation_bytes(validation_path: Path) -> bytes:
    """Project the relocated fingerprint import into its frozen CLI edge."""

    return validation_path.read_bytes().replace(
        b"from .provenance import legacy_sentinel_source_fingerprint as sentinel_source_fingerprint\n",
        b"from .cli import sentinel_source_fingerprint\n",
        1,
    )


def _legacy_sentinel_source_fingerprint(repository_root: str | Path) -> str:
    """Hash the exact Sentinel Python path names and bytes."""

    root = Path(repository_root)
    package = root / "uquant" / "risk_sentinel"
    paths = tuple(sorted(package.glob("*.py")))
    if not paths:
        raise RuntimeError("Sentinel source package is missing")
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def legacy_sentinel_source_fingerprint(repository_root: str | Path) -> str:
    """Return the sealed pre-refactor Sentinel identity for legacy contracts."""

    root = Path(repository_root)
    package = root / "uquant" / "risk_sentinel"
    if not tuple(package.glob("*.py")):
        raise RuntimeError("Sentinel source package is missing")
    members = dict(immutable_source_identity_archive())
    provenance_source = members["uquant/risk_sentinel/provenance.py"]
    digest = hashlib.sha256()
    for relative, source in members.items():
        if relative == "uquant/risk_sentinel/provenance.py":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            _legacy_cli_source_bytes(
                cli_source=source,
                provenance_source=provenance_source,
            )
            if relative.endswith("/cli.py")
            else source.replace(
                b"from .provenance import legacy_sentinel_source_fingerprint as sentinel_source_fingerprint\n",
                b"from .cli import sentinel_source_fingerprint\n",
                1,
            )
            if relative.endswith("/validation.py")
            else source
        )
        digest.update(b"\0")
    return digest.hexdigest()


def current_sentinel_surface_fingerprint(repository_root: str | Path) -> str:
    """Return the reviewed current Sentinel source-surface identity."""

    return source_surface_fingerprint(repository_root, "sentinel_v1")
