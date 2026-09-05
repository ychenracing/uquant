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
    PendingOrder,
    Position,
    Risk,
    RiskAssessment,
)


def test_same_session_recovery_observation_cannot_advance_unqualified_entry() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "new_a")
    close = np.linspace(0.80, 1.00, len(dates))
    panel = {}
    for symbol in symbols:
        frame = _trend_frame(dates, close=close)
        frame["ret120"] = -0.40
        panel[symbol] = frame
    leaders = {symbol: _leader(symbol, 0.90 - 0.01 * index) for index, symbol in enumerate(symbols)}
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={"lead": Position("lead", shares=60, avg_cost=1.0)},
        anchor_weights={"lead": 0.60},
        recovery_anchor_date=str(dates[-3].date()),
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.20, "tech_ret120": -0.20},
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    first = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )
    second = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )
    third = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in first} == pytest.approx({"lead": 0.60})
    assert {target.symbol: target.weight for target in second} == pytest.approx({"lead": 0.60})
    assert account.anchor_weights == {"lead": 0.60}
    assert {target.symbol: target.weight for target in third} == pytest.approx({"lead": 0.60})
    assert account.positions["lead"].shares == 60
    assert account.cash == 40.0

def test_unqualified_recovery_expansion_keeps_the_physical_incumbent() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "new_a", "new_b")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            "lead": Position(
                "lead",
                shares=60,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        anchor_weights={"lead": 0.60},
        recovery_anchor_date=str(dates[-3].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
        },
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx({"lead": 0.60})
    assert account.anchor_weights == {"lead": 0.60}
    assert account.positions["lead"].shares == 60
    assert account.cash == 40.0

def test_risk_anchor_breadth_does_not_create_tradable_qualification() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    # Broad risk-anchor evidence is not each tradable's entry confirmation.
    assert targets == ()
    assert account.anchor_weights == {}
    assert account.cash == 100.0
    assert account.positions == {}

    partially_filled = AccountState(
        initial_cash=300.0,
        cash=200.0,
        positions={
            symbols[0]: Position(
                symbols[0],
                shares=100,
                avg_cost=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        anchor_weights=dict(account.anchor_weights),
        recovery_anchor_date=str(dates[-1].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=300.0,
        capital_peak=300.0,
    )
    resumed_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=partially_filled,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in resumed_targets} == pytest.approx(
        {symbols[0]: 1.0 / 3.0}
    )
    assert partially_filled.positions[symbols[0]].shares == 100
    assert partially_filled.cash == 200.0

    caution = RiskAssessment(
        Risk.CAUTION,
        0.70,
        1,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        ("capital repair remains incomplete",),
        "RECOVERY",
    )
    caution_account = AccountState.empty(100.0)
    caution_account.capital_budget_level = 1
    caution_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=caution,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=caution_account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    caution_weights = {target.symbol: target.weight for target in caution_targets}
    assert caution_targets == ()
    assert caution_account.capital_budget_level == 1
    assert caution_account.cash == 100.0
    assert sum(caution_weights.values()) <= caution.target_gross_cap + 1e-12
    assert max(caution_weights.values(), default=0.0) <= (
        DEFAULT_CONFIG.tactical_rebound_weight + 1e-12
    )

def test_unconfirmed_recovery_candidates_cannot_create_a_hidden_target_book() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": -0.05,
            "tech_ret60": -0.05,
            "broad_ret120": 0.12,
            "tech_ret120": 0.10,
            "risk_anchor_group_count": 0,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert targets == ()
    assert account.anchor_weights == {}
    assert account.positions == {}
    assert account.pending_orders == []
    assert account.cash == 100.0

@pytest.mark.parametrize("reported_universe_size", (3, 30))
def test_reported_universe_size_cannot_authorize_unqualified_recovery_entry(
    reported_universe_size: int,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
            "configured_user_universe_size": reported_universe_size,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert weights == {}
    assert account.positions == {}
    assert account.pending_orders == []
    assert account.cash == 100.0

def test_ambiguous_recovery_metadata_cannot_authorize_unqualified_deployment() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third", "fourth")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    # Recovery metadata alone cannot create qualified entry or physical capital.
    assert targets == ()
    assert account.cash == account.initial_cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_unfinished_held_buy_retains_only_shared_concentration_capacity() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    account = AccountState(
        initial_cash=100.0,
        cash=38.0,
        positions={
            "lead": Position("lead", shares=30, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value),
            "second": Position(
                "second", shares=16, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
            ),
            "third": Position(
                "third", shares=16, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
            ),
        },
        pending_orders=[
            PendingOrder(
                str(dates[-2].date()),
                "lead",
                "BUY",
                DEFAULT_CONFIG.tactical_rebound_weight,
                "recovery cohort construction",
                Lifecycle.RECOVERY.value,
                remaining_shares=30,
                attempts=1,
            )
        ],
        anchor_weights={"lead": 0.60, "second": 0.16, "third": 0.16},
        recovery_anchor_date=str(dates[-2].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.12,
            "tech_ret120": 0.10,
            "risk_anchor_group_count": 0,
        },
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"lead": 0.43, "second": 0.16, "third": 0.16}
    )
    # The unfinished buy keeps available funding, bounded by the shared 75% cap.
    assert account.anchor_weights == {"lead": 0.60, "second": 0.16, "third": 0.16}
    assert account.cash == 38.0
    assert {symbol: position.shares for symbol, position in account.positions.items()} == {
        "lead": 30, "second": 16, "third": 16
    }
    assert account.pending_orders[0].remaining_shares == 30
    assert account.pending_orders[0].attempts == 1

@pytest.mark.parametrize("restored_after_shock", [False, True])
def test_structural_recovery_exit_survives_freeze_and_prior_shock(
    restored_after_shock: bool,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("trail_me", "keep_a", "keep_b")
    frames = {
        symbol: _trend_frame(dates, close=np.linspace(0.80, price, len(dates)))
        for symbol, price in zip(symbols, (0.88, 0.95, 0.95), strict=True)
    }
    frames["trail_me"]["ma60"] = 1.0
    frames["trail_me"]["ret20"] = -0.20
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "trail_me": Position(
                "trail_me",
                shares=30,
                avg_cost=0.70,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
            "keep_a": Position(
                "keep_a",
                shares=30,
                avg_cost=0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
            "keep_b": Position(
                "keep_b",
                shares=30,
                avg_cost=0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
        },
        anchor_weights={symbol: 0.30 for symbol in symbols},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        last_shock_date=(str(dates[-10].date()) if restored_after_shock else ""),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        5,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.60,
            "held_damage_ratio": 2.0 / 3.0,
            "sector_stress_ratio": 0.80,
        },
        ("confirmed multi-industry damage",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for observed in dates[-DEFAULT_CONFIG.replacement_confirm_days :]:
        targets = allocator.allocate(
            date=observed,
            opportunity=Opportunity.STRONG_TREND,
            risk=caution,
            user_panel=frames,
            leaders={symbol: _leader(symbol, 0.90, mature=symbol != "trail_me") for symbol in symbols},
            account=account,
            prices={symbol: float(frames[symbol].loc[observed, "close"]) for symbol in symbols},
        )

    weights = {target.symbol: target.weight for target in targets}
    assert weights["trail_me"] == 0.0
    assert weights["keep_a"] > 0.0
    assert weights["keep_b"] > 0.0
    assert "trail_me" not in account.anchor_weights
    assert account.positions["trail_me"].shares == 30
    assert account.cash == 10.0
