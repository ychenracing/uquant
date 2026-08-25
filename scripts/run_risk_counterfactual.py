#!/usr/bin/env python3
"""Run fixed portfolio-level Risk Differential shadow policies."""

# ruff: noqa: F401, RUF022 - finite legacy aliases and frozen public seam order

from __future__ import annotations

from research.risk_counterfactual_cli import layered_targets as _layered_targets
from research.risk_counterfactual_cli import (
    load_job_checkpoint as _load_job_checkpoint,
)
from research.risk_counterfactual_cli import main, run_cell_policy
from research.risk_counterfactual_cli import (
    write_job_checkpoint as _write_job_checkpoint,
)

__all__ = ("run_cell_policy", "main")


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
