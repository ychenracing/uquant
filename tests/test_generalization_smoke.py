from __future__ import annotations

import pandas as pd
import pytest

from uquant.validation.ai_era import require_ai_era_interval
from uquant.validation.generalization import compute_pre_window_evidence
from uquant.validation.generalization_contract import (
    build_official_scenarios,
    official_windows,
)
from uquant.validation.universe import load_ai_universe


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


def test_canonical_economic_scenarios_use_only_pre_window_evidence() -> None:
    canonical = load_ai_universe()
    evidence = compute_pre_window_evidence(
        _prices(), canonical.symbols_as_of("2023-01-02"),
        window_start="2023-01-03", lookback_sessions=120,
    )
    scenarios = build_official_scenarios(
        window=official_windows(("h1_2023",))[0], evidence=evidence,
    )
    assert len([item for item in scenarios if item.economic]) == 32


def test_canonical_universe_rejects_changed_industry_authority(tmp_path) -> None:
    import json
    from pathlib import Path

    source = Path(__file__).parents[1] / "uquant/contracts/resources/ai_universe_manifest.json"
    payload = json.loads(source.read_bytes())
    payload["members"][0]["industry"] = "invented"
    altered = tmp_path / "universe.json"
    altered.write_text(json.dumps(payload))
    with pytest.raises((ValueError, RuntimeError)):
        load_ai_universe(altered)


def test_matrix_rejects_nonofficial_window_before_execution() -> None:
    with pytest.raises(ValueError, match="unknown"):
        official_windows(("custom-window",))


def test_economic_interval_rejects_pre_2023() -> None:
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        require_ai_era_interval("2022-01-03", "2022-06-30")
