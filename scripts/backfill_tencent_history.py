#!/usr/bin/env python3
"""Thin CLI compatibility entry for the non-production Tencent adapter."""

# ruff: noqa: F401 - finite legacy import-mode aliases

from __future__ import annotations

import json
from dataclasses import asdict
from functools import wraps
from urllib.error import HTTPError
from urllib.request import Request

from research.tencent_history_adapter import (
    ENDPOINT,
    TECH_INDEX,
    BackfillResult,
    tencent_history_cli_seams,
)
from research.tencent_history_adapter import (
    TencentRedirectHandler as _TencentRedirectHandler,
)
from research.tencent_history_adapter import backfill_one as _backfill_one
from research.tencent_history_adapter import main as _owner_main
from research.tencent_history_adapter import prepend_tech_proxy as _prepend_tech_proxy
from research.tencent_history_adapter import write_metadata as _write_metadata

__all__ = ("BackfillResult", "main")


@wraps(_owner_main)
def main(argv: list[str] | None = None) -> int:
    with tencent_history_cli_seams(
        backfill=_backfill_one,
        prepend=_prepend_tech_proxy,
    ):
        return _owner_main(argv)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
