"""Operational CLI for Future Holdout lanes and the isolated manual Journal."""

# ruff: noqa: F401, RUF022 - finite legacy aliases and frozen public seam order

from __future__ import annotations

from functools import wraps
from typing import Any

from research.future_holdout_cli import (
    compute_risk_differential_payload as _owner_compute_risk_differential_payload,
)
from research.future_holdout_cli import (
    differential_formal_scores as _differential_formal_scores,
)
from research.future_holdout_cli import future_holdout_cli_seams
from research.future_holdout_cli import future_holdout_parser as _parser
from research.future_holdout_cli import main as _owner_main
from research.future_holdout_cli import (
    validate_differential_session as _validate_differential_session,
)
from research.future_holdout_cli import (
    validate_prior_differential_source_identity as _validate_prior_differential_source_identity,
)
from research.future_holdout_cli import (
    validate_risk_differential_payload as _validate_risk_differential_payload,
)
from research.risk_replay_runtime import run_trade_cell, run_uquant_cell
from uquant.validation.holdout.cli_operations import (
    build_local_lane_report,
    load_journal_checkpoint,
    read_trusted_execution_journal,
    render_execution_journal,
    summarize_execution_journal,
    write_journal_checkpoint,
)

__all__ = (
    "build_local_lane_report",
    "load_journal_checkpoint",
    "write_journal_checkpoint",
    "read_trusted_execution_journal",
    "summarize_execution_journal",
    "render_execution_journal",
    "main",
)


def _owner_seams() -> Any:
    return future_holdout_cli_seams(
        trade_replay=run_trade_cell,
        uquant_replay=run_uquant_cell,
    )


@wraps(_owner_compute_risk_differential_payload)
def _compute_risk_differential_payload(*args: Any, **kwargs: Any) -> Any:
    with _owner_seams():
        return _owner_compute_risk_differential_payload(*args, **kwargs)


@wraps(_owner_main)
def main(argv: list[str] | None = None) -> int:
    with _owner_seams():
        return _owner_main(argv)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
