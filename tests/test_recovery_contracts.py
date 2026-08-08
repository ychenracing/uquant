from __future__ import annotations

import pandas as pd
import pytest

from unified_ai_quant.config import DEFAULT_CONFIG
from unified_ai_quant.engine import ProductionEngine
from unified_ai_quant.portfolio import PortfolioAllocator
from unified_ai_quant.types import (
    AccountState,
    LeaderScore,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
)

PRIMARY = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")


def test_recovery_breadth_perturbation_does_not_change_primary_path(data_dir):
    results = []
    for breadth in (DEFAULT_CONFIG.recovery_breadth_min * 0.90, DEFAULT_CONFIG.recovery_breadth_min * 1.10):
        result = ProductionEngine(
            data_dir, DEFAULT_CONFIG.override(recovery_breadth_min=breadth)
        ).backtest(symbols=PRIMARY, start="2025-04-01", end="2026-06-30")
        results.append(
            (
                result["final_wealth"],
                result["max_drawdown"],
                result["account_orders"],
                result["decision_digests"],
            )
        )
    assert results[0][0:3] == pytest.approx(results[1][0:3])
    assert results[0][3] == results[1][3]


def test_severe_recovery_restoration_is_capped_at_quarter_gross():
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "a": Position("a", shares=60, avg_cost=1.0, entry_date="2026-01-01"),
            "b": Position("b", shares=30, avg_cost=1.0, entry_date="2026-01-01"),
        },
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"a": 0.60, "b": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {
        symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {})
        for symbol in account.positions
    }
    risk = RiskAssessment(Risk.CAUTION, 0.35, 0, {}, (), "RECOVERY")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )
    assert sum(target.weight for target in targets) == pytest.approx(0.25)
    assert all(target.reason == "confirmed post-shock restoration" for target in targets)
