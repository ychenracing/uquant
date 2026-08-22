"""Compatibility surface for canonical economic attribution."""

from __future__ import annotations

from .builder import build_economic_attribution
from .concentration import RECONCILIATION_TOLERANCE, contribution_concentration
from .diagnostics import ExitRecord, attribution_diagnostics, post_exit_diagnostics
from .ledger import build_daily_ledger_row
from .replay_evidence import (
    _DAILY_REPLAY_FIELDS,
    _LEDGER_FIELDS,
    build_daily_replay_evidence_row,
)
from .validation import (
    _ACCOUNTING_FIELDS,
    _ATTRIBUTION_FIELDS,
    _COST_FIELDS,
    _GROUP_FIELDS,
    _LOT_COST_FIELDS,
    _LOT_FIELDS,
    validate_attribution_against_engine_result,
    validate_economic_attribution,
)

__all__ = (  # noqa: RUF022 - frozen public-name order
    "ExitRecord",
    "RECONCILIATION_TOLERANCE",
    "_ACCOUNTING_FIELDS",
    "_ATTRIBUTION_FIELDS",
    "_COST_FIELDS",
    "_DAILY_REPLAY_FIELDS",
    "_GROUP_FIELDS",
    "_LEDGER_FIELDS",
    "_LOT_COST_FIELDS",
    "_LOT_FIELDS",
    "attribution_diagnostics",
    "build_daily_ledger_row",
    "build_daily_replay_evidence_row",
    "build_economic_attribution",
    "contribution_concentration",
    "post_exit_diagnostics",
    "validate_attribution_against_engine_result",
    "validate_economic_attribution",
)

for _exported in (
    ExitRecord,
    attribution_diagnostics,
    build_daily_ledger_row,
    build_daily_replay_evidence_row,
    build_economic_attribution,
    contribution_concentration,
    post_exit_diagnostics,
    validate_attribution_against_engine_result,
    validate_economic_attribution,
):
    _exported.__module__ = __name__

del _exported
