#!/usr/bin/env python3
"""Build and strictly evaluate the exact 2025-2026 four-system matrix."""

# ruff: noqa: F401, RUF022 - finite aliases and frozen public seam order

from __future__ import annotations

import shutil
from collections.abc import Sequence
from functools import wraps

from research.window_outperformance import (
    ACUTE_END,
    ACUTE_START,
    POOLS,
    SYSTEMS,
    TARGET_END,
    TARGET_START,
    build,
    evaluate,
    window_outperformance_cli_seams,
)
from research.window_outperformance import acute_return as _acute_return
from research.window_outperformance import git_executable as _git_executable
from research.window_outperformance import main as _owner_main

__all__ = ("evaluate", "build", "main")


@wraps(_owner_main)
def main(argv: Sequence[str] | None = None) -> int:
    with window_outperformance_cli_seams(build_report=build):
        return _owner_main(argv)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
