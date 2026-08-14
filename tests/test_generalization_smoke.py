from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import pytest

from research import generalization_smoke as smoke_module
from research.generalization_smoke import build_smoke_scenarios, run_generalization_smoke
from uquant.validation.generalization import compute_pre_window_evidence
from uquant.validation.generalization_contract import (
    CORE_SYMBOLS,
    build_official_scenarios,
    official_windows,
)
from uquant.validation.universe import load_ai_universe


def _industries() -> dict[str, str]:
    return {member.symbol: member.industry for member in load_ai_universe().members}


def _prices() -> dict[str, pd.Series]:
    dates = pd.bdate_range("2022-06-01", "2023-01-02")
    return {
        symbol: pd.Series(
            [100.0 + index + day / 100.0 for day in range(len(dates))],
            index=dates,
            dtype=float,
        )
        for index, symbol in enumerate(load_ai_universe().symbols)
    }


def test_smoke_adapter_selects_the_canonical_economic_window_contract() -> None:
    """Catches revival of the old fixed 24-case/11-industry smoke matrix."""
    scenarios = build_smoke_scenarios(
        _prices(),
        load_ai_universe().symbols,
        _industries(),
        CORE_SYMBOLS,
        window_start="2023-01-03",
    )
    evidence = compute_pre_window_evidence(
        _prices(),
        load_ai_universe().symbols,
        window_start="2023-01-03",
        lookback_sessions=120,
    )
    expected = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=evidence,
    )

    assert len(scenarios) == 32
    assert tuple(item.name for item in scenarios) == tuple(
        item.name for item in expected if item.economic
    )


def test_smoke_adapter_rejects_duplicate_or_noncanonical_universe_rules() -> None:
    """Catches an adapter accepting a second industry or universe source."""
    industries = _industries()
    industries[next(iter(industries))] = "invented"
    with pytest.raises(ValueError, match="canonical AI universe"):
        build_smoke_scenarios(
            _prices(),
            load_ai_universe().symbols,
            industries,
            CORE_SYMBOLS,
            window_start="2023-01-03",
        )


def test_smoke_runner_delegates_only_an_exact_official_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches independent smoke execution or configurable random seed counts."""
    observed: dict[str, Any] = {}

    def fake_matrix(**kwargs: Any) -> Mapping[str, Any]:
        observed.update(kwargs)
        return {"passed": True, "cells": []}

    monkeypatch.setattr(smoke_module, "run_generalization_matrix", fake_matrix)
    result = run_generalization_smoke(
        data_dir="fixture-data",
        universe=load_ai_universe().symbols,
        industries=_industries(),
        prior_symbols=CORE_SYMBOLS,
        start="2023-01-03",
        end="2023-06-30",
    )

    assert result == {"passed": True, "cells": []}
    assert observed == {
        "data_dir": "fixture-data",
        "window_names": ("h1_2023",),
        "lookback_sessions": 120,
    }


def test_smoke_runner_rejects_pre_2023_before_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        smoke_module,
        "run_generalization_matrix",
        lambda **_: pytest.fail("old interval must not reach matrix execution"),
    )
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        run_generalization_smoke(
            data_dir="fixture-data",
            universe=load_ai_universe().symbols,
            industries=_industries(),
            prior_symbols=CORE_SYMBOLS,
            start="2022-01-03",
            end="2022-06-30",
        )
