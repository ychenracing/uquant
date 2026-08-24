from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _leader,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
)


def test_confirmed_hard_risk_can_only_exit_an_armed_recovery_winner() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("trail_me", "drift_exit", "keep_a", "keep_b")
    frames = {
        symbol: _trend_frame(dates, close=np.linspace(0.80, price, len(dates)))
        for symbol, price in zip(symbols, (0.88, 0.88, 0.95, 0.95), strict=True)
    }
    account = AccountState(
        initial_cash=100.0,
        cash=50.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.70 if symbol == "trail_me" else 0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
            for symbol in symbols
        },
        anchor_weights={
            "trail_me": 0.30,
            "drift_exit": 0.10,
            "keep_a": 0.30,
            "keep_b": 0.30,
        },
        protected_weights={
            "trail_me": 0.30,
            "drift_exit": 0.10,
            "keep_a": 0.30,
            "keep_b": 0.30,
        },
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    hard_risk = RiskAssessment(
        Risk.RISK_OFF,
        0.80,
        3,
        {
            "freeze_new_risk": True,
            "held_damage_ratio": 1.0 / 3.0,
            "sector_stress_ratio": 0.10,
        },
        ("confirmed synchronized holdings shock",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=2,
    )

    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    first_observation = allocator.allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=hard_risk,
        user_panel=frames,
        leaders={
            symbol: _leader(
                symbol,
                0.90,
                industry="equipment" if symbol == "keep_b" else "optical",
            )
            for symbol in symbols
        },
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )
    first_weights = {target.symbol: target.weight for target in first_observation}
    assert first_weights["trail_me"] > 0.0
    assert first_weights["drift_exit"] > account.anchor_weights["drift_exit"]
    assert "trail_me" in account.anchor_weights
    assert "trail_me" in account.protected_weights
    assert "drift_exit" in account.anchor_weights
    assert "drift_exit" in account.protected_weights

    continuing_hard_risk = RiskAssessment(
        Risk.RISK_OFF,
        0.80,
        3,
        hard_risk.evidence,
        ("awaiting synchronized repair confirmation",),
        "PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=2,
    )
    # A one-day member bounce does not erase yesterday's independently
    # observed trail break while the same hard portfolio risk persists.
    frames["trail_me"].loc[date, "close"] = 0.95
    frames["drift_exit"].loc[date, "close"] = 0.95
    targets = allocator.allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=continuing_hard_risk,
        user_panel=frames,
        leaders={
            symbol: _leader(
                symbol,
                0.90,
                industry="equipment" if symbol == "keep_b" else "optical",
            )
            for symbol in symbols
        },
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert weights["trail_me"] == 0.0
    assert weights["drift_exit"] == 0.0
    assert 0.0 < weights["keep_a"] <= account.anchor_weights["keep_a"]
    assert 0.0 < weights["keep_b"] <= account.anchor_weights["keep_b"]
    assert sum(weights.values()) <= hard_risk.target_gross_cap
    assert "trail_me" not in account.anchor_weights
    assert "trail_me" not in account.protected_weights
    assert "drift_exit" not in account.anchor_weights
    assert "drift_exit" not in account.protected_weights

def test_confirmed_level1_repair_reaches_the_bounded_empty_book_probe() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_repair_candidate"
    close = np.ones(len(dates), dtype=float)
    close[-1] = 0.94
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 0.90,
            "ma60": 0.90,
            "ma120": 0.90,
            "ret5": -0.05,
            "ret20": -0.10,
            "ret60": -0.30,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    frozen_repair = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {
            "transition_damage": 0.20,
            "freeze_new_risk": True,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
        },
        ("level-1 capital repair",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState.empty(100.0)
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 2
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 0.94},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )
    assert targets[0].lifecycle == Lifecycle.RECOVERY.value
    assert account.candidate_tenure["tactical_active"] == 1

    generic_account = AccountState.empty(100.0)
    generic_account.capital_budget_level = 1
    generic_account.capital_budget_repair_streak = 2
    generic_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=generic_account,
        prices={symbol: 0.94},
    )
    assert generic_targets == ()

def test_caution_frozen_empty_book_deep_recovery_new_high_is_independently_confirmed() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_new_high"
    close = np.full(len(dates), 0.80)
    close[-1] = 1.00
    frame = _trend_frame(dates, close=close, ma20=0.90, ret20=0.10, ret60=-0.20)
    frame["ret120"] = -0.35
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(
            Risk.CAUTION,
            1.0,
            1,
            {"broad_ret120": 0.05, "tech_ret120": 0.05},
            (),
            "NONE",
            freeze_new_risk=True,
            reduction_level=1,
        ),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.70)},
        account=account,
        prices={symbol: 1.00},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_rebound_weight}
    )
    assert account.anchor_weights == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_rebound_weight}
    )

def test_level1_repair_without_candidate_retains_existing_generic_core() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "held_generic_core"
    frame = _trend_frame(dates)
    frozen_repair = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"transition_damage": 0.20, "freeze_new_risk": True},
        ("level-1 capital repair",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={
            symbol: Position(
                symbol,
                shares=20,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        active_leaders=[symbol],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 2

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.20}
    )
    assert targets[0].lifecycle == Lifecycle.CORE.value
    assert targets[0].reason_code == "risk_freeze_hold"

def test_first_level1_repair_step_reopens_only_explicit_protected_intent() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "saved_restore"
    frame = _trend_frame(dates)
    frozen_repair = RiskAssessment(
        Risk.CAUTION,
        0.60,
        1,
        {"transition_damage": 0.20, "freeze_new_risk": True},
        ("level-1 protected restoration",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    protected = AccountState.empty(100.0)
    protected.protected_weights = {symbol: 0.60}
    protected.capital_budget_level = 1
    protected.capital_budget_repair_streak = 1

    restored = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=protected,
        prices={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in restored} == pytest.approx({symbol: 0.60})
    assert restored[0].reason == "confirmed post-shock restoration"

    no_intent = AccountState.empty(100.0)
    no_intent.capital_budget_level = 1
    no_intent.capital_budget_repair_streak = 1
    assert (
        allocator.allocate(
            date=dates[-1],
            opportunity=Opportunity.TREND,
            risk=frozen_repair,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, 0.90)},
            account=no_intent,
            prices={symbol: 1.0},
        )
        == ()
    )

def test_synchronized_crisis_repair_reopens_only_protected_weights() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "reserve_a", "reserve_b")
    frame = _trend_frame(dates)
    confirmed_repair = RiskAssessment(
        Risk.CAUTION,
        0.50,
        4,
        {"transition_damage": 0.80, "freeze_new_risk": True},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=2,
        severity="COHORT_BREAK",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=75.0,
        positions={
            "lead": Position(
                "lead",
                shares=25,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        protected_weights={"lead": 0.60, "reserve_a": 0.16, "reserve_b": 0.16},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=confirmed_repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert sum(weights.values()) == pytest.approx(confirmed_repair.target_gross_cap)
    assert set(weights) == set(symbols)
    assert weights["lead"] > 0.25
    assert all(target.reason == "confirmed post-shock restoration" for target in targets)
    assert account.candidate_tenure.get("post_shock_restore_submitted", 0) == 1

    account.positions = {
        "lead": Position("lead", shares=59, avg_cost=1.0, lifecycle=Lifecycle.CORE.value),
        "reserve_a": Position(
            "reserve_a", shares=15, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
        ),
        "reserve_b": Position(
            "reserve_b", shares=15, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
        ),
    }
    account.cash = 11.0
    account.pending_orders.clear()
    normal = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")
    settled = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=normal,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.candidate_tenure["post_shock_restore_complete"] == 1
    assert {target.symbol: target.weight for target in settled} == pytest.approx(
        {"lead": 0.59, "reserve_a": 0.15, "reserve_b": 0.15}
    )
    assert {target.reason for target in settled} == {
        "completed post-shock restoration; retain price drift"
    }

def test_generic_protected_restore_waits_for_existing_confirmation_before_expansion() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("restore_a", "restore_b")
    frame = _trend_frame(dates)
    protected = AccountState.empty(100.0)
    protected.protected_weights = {symbol: 0.40 for symbol in symbols}
    protected.capital_budget_level = 1
    protected.capital_budget_repair_streak = 1
    first_repair = RiskAssessment(
        Risk.CAUTION,
        0.50,
        1,
        {"transition_damage": 0.20, "freeze_new_risk": True},
        ("level-1 protected restoration",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    first_targets = allocator.allocate(
        date=date,
        opportunity=Opportunity.RECOVERY,
        risk=first_repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=protected,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in first_targets} == pytest.approx(
        {symbol: 0.25 for symbol in symbols}
    )
    assert protected.candidate_tenure["post_shock_restore_submitted"] == 1
    assert protected.candidate_tenure["post_shock_restore_deferred_expansion"] == 1

    protected.positions = {
        symbol: Position(
            symbol,
            shares=25,
            avg_cost=1.0,
            lifecycle=Lifecycle.RECOVERY.value,
        )
        for symbol in symbols
    }
    protected.cash = 50.0
    protected.capital_budget_level = 0
    protected.capital_budget_repair_streak = 0
    deferred = allocator.allocate(
        date=date,
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE"),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=protected,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert protected.candidate_tenure.get("post_shock_restore_complete", 0) == 0
    assert {target.symbol: target.weight for target in deferred} == pytest.approx(
        {symbol: 0.25 for symbol in symbols}
    )

    protected.risk_streaks["protected_structure_normalization"] = (
        DEFAULT_CONFIG.recovery_risk_confirm_days
    )
    expanded = allocator.allocate(
        date=date,
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE"),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=protected,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in expanded} == pytest.approx(
        {symbol: 0.40 for symbol in symbols}
    )
    assert protected.candidate_tenure["post_shock_restore_deferred_expansion"] == 0

    protected.positions = {
        symbol: Position(
            symbol,
            shares=40,
            avg_cost=1.0,
            lifecycle=Lifecycle.RECOVERY.value,
        )
        for symbol in symbols
    }
    protected.cash = 20.0
    settled = allocator.allocate(
        date=date,
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE"),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=protected,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert protected.candidate_tenure["post_shock_restore_complete"] == 1
    assert {target.symbol: target.weight for target in settled} == pytest.approx(
        {symbol: 0.40 for symbol in symbols}
    )
