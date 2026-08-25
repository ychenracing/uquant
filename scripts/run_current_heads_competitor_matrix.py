#!/usr/bin/env python3
"""Thin CLI compatibility entry for the current-HEAD competitor matrix."""

# ruff: noqa: RUF022 - frozen public seam order

from __future__ import annotations

from research import current_heads_competitor_matrix as _implementation  # noqa: F401
from research.current_heads_competitor_matrix import (
    ReplayRequest,
    assemble_matrix,
    build_matrix_cell,
    build_replay_requests,
    competitor_executor_policy,
    main,
    normalize_replay_row,
    observable_symbols_in_window,
    prepare_runtime,
    run_competitor_batch,
    run_uquant_official_batch,
    stage_bounded_market_data,
    visible_symbols,
)

__all__ = (
    "ReplayRequest",
    "stage_bounded_market_data",
    "visible_symbols",
    "observable_symbols_in_window",
    "normalize_replay_row",
    "build_matrix_cell",
    "build_replay_requests",
    "prepare_runtime",
    "competitor_executor_policy",
    "run_competitor_batch",
    "run_uquant_official_batch",
    "assemble_matrix",
    "main",
)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
