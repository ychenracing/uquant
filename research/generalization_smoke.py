"""Compatibility adapter to the canonical AI-era generalization matrix."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from uquant.validation.ai_era import require_ai_era_interval
from uquant.validation.generalization import GeneralizationScenario, compute_pre_window_evidence
from uquant.validation.generalization_contract import (
    CORE_SYMBOLS,
    build_official_scenarios,
    official_windows,
)
from uquant.validation.generalization_matrix import run_generalization_matrix
from uquant.validation.universe import load_ai_universe


def _require_canonical_inputs(
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
) -> None:
    canonical = load_ai_universe()
    expected_industries = {member.symbol: member.industry for member in canonical.members}
    if (
        tuple(sorted(universe)) != canonical.symbols
        or dict(industries) != expected_industries
        or tuple(prior_symbols) != CORE_SYMBOLS
    ):
        raise ValueError("generalization smoke must use the canonical AI universe contract")


def _window_from_bounds(start: str, end: str) -> str:
    start, end = require_ai_era_interval(start, end)
    matches = [
        window.name for window in official_windows() if (window.start, window.end) == (start, end)
    ]
    if len(matches) != 1:
        raise ValueError("generalization smoke requires exact official window bounds")
    return matches[0]


def build_smoke_scenarios(
    prices: Mapping[str, pd.Series | pd.DataFrame],
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    *,
    window_start: str,
    lookback_sessions: int = 120,
) -> tuple[GeneralizationScenario, ...]:
    """Expose the canonical economic scenarios for legacy research callers."""
    _require_canonical_inputs(universe, industries, prior_symbols)
    matches = [window for window in official_windows() if window.start == window_start]
    if not matches:
        raise ValueError("generalization smoke requires an official window start")
    canonical = load_ai_universe()
    causal_cutoff = (pd.Timestamp(window_start) - pd.Timedelta(days=1)).date().isoformat()
    pit_symbols = canonical.symbols_as_of(causal_cutoff)
    evidence = compute_pre_window_evidence(
        prices,
        pit_symbols,
        window_start=window_start,
        lookback_sessions=lookback_sessions,
    )
    return tuple(
        scenario.raw_scenario
        for scenario in build_official_scenarios(
            window=matches[0],
            evidence=evidence,
            universe=canonical,
        )
        if scenario.raw_scenario is not None
    )


def run_generalization_smoke(
    *,
    data_dir: str | Path,
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    lookback_sessions: int = 120,
) -> Mapping[str, Any]:
    """Delegate a legacy smoke request to one exact canonical matrix shard."""
    window_name = _window_from_bounds(start, end)
    _require_canonical_inputs(universe, industries, prior_symbols)
    return run_generalization_matrix(
        data_dir=data_dir,
        window_names=(window_name,),
        lookback_sessions=lookback_sessions,
    )
