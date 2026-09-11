"""Read audited non-executable evidence from immutable Git history."""
from __future__ import annotations

import io
import shutil
import subprocess  # nosec B404
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

_SOURCE_COMMIT = "7fcf9562e6c7f96250811acd80c2dd4ee46485e3"
_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def evidence_root() -> Path:
    """Materialize original evidence outside the checkout, without running old code."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required to read the immutable evidence source")
    archive = subprocess.run(
        [git, "-C", str(_ROOT), "archive", _SOURCE_COMMIT, "--", "artifacts",
         "benchmarks/cross_ai_stage1_baselines.json"],
        check=True, capture_output=True,
    ).stdout  # nosec B603
    destination = Path(tempfile.mkdtemp(prefix="uquant-evidence-"))
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("immutable evidence archive contains an unsafe member")
        bundle.extractall(destination, filter="data")
    return destination
