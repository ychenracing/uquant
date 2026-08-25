#!/usr/bin/env python3
"""Thin CLI compatibility entry for the window competitor research adapter."""

# ruff: noqa: F401 - finite legacy import-mode aliases

from __future__ import annotations

from research.window_competitor_adapter import (
    LOCKED_SOURCES,
    POOLS,
    SYSTEMS,
    TARGET_END,
    TARGET_START,
    WINDOWS,
    Task,
    main,
)
from research.window_competitor_adapter import (
    broker_order_ledger as _broker_order_ledger,
)
from research.window_competitor_adapter import (
    default_source_roots as _default_source_roots,
)
from research.window_competitor_adapter import execute_matrix as _execute_matrix
from research.window_competitor_adapter import (
    link_missing_signal_dates as _link_missing_signal_dates,
)
from research.window_competitor_adapter import (
    validate_complete_rows as _validate_complete_rows,
)

__all__ = ("Task", "main")


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
