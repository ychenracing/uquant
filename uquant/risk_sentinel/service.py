"""Pure service boundary for Independent Risk Sentinel evaluation."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .coverage import assess_coverage
from .evidence import build_market_evidence
from .models import SentinelAssessment
from .opinion import build_risk_opinion


def evaluate_sentinel(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    leader_symbols: tuple[str, ...] = (),
    capital_drawdown: float | None = None,
) -> SentinelAssessment:
    """Evaluate causal inputs without modifying them or any durable state."""

    expected_symbols = tuple(sorted(point_in_time_industries))
    coverage = assess_coverage(
        as_of=as_of,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        expected_symbols=expected_symbols,
        reference_panel=reference_panel,
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
    )
    evidence = build_market_evidence(
        as_of=as_of,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        reference_panel=reference_panel,
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
        leader_symbols=leader_symbols,
        capital_drawdown=capital_drawdown,
    )
    return build_risk_opinion(evidence=evidence, coverage=coverage)
