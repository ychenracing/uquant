from __future__ import annotations

import importlib
import sys

import pandas as pd
import pytest

from uquant.risk_sentinel.calibration import (
    calibrate_events,
    load_calibration_contract,
    summarize_calibration,
)


def _market(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": values},
        index=pd.bdate_range("2026-07-01", periods=len(values)),
    )


def test_calibration_contract_preregisters_horizons_and_shock() -> None:
    contract = load_calibration_contract()

    assert contract.horizons == (1, 3, 5, 10, 20)
    assert contract.shock_horizon == 20
    assert contract.shock_drawdown_lte == -0.08
    assert contract.lead_window_sessions == 5


def test_event_outcomes_use_only_rows_through_evaluation_end() -> None:
    values = [100.0] * 6 + [98.0, 96.0, 94.0, 92.0, 90.0] + [90.0] * 20
    market = _market(values)
    event_date = str(market.index[5].date())
    end = str(market.index[25].date())
    market.loc[market.index[-1] + pd.offsets.BDay(1), "close"] = 10000.0

    events = calibrate_events(
        assessments=(
            {
                "date": event_date,
                "level": "CAUTION",
                "confidence": 0.8,
                "first_evidence_date": str(market.index[4].date()),
            },
        ),
        market_frame=market,
        evaluation_end=end,
    )

    assert len(events) == 1
    event = events[0]
    assert event["return_1d"] == pytest.approx(-0.02)
    assert event["return_5d"] == pytest.approx(-0.10)
    assert event["drawdown_5d"] == pytest.approx(-0.10)
    assert event["return_20d"] == pytest.approx(-0.10)
    assert event["realized_shock"] is True
    assert event["false_positive"] is False
    assert event["lead_time"] == 1


def test_incomplete_horizon_remains_null_instead_of_reading_past_end() -> None:
    market = _market([100.0] * 12)
    event_date = str(market.index[8].date())

    event = calibrate_events(
        assessments=(
            {
                "date": event_date,
                "level": "CAUTION",
                "confidence": 0.8,
                "first_evidence_date": event_date,
            },
        ),
        market_frame=market,
        evaluation_end=str(market.index[10].date()),
    )[0]

    assert event["return_1d"] == 0.0
    assert event["return_3d"] is None
    assert event["drawdown_20d"] is None
    assert event["realized_shock"] is None
    assert event["false_positive"] is None


def test_calibration_summary_defines_precision_recall_cost_and_silence() -> None:
    sessions = tuple(
        str(value.date()) for value in pd.bdate_range("2026-07-01", periods=12)
    )
    events = (
        {
            "event_date": sessions[1],
            "first_evidence_date": sessions[0],
            "level": "CAUTION",
            "realized_shock": True,
            "lead_time": 1,
            "opportunity_cost": 0.0,
        },
        {
            "event_date": sessions[8],
            "first_evidence_date": sessions[8],
            "level": "CAUTION",
            "realized_shock": False,
            "lead_time": 0,
            "opportunity_cost": 0.06,
        },
    )

    summary = summarize_calibration(
        events=events,
        shock_dates=(sessions[1], sessions[5]),
        bull_dates=(sessions[8], sessions[10]),
        sessions=sessions,
    )

    assert summary["precision"] == 0.5
    assert summary["recall"] == 0.5
    assert summary["median_lead_time"] == 1.0
    assert summary["false_positive_opportunity_cost"] == 0.06
    assert summary["caution_freeze_opportunity_cost"] == 0.06
    assert summary["bull_silence_rate"] == 0.5
    assert summary["missed_shock_count"] == 1


def test_production_service_does_not_import_offline_calibration() -> None:
    for name in tuple(sys.modules):
        if name.startswith("uquant.risk_sentinel"):
            sys.modules.pop(name)

    importlib.import_module("uquant.risk_sentinel.service")

    assert "uquant.risk_sentinel.calibration" not in sys.modules
