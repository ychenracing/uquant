from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _dynamic_cohort_inputs,
    _leader,
    _normal_risk,
    _strategic_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    LeaderScore,
    Risk,
    RiskAssessment,
)


def test_strategic_cohort_discovers_arbitrary_symbols_without_a_static_prior():
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    expected = {"arbitrary_optical", "arbitrary_compute", "arbitrary_equipment"}

    assert account.strategic_cohort_symbols == []
    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert set(account.strategic_cohort_symbols) == expected
    assert set(account.strategic_cohort_targets) == expected
    assert account.strategic_epoch == 1
    assert account.strategic_candidate_signature.startswith("strategic_qualification:")
    assert all(symbol in account.strategic_candidate_signature for symbol in expected)
    assert sum(account.strategic_cohort_targets.values()) == pytest.approx(DEFAULT_CONFIG.max_gross)
    assert all(weight == pytest.approx(1.0 / 3.0) for weight in account.strategic_cohort_targets.values())

def test_strategic_rank_prefers_a_confirmed_industry_cluster_over_one_high_scoring_outsider():
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    strong = ("optical_a", "optical_b", "optical_c")
    symbols = (*strong, "isolated_compute")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {
        symbol: _leader(
            symbol,
            0.97 - 0.01 * index if symbol in strong else 0.99,
            industry="optical" if symbol in strong else "compute",
        )
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert tuple(account.strategic_cohort_symbols) == strong
    assert "isolated_compute" not in account.strategic_cohort_targets

def test_strategic_established_route_rejects_broken_medium_term_structure():
    dates = pd.bdate_range("2023-01-02", periods=246)
    close = np.concatenate(
        (
            np.linspace(1.0, 5.0, 125),
            np.linspace(5.0, 3.2, 90),
            np.linspace(3.2, 3.7, 31),
        )
    )
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("old_a", "old_b", "old_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {symbol: _leader(symbol, 0.95) for symbol in symbols}
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert close[-1] / close[-121] - 1.0 < 0.0
    assert account.strategic_epoch == 0
    assert account.candidate_tenure["strategic_long_cycle_open"] == 0

def test_strategic_transition_route_needs_no_high_240_day_secular_score():
    dates = pd.bdate_range("2023-01-02", periods=160)
    close = np.concatenate((np.linspace(1.0, 0.85, 39), np.linspace(0.85, 1.45, 121)))
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("emerging_a", "emerging_b", "emerging_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders: dict[str, LeaderScore] = {}
    for index, symbol in enumerate(symbols):
        base = _leader(symbol, 0.90 - 0.01 * index, mature=False, emerging=True)
        leaders[symbol] = LeaderScore(
            symbol=base.symbol,
            score=base.score,
            confidence=base.confidence,
            mature=False,
            emerging=True,
            industry=base.industry,
            components={**base.components, "secular_score": 0.35},
        )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert len(dates) < 241
    assert account.strategic_epoch == 1
    assert tuple(account.strategic_cohort_symbols) == symbols

def test_synchronized_industry_impulse_is_causal_and_signature_order_invariant() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    close = np.full(len(dates), 1.20)
    close[125:205] = np.linspace(1.20, 0.90, 80)
    close[205:] = np.linspace(0.90, 1.08, 41)
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("impulse_a", "impulse_b", "impulse_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    first = {
        symbol: _leader(symbol, 0.61 - 0.01 * index, mature=False, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    second = {
        symbol: _leader(symbol, 0.59 + 0.01 * index, mature=False, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    allocator._initialize_strategic_cohort(
        date=dates[-2],
        user_panel=panel,
        leaders=first,
        account=account,
        risk=_normal_risk(),
    )
    allocator._initialize_strategic_cohort(
        date=dates[-1],
        user_panel=panel,
        leaders=second,
        account=account,
        risk=_normal_risk(),
    )

    assert close[-1] / close[-121] - 1.0 == pytest.approx(-0.10)
    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert set(account.strategic_cohort_symbols) == set(symbols)
    assert account.strategic_candidate_signature.startswith(
        "strategic_qualification:EMERGING_SECULAR:"
    )
    assert "evidence=transition_impulse" in account.strategic_candidate_signature

    unsynchronized = AccountState.empty(100.0)
    mixed = {
        symbol: _leader(
            symbol,
            0.61 - 0.01 * index,
            mature=False,
            industry=("optical", "compute", "equipment")[index],
        )
        for index, symbol in enumerate(symbols)
    }
    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=mixed,
            account=unsynchronized,
            risk=_normal_risk(),
        )
    assert unsynchronized.strategic_epoch == 0

    negative_market = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"tech_ret120": 0.0, "broad_ret20": -0.01, "tech_ret20": 0.02},
        (),
        "NONE",
    )
    for date, leaders in zip(
        dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :],
        (first, second),
        strict=True,
    ):
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=negative_market,
            risk=risk,
        )
    assert negative_market.strategic_epoch == 0

def test_synchronized_impulse_rejects_low_quality_medium_term_rebound() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    close = np.full(len(dates), 1.20)
    close[125:205] = np.linspace(1.20, 0.90, 80)
    close[205:] = np.linspace(0.90, 1.08, 41)
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("weak_impulse_a", "weak_impulse_b", "weak_impulse_c")
    weak_leaders = {}
    for symbol in symbols:
        base = _leader(symbol, 0.10, mature=False, industry="optical")
        weak_leaders[symbol] = LeaderScore(
            symbol=base.symbol,
            score=base.score,
            confidence=base.confidence,
            mature=False,
            emerging=False,
            industry=base.industry,
            components={
                **base.components,
                "secular_score": 0.0,
                "secular_confidence": 0.0,
            },
        )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel={symbol: frame for symbol in symbols},
            leaders=weak_leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert close[-1] / close[-61] - 1.0 < 0.20
    assert close[-1] / close[-121] - 1.0 < 0.0
    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0

def test_established_cohort_rejects_a_broadly_negative_market_rebound() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret20": -0.04,
            "tech_ret20": -0.06,
            "tech_ret120": -0.20,
        },
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_strategic_cohort_defers_while_both_market_legs_remain_in_recovery() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret20": 0.08,
            "tech_ret20": 0.20,
            "broad_ret120": -0.15,
            "tech_ret120": -0.24,
        },
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_full_strategic_cohort_requires_existing_high_confidence_breadth() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "breadth20": DEFAULT_CONFIG.high_confidence_entry_breadth - 0.01,
            "broad_ret20": 0.08,
            "tech_ret20": 0.10,
            "broad_ret120": 0.05,
            "tech_ret120": 0.05,
        },
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_strategic_cohort_rejects_a_broad_index_blowoff() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "breadth20": 0.75,
            "broad_ret20": 0.08,
            "tech_ret20": 0.10,
            "broad_ret120": DEFAULT_CONFIG.strategic_long_cycle_max_tech_ret120 + 0.01,
            "tech_ret120": DEFAULT_CONFIG.strategic_long_cycle_max_tech_ret120 - 0.01,
        },
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_full_strategic_cohort_requires_independent_risk_anchor_coverage() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "breadth20": 0.80,
            "broad_ret20": 0.08,
            "tech_ret20": 0.10,
            "broad_ret120": 0.05,
            "tech_ret120": 0.05,
            "risk_anchor_group_count": 0,
        },
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_absolute_ret240_can_admit_without_a_symbol_specific_prior() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    symbols = ("persistent_a", "persistent_b", "persistent_c")
    leaders = {
        symbol: _leader(symbol, 0.20, industry="independent_optical")
        for symbol in symbols
    }
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        1,
        {
            "tech_ret120": -0.05,
            "risk_anchor_symbols": [],
            "risk_anchor_group_count": 0,
        },
        ("isolated index weakness",),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-3:]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel={symbol: frame for symbol in symbols},
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert account.strategic_epoch == 1
    assert set(account.strategic_cohort_symbols) == set(symbols)
    assert account.strategic_candidate_signature.startswith(
        "strategic_qualification:SECULAR:"
    )
    assert "evidence=persistent_industry" in account.strategic_candidate_signature

def test_persistent_startup_exception_defers_an_overextended_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    close = np.concatenate((np.ones(125), np.linspace(1.0, 4.0, 121)))
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.85
    symbols = ("extended_a", "extended_b", "extended_c")
    leaders = {
        symbol: _leader(symbol, 0.95 - 0.01 * index, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"risk_anchor_symbols": [], "risk_anchor_group_count": 0},
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel={symbol: frame for symbol in symbols},
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert close[-1] / close[-121] - 1.0 > DEFAULT_CONFIG.strategic_persistent_max_ret120
    assert account.strategic_epoch == 0
    assert account.candidate_tenure["strategic_long_cycle_open"] == 0
