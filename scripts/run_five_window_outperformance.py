#!/usr/bin/env python3
"""Build and strictly evaluate the five-window four-system Pareto matrix."""

# ruff: noqa: F401, RUF022 - finite aliases and frozen public seam order

from __future__ import annotations

import shutil
from collections.abc import Sequence
from functools import wraps

from research.five_window_outperformance import (
    ACUTE_WINDOWS,
    COMPARISON_CONTRACT,
    COMPETITORS,
    INITIAL_CASH,
    LOCKED_COMPETITOR_SOURCES,
    METRICS,
    POOLS,
    SYSTEMS,
    TARGET_END,
    WINDOWS,
    build,
    evaluate,
    five_window_cli_seams,
)
from research.five_window_outperformance import (
    bounded_data_fingerprint as _bounded_data_fingerprint,
)
from research.five_window_outperformance import canonical_hash as _canonical_hash
from research.five_window_outperformance import (
    compact_competitor_rows as _compact_competitor_rows,
)
from research.five_window_outperformance import effective_symbols as _effective_symbols
from research.five_window_outperformance import git_identity as _git_identity
from research.five_window_outperformance import main as _owner_main
from research.five_window_outperformance import promotion_pools as _promotion_pools
from research.five_window_outperformance import validate_evidence as _validate_evidence

__all__ = ("evaluate", "build", "main")


@wraps(_owner_main)
def main(argv: Sequence[str] | None = None) -> int:
    with five_window_cli_seams(build_report=build):
        return _owner_main(argv)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
