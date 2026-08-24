#!/usr/bin/env python3
"""Preregister and seal the Risk Differential Closure evidence plane."""

# ruff: noqa: F401, RUF022 - finite legacy aliases and frozen public seam order

from __future__ import annotations

from functools import wraps
from pathlib import Path

from research.risk_differential_cli import (
    RISK_DIFFERENTIAL_AXES,
    STARTING_MAIN,
    TRADE_COMMIT,
    capability_inventory,
    replay,
    risk_differential_cli_seams,
    seal_initial_evidence,
    seal_trade_trace,
    validate_contract_axes,
)
from research.risk_differential_cli import cell_cache_identity as _cell_cache_identity
from research.risk_differential_cli import (
    derive_checkout_identity as _derive_checkout_identity,
)
from research.risk_differential_cli import (
    hash_checkout_python_sources as _hash_checkout_python_sources,
)
from research.risk_differential_cli import load_replay_cache as _load_replay_cache
from research.risk_differential_cli import main as _owner_main
from research.risk_differential_cli import preregister as _owner_preregister
from research.risk_differential_cli import (
    replay_result_sha256 as _replay_result_sha256,
)
from research.risk_differential_cli import (
    require_unchanged_checkout as _require_unchanged_checkout,
)
from research.risk_differential_cli import standard_warning_sets as _standard_warning_sets

__all__ = (
    "validate_contract_axes",
    "capability_inventory",
    "seal_trade_trace",
    "replay",
    "preregister",
    "seal_initial_evidence",
    "main",
)


@wraps(_owner_preregister)
def preregister(
    root: Path,
    *,
    baseline_root: Path,
    trade_root: Path,
    frozen_at_utc: str | None = None,
) -> None:
    with risk_differential_cli_seams(derive_identity=_derive_checkout_identity):
        _owner_preregister(
            root,
            baseline_root=baseline_root,
            trade_root=trade_root,
            frozen_at_utc=frozen_at_utc,
        )


@wraps(_owner_main)
def main() -> int:
    with risk_differential_cli_seams(derive_identity=_derive_checkout_identity):
        return _owner_main()


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
