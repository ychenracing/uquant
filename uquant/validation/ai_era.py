"""Single calendar and economic-measurement contract for the AI era."""

from __future__ import annotations

import hashlib
import platform
import shutil

# Security: uv is invoked only through a resolved executable and fixed arguments.
import subprocess  # nosec B404
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

AI_ERA_START: Final = "2023-01-01"

AI_ERA_WINDOWS: Final[dict[str, tuple[str, str]]] = {
    "h1_2023": ("2023-01-03", "2023-06-30"),
    "h2_2023": ("2023-07-03", "2023-12-29"),
    "h1_2024": ("2024-01-02", "2024-07-01"),
    "h2_2024": ("2024-07-01", "2024-12-31"),
    "bull_crash_2025_2026": ("2025-01-02", "2026-07-31"),
    "continuous_ai_era": ("2023-01-03", "2026-08-05"),
}

AI_ERA_ACUTE_WINDOWS: Final[dict[str, tuple[str, str]]] = {
    "h1_2023": ("2023-04-20", "2023-05-25"),
    "h2_2023": ("2023-07-26", "2023-08-25"),
    "h1_2024": ("2024-01-03", "2024-02-02"),
    "h2_2024": ("2024-08-01", "2024-09-02"),
    "bull_crash_2025_2026": ("2026-06-30", "2026-07-30"),
    "continuous_ai_era": ("2026-06-30", "2026-07-30"),
}


def require_ai_era_interval(start: str, end: str) -> tuple[str, str]:
    """Validate and normalize an interval used for economic evaluation."""

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start_date < date.fromisoformat(AI_ERA_START):
        raise RuntimeError(f"economic validation cannot start before {AI_ERA_START}")
    if start_date > end_date:
        raise RuntimeError("economic validation interval starts after it ends")
    return start_date.isoformat(), end_date.isoformat()


def runtime_environment_provenance(repository_root: str | Path) -> dict[str, str]:
    """Return the exact interpreter, numerical stack, uv, and lock identity."""

    root = Path(repository_root)
    lock_path = root / "uv.lock"
    executable = shutil.which("uv")
    if not lock_path.is_file() or executable is None:
        raise RuntimeError("cannot resolve the locked AI-era runtime")
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    parts = completed.stdout.strip().split()
    if len(parts) < 2 or parts[0] != "uv":
        raise RuntimeError("cannot identify the uv runtime")
    return {
        "python_full_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "uv_version": parts[1],
        "uv_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    }
