"""Immutable source identity for the read-only Risk Sentinel surface."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


def _legacy_cli_bytes(*, cli_path: Path, provenance_path: Path) -> bytes:
    """Project the relocated fingerprint body into its frozen CLI identity."""

    cli_source = cli_path.read_text(encoding="utf-8")
    provenance_source = provenance_path.read_text(encoding="utf-8")
    cli_tree = ast.parse(cli_source, filename=str(cli_path))
    provenance_tree = ast.parse(provenance_source, filename=str(provenance_path))
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
    cli_lines = cli_source.splitlines(keepends=True)
    provenance_lines = provenance_source.splitlines(keepends=True)
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
        "from .provenance import sentinel_source_fingerprint as _sentinel_source_fingerprint\n",
        "",
        1,
    )
    return legacy.encode("utf-8")


def _legacy_validation_bytes(validation_path: Path) -> bytes:
    """Project the relocated fingerprint import into its frozen CLI edge."""

    return validation_path.read_bytes().replace(
        b"from .provenance import sentinel_source_fingerprint\n",
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


def sentinel_source_fingerprint(repository_root: str | Path) -> str:
    """Hash the exact Sentinel Python path names and bytes."""

    root = Path(repository_root)
    package = root / "uquant" / "risk_sentinel"
    provenance_path = package / "provenance.py"
    paths = tuple(sorted(path for path in package.glob("*.py") if path != provenance_path))
    if not paths:
        raise RuntimeError("Sentinel source package is missing")
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(
            _legacy_cli_bytes(cli_path=path, provenance_path=provenance_path)
            if path.name == "cli.py"
            else _legacy_validation_bytes(path)
            if path.name == "validation.py"
            else path.read_bytes()
        )
        digest.update(b"\0")
    return digest.hexdigest()
