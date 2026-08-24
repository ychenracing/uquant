"""Cryptographically verify every file in a frozen market-data snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..data import DataContractError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^(?:sh|sz|bj)[0-9]{6}$")


def _reject_duplicate_manifest_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise DataContractError(f"frozen manifest contains duplicate key: {key}")
        payload[key] = value
    return payload


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_entries(path: Path) -> dict[str, str]:
    """Parse a canonical SHA-256 manifest while rejecting unsafe paths."""
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DataContractError(f"cannot read frozen checksum file: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != 2 or not _SHA256.fullmatch(parts[0]):
            raise DataContractError(f"invalid SHA256SUMS entry on line {line_number}")
        filename = parts[1]
        if Path(filename).name != filename or not filename.endswith(".csv"):
            raise DataContractError(f"unsafe frozen-data filename: {filename!r}")
        if filename in entries:
            raise DataContractError(f"duplicate frozen-data checksum: {filename}")
        entries[filename] = parts[0]
    if not entries:
        raise DataContractError("frozen checksum file is empty")
    return entries


def verify_data_manifest(root: str | Path) -> dict[str, Any]:
    """Fail closed unless manifest, checksum inventory, and CSV bytes agree."""
    data_root = Path(root)
    if not data_root.is_dir():
        raise DataContractError(f"data directory does not exist: {data_root}")
    manifest_path = data_root / "DATA_MANIFEST.json"
    checksums_path = data_root / "SHA256SUMS"
    if manifest_path.is_symlink() or checksums_path.is_symlink():
        raise DataContractError("frozen metadata must be regular files")
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataContractError("frozen DATA_MANIFEST.json is missing or corrupt") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("results"), list):
        raise DataContractError("frozen manifest has an invalid results inventory")

    expected = _checksum_entries(checksums_path)
    manifest_hashes: dict[str, str] = {}
    for item in manifest["results"]:
        if not isinstance(item, dict):
            raise DataContractError("frozen manifest result must be an object")
        symbol = str(item.get("symbol", ""))
        filename = f"{symbol}.csv"
        digest = str(item.get("sha256", ""))
        if not _SYMBOL.fullmatch(symbol) or not _SHA256.fullmatch(digest):
            raise DataContractError(f"frozen manifest has an invalid result for {symbol!r}")
        if filename in manifest_hashes:
            raise DataContractError(f"frozen manifest repeats {filename}")
        manifest_hashes[filename] = digest

    actual_files = {path.name for path in data_root.glob("*.csv")}
    inventories = {
        "manifest": set(manifest_hashes),
        "checksums": set(expected),
        "directory": actual_files,
    }
    if len({frozenset(items) for items in inventories.values()}) != 1:
        detail = ", ".join(f"{name}={len(items)}" for name, items in inventories.items())
        raise DataContractError(f"frozen data inventories differ ({detail})")

    for filename, expected_digest in sorted(expected.items()):
        path = data_root / filename
        if path.is_symlink() or not path.is_file():
            raise DataContractError(f"frozen data must be a regular file: {filename}")
        if manifest_hashes[filename] != expected_digest:
            raise DataContractError(f"manifest checksum differs for {filename}")
        observed = _digest(path)
        if observed != expected_digest:
            raise DataContractError(f"frozen data checksum mismatch: {filename}")

    return {
        "snapshot_id": str(manifest.get("snapshot_id", "")),
        "files_verified": len(expected),
        "manifest_sha256": _digest(manifest_path),
        "checksums_sha256": _digest(checksums_path),
    }


_reject_duplicate_keys = _reject_duplicate_manifest_keys
