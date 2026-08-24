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


def test_recovery_member_signature_must_persist_before_new_buys() -> None:
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
    assert account.anchor_weights == pytest.approx({"lead": 0.60, "new_a": 0.20})
    assert {target.symbol: target.weight for target in third} == pytest.approx(account.anchor_weights)

def test_three_member_expansion_preserves_the_confirmed_tactical_anchor() -> None:
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

    expected = {"lead": 0.60, "new_a": 0.16, "new_b": 0.16}
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected)
    assert account.anchor_weights == pytest.approx(expected)
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

def test_three_confirmed_recovery_members_share_the_full_locked_budget() -> None:
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

    # Exactly three confirmed members fill all available seats without
    # selection ambiguity while preserving the crash winner's conviction ratio.
    gross = min(DEFAULT_CONFIG.max_gross, risk.target_gross_cap)
    lead = min(
        DEFAULT_CONFIG.max_symbol_weight,
        DEFAULT_CONFIG.tactical_rebound_weight,
        gross,
    )
    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {
            symbols[0]: lead,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )
    assert account.anchor_weights == pytest.approx(
        {
            symbols[0]: lead,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

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
        {
            symbols[0]: 1.0 / 3.0,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )

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
    assert sum(caution_weights.values()) <= caution.target_gross_cap + 1e-12
    assert max(caution_weights.values(), default=0.0) <= (
        DEFAULT_CONFIG.tactical_rebound_weight + 1e-12
    )

def test_unconfirmed_simultaneous_recovery_members_keep_one_tactical_owner() -> None:
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

    expected = {
        "deepest": DEFAULT_CONFIG.tactical_rebound_weight,
        "second": 0.16,
        "third": 0.16,
    }
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected)
    assert account.anchor_weights == pytest.approx(expected)
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

@pytest.mark.parametrize("reported_universe_size", (3, 30))
def test_recovery_cohort_size_ignores_unrelated_universe_members(
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
    assert sum(weights.values()) == pytest.approx(DEFAULT_CONFIG.max_gross)
    assert weights[symbols[0]] > weights[symbols[1]] == pytest.approx(weights[symbols[2]])

def test_ambiguous_recovery_candidates_bound_the_first_deployment() -> None:
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=AccountState.empty(100.0),
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert len(targets) == 3
    assert sum(target.weight for target in targets) == pytest.approx(
        DEFAULT_CONFIG.recovery_expansive_universe_gross
    )

def test_locked_recovery_cohort_keeps_an_unfinished_owner_buy_target() -> None:
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
        account.anchor_weights
    )

@pytest.mark.parametrize("restored_after_shock", [False, True])
def test_confirmed_caution_can_execute_an_armed_recovery_winner_trail(
    restored_after_shock: bool,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("trail_me", "keep_a", "keep_b")
    frames = {
        symbol: _trend_frame(dates, close=np.linspace(0.80, price, len(dates)))
        for symbol, price in zip(symbols, (0.88, 0.95, 0.95), strict=True)
    }
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=caution,
        user_panel=frames,
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert (weights["trail_me"] == 0.0) is (not restored_after_shock)
    assert weights["keep_a"] > 0.0
    assert weights["keep_b"] > 0.0
    assert ("trail_me" not in account.anchor_weights) is (not restored_after_shock)
