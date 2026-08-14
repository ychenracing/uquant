from __future__ import annotations

import hashlib
import platform
from pathlib import Path

import pandas as pd
import pytest

from uquant.engine import ProductionEngine
from uquant.validation.ai_era import (
    AI_ERA_ACUTE_WINDOWS,
    AI_ERA_START,
    AI_ERA_WINDOWS,
    require_ai_era_interval,
    runtime_environment_provenance,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "frozen"
OFFICIAL_WINDOWS = (
    "h1_2023",
    "h2_2023",
    "h1_2024",
    "h2_2024",
    "bull_crash_2025_2026",
    "continuous_ai_era",
)


def test_ai_era_contract_has_one_start_and_six_official_windows() -> None:
    assert AI_ERA_START == "2023-01-01"
    assert tuple(AI_ERA_WINDOWS) == OFFICIAL_WINDOWS
    assert AI_ERA_WINDOWS == {
        "h1_2023": ("2023-01-03", "2023-06-30"),
        "h2_2023": ("2023-07-03", "2023-12-29"),
        "h1_2024": ("2024-01-02", "2024-07-01"),
        "h2_2024": ("2024-07-01", "2024-12-31"),
        "bull_crash_2025_2026": ("2025-01-02", "2026-07-31"),
        "continuous_ai_era": ("2023-01-03", "2026-08-05"),
    }
    assert tuple(AI_ERA_ACUTE_WINDOWS) == OFFICIAL_WINDOWS
    assert all(start >= AI_ERA_START for start, _ in AI_ERA_WINDOWS.values())
    with pytest.raises(TypeError):
        AI_ERA_WINDOWS["invented"] = ("2023-01-01", "2023-01-02")  # type: ignore[index]


def test_continuous_ai_era_ends_on_latest_frozen_common_index_session() -> None:
    broad = pd.read_csv(DATA / "sh000300.csv", usecols=["date"])
    tech = pd.read_csv(DATA / "sh000682.csv", usecols=["date"])
    latest_common = max(set(broad["date"]) & set(tech["date"]))

    assert AI_ERA_WINDOWS["continuous_ai_era"][1] == latest_common


def test_ai_era_interval_rejects_pre_2023_economic_measurement() -> None:
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        require_ai_era_interval("2022-12-30", "2023-01-10")

    with pytest.raises(RuntimeError, match="starts after it ends"):
        require_ai_era_interval("2024-07-02", "2024-07-01")

    assert require_ai_era_interval("2023-01-01", "2023-01-03") == (
        "2023-01-01",
        "2023-01-03",
    )


def test_pre_2023_rows_are_loaded_only_as_warmup_not_economic_events() -> None:
    start = "2023-01-03"
    engine = ProductionEngine(DATA)
    result = engine.backtest(
        symbols=("sz300308", "sz300502", "sz300394"),
        start=start,
        end="2023-01-20",
    )

    assert min(frame.index.min() for frame in engine._raw.values()) < pd.Timestamp(AI_ERA_START)
    assert result["start"] == start
    assert all(row["date"] >= start for row in result["equity_curve"])
    assert all(fill["fill_date"] >= start for fill in result["final_account"]["fills"])
    assert all(order["signal_date"] >= start for order in result["order_ledger"])
    assert all(str(event["date"]) >= start for event in result["risk_events"])


def test_production_backtest_rejects_pre_2023_before_loading_market_data() -> None:
    engine = ProductionEngine(DATA)

    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        engine.backtest(
            symbols=("sz300308",),
            start="2022-12-30",
            end="2023-01-10",
        )

    assert engine._raw == {}


def test_runtime_provenance_binds_python_numeric_stack_and_lockfile() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = runtime_environment_provenance(root)

    assert payload == {
        "python_full_version": platform.python_version(),
        "numpy_version": "2.5.1",
        "pandas_version": "3.0.5",
        "uv_version": "0.11.33",
        "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
    }
