from __future__ import annotations

import pandas as pd
import pytest

from unified_ai_quant.validation.comparison import (
    bounded_performance,
    lead_to_target,
    mature_false_exit_regrets,
    recovery_delay_opportunity_cost,
    replacement_spreads,
    risk_action_dates,
)


def _write_prices(path, symbol, closes):
    dates = pd.bdate_range("2025-01-02", periods=len(closes))
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1_000_000,
            "amount": 100_000_000,
        }
    )
    frame.to_csv(path / f"{symbol}.csv", index=False)
    return dates


def test_common_risk_and_equity_attribution_is_causal():
    dates = pd.bdate_range("2025-01-02", periods=6)
    row = {
        "equity_curve": [
            {"date": str(date.date()), "equity": value}
            for date, value in zip(dates, (100, 105, 95, 98, 102, 103), strict=True)
        ],
        "risk_events": [
            {"date": str(dates[1].date()), "to": "RISK_OFF"}
        ],
        "fills": [],
    }
    performance = bounded_performance(
        row, str(dates[0].date()), str(dates[-1].date())
    )
    assert performance["return"] == pytest.approx(0.03)
    assert performance["max_drawdown"] == pytest.approx(1 - 95 / 105)
    actions = risk_action_dates(
        row, start=str(dates[0].date()), end=str(dates[-1].date())
    )
    assert lead_to_target(actions, dates[4], dates) == 3


def test_exit_regret_and_replacement_spread_use_only_future_sessions(tmp_path):
    dates = _write_prices(tmp_path, "sz300308", [10 + index for index in range(50)])
    _write_prices(tmp_path, "sz300394", [10 + 2 * index for index in range(50)])
    row = {
        "fills": [
            {
                "fill_date": str(dates[4].date()),
                "symbol": "300308",
                "side": "SELL",
                "price": 14.0,
                "reason": "rotation exit",
                "lifecycle": "CORE",
            }
        ]
    }
    regrets = mature_false_exit_regrets(
        row,
        data_dir=tmp_path,
        mature_symbols={"sz300308"},
        horizon=20,
        as_of=dates[24],
    )
    assert regrets == pytest.approx([34 / 14 - 1])
    spreads = replacement_spreads(
        [
            {
                "signal_date": str(dates[4].date()),
                "old_symbol": "sz300308",
                "new_symbol": "sz300394",
                "old_close": 14.0,
                "new_close": 18.0,
            }
        ],
        data_dir=tmp_path,
        horizons=(20,),
        as_of=dates[24],
    )
    assert spreads[20] == pytest.approx([(58 / 18 - 1) - (34 / 14 - 1)])

    assert mature_false_exit_regrets(
        row,
        data_dir=tmp_path,
        mature_symbols={"sz300308"},
        horizon=20,
        as_of=dates[23],
    ) == []
    assert replacement_spreads(
        [
            {
                "signal_date": str(dates[4].date()),
                "old_symbol": "sz300308",
                "new_symbol": "sz300394",
                "old_close": 14.0,
                "new_close": 18.0,
            }
        ],
        data_dir=tmp_path,
        horizons=(20,),
        as_of=dates[23],
    )[20] == []


def test_recovery_delay_measures_missed_market_move_from_executable_buys():
    dates = pd.bdate_range("2020-03-23", periods=70)
    market = pd.Series([100.0 + index for index in range(70)], index=dates)
    old = {
        "fills": [
            {"fill_date": str(dates[2].date()), "side": "BUY"},
        ]
    }
    new = {
        "fills": [
            {"fill_date": str(dates[7].date()), "side": "BUY"},
        ]
    }
    result = recovery_delay_opportunity_cost(
        new,
        comparable_rows=(old,),
        trough=dates[0],
        market_close=market,
    )
    assert result["benchmark_date"] == str(dates[2].date())
    assert result["new_date"] == str(dates[7].date())
    assert result["opportunity_cost"] == pytest.approx(107 / 102 - 1)
