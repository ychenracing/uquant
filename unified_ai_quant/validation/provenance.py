"""Fail-closed fingerprints shared by every validation artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import DEFAULT_CONFIG, SystemConfig


def assert_replay_signature_unchanged(
    initial: dict[str, object],
    current: dict[str, object],
    *,
    replay: str,
) -> None:
    """Reject evidence produced across two different input snapshots."""
    if current != initial:
        raise RuntimeError(
            f"{replay} inputs changed during long replay; "
            "refusing mixed-version evidence"
        )


def config_fingerprint(cfg: SystemConfig = DEFAULT_CONFIG) -> str:
    """Return the canonical production-configuration fingerprint."""
    return hashlib.sha256(
        json.dumps(cfg.to_dict(), sort_keys=True).encode()
    ).hexdigest()


def validation_fingerprint(root: Path | None = None) -> str:
    """Fingerprint validation code, contract tests, and benchmark adapter.

    Stress and robustness evidence is unsafe to reuse when the code that
    creates or judges it has changed.  Tests are part of that judging code,
    so they are deliberately included instead of hashing only the validation
    package.
    """
    project_root = root or Path(__file__).resolve().parents[2]
    paths = [
        *(project_root / "unified_ai_quant" / "validation").glob("*.py"),
        *(project_root / "tests").glob("*.py"),
        project_root / "scripts" / "run_legacy_common_adapter.py",
        project_root / "pyproject.toml",
    ]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        rendered = ", ".join(str(path.relative_to(project_root)) for path in missing)
        raise RuntimeError(f"validation provenance input missing: {rendered}")
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(project_root).as_posix()):
        digest.update(path.relative_to(project_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def bounded_data_fingerprint(data_dir: Path, *, end: str) -> str:
    """Hash every frozen CSV byte observable on or before ``end``."""
    files = sorted(data_dir.glob("*.csv"), key=lambda path: path.name.encode())
    if not files:
        raise RuntimeError("bounded data fingerprint found no CSV files")
    digest = hashlib.sha256()
    for path in files:
        digest.update(f"{path.name}\n".encode())
        with path.open("rb") as stream:
            try:
                digest.update(next(stream))
            except StopIteration as exc:
                raise RuntimeError(f"empty frozen CSV: {path.name}") from exc
            for line in stream:
                date = line.split(b",", 1)[0].decode()
                if date <= end:
                    digest.update(line)
    return digest.hexdigest()
