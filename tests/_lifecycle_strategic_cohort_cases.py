from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _dynamic_cohort_inputs,
    _leader,
    _normal_risk,
    _strategic_frame,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import StrategicEpochStatus
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
    Target,
)


def _assert_unfilled_strategic_probe(
    account: AccountState,
    *,
    realized_epoch_count: int = 0,
) -> None:
    assert account.strategic_epoch == realized_epoch_count
    assert account.active_strategic_epoch_id == ""
    assert len(account.strategic_epochs) == 1
    assert account.strategic_epochs[-1].realized_status == StrategicEpochStatus.PROBE.value


def test_persistent_industry_outranks_a_shorter_established_group() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    persistent = _strategic_frame(dates)
    shorter = persistent.copy()
    shorter_close = np.linspace(1.0, 1.50, len(dates))
    shorter["close"] = shorter_close
    shorter["ma20"] = shorter_close * 0.95
    shorter["ma60"] = shorter_close * 0.90
    persistent_symbols = ("persistent_a", "persistent_b", "persistent_c")
    shorter_symbols = ("shorter_a", "shorter_b", "shorter_c")
    panel = {
        **{symbol: persistent.copy() for symbol in persistent_symbols},
        **{symbol: shorter.copy() for symbol in shorter_symbols},
    }
    leaders = {
        **{
            symbol: _leader(symbol, 0.20, industry="persistent_group")
            for symbol in persistent_symbols
        },
        **{
            symbol: LeaderScore(
                symbol=symbol,
                score=0.95,
                confidence=0.95,
                mature=True,
                emerging=False,
                industry="shorter_group",
                components={
                    **_leader(symbol, 0.95).components,
                    "short_relative_strength": 0.0,
                    "breakout_quality": 0.0,
                    "acceleration": 0.0,
                    "industry_rotation_strength": 0.0,
                },
            )
            for symbol in shorter_symbols
        },
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "configured_user_universe_size": 10,
            "risk_anchor_symbols": ["sentinel"],
            "risk_anchor_group_count": 3,
            "breadth20": 1.0,
            "broad_ret20": 0.05,
            "tech_ret20": 0.05,
            "broad_ret120": 0.0,
            "tech_ret120": 0.0,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    assert set(account.strategic_cohort_symbols) == set(persistent_symbols)
    assert "evidence=persistent_industry" in account.strategic_candidate_signature

def test_broad_established_group_rejects_weak_median_persistence() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    close = np.linspace(1.0, 1.50, len(dates))
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("broad_a", "broad_b", "broad_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {
        symbol: LeaderScore(
            symbol=symbol,
            score=0.95,
            confidence=0.95,
            mature=True,
            emerging=False,
            industry=f"group_{index}",
            components={
                **_leader(symbol, 0.95).components,
                "short_relative_strength": 0.0,
                "breakout_quality": 0.0,
                "acceleration": 0.0,
                "industry_rotation_strength": 0.0,
            },
        )
        for index, symbol in enumerate(symbols)
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "configured_user_universe_size": 10,
            "risk_anchor_symbols": ["sentinel"],
            "risk_anchor_group_count": 3,
            "breadth20": 1.0,
            "broad_ret20": 0.05,
            "tech_ret20": 0.05,
            "broad_ret120": 0.0,
            "tech_ret120": 0.0,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )

    median_ret240 = float((frame["close"] / frame["close"].shift(240) - 1.0).dropna().median())
    assert median_ret240 < DEFAULT_CONFIG.strategic_established_min_median_ret240
    assert account.strategic_epoch == 0
    assert account.strategic_cohort_targets == {}

def test_synchronized_reversal_is_tagged_as_emerging_secular() -> None:
    dates = pd.bdate_range("2023-01-02", periods=250)
    close = np.concatenate(
        [
            np.linspace(1.0, 0.68, len(dates) - 5),
            np.linspace(0.69, 0.74, 5),
        ]
    )
    frame = _trend_frame(dates, close=close, ma20=0.70, ma60=0.75, ret20=0.01, ret60=-0.08)
    frame["atr"] = 0.02
    symbols = ("reversal_a", "reversal_b", "reversal_c")
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
            "tech_ret120": -0.10,
            "risk_anchor_symbols": [],
            "risk_anchor_group_count": 0,
        },
        ("isolated index weakness",),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-2:]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel={symbol: frame for symbol in symbols},
            leaders=leaders,
            account=account,
            risk=risk,
        )

    _assert_unfilled_strategic_probe(account)
    assert len(account.strategic_cohort_symbols) == 2
    assert sorted(account.strategic_cohort_targets.values()) == pytest.approx([0.34, 0.51])
    assert account.strategic_candidate_signature.startswith(
        "strategic_qualification:EMERGING_SECULAR:"
    )
    assert "evidence=reversal_industry" in account.strategic_candidate_signature

@pytest.mark.parametrize(
    ("configured_universe_size", "irrelevant_count"),
    ((3, 0), (30, 10)),
)
def test_decisive_synchronized_reversal_concentrates_one_dominant_owner(
    configured_universe_size: int,
    irrelevant_count: int,
) -> None:
    dates = pd.bdate_range("2023-01-02", periods=250)

    def reversal_frame(ret60_base: float) -> pd.DataFrame:
        close = np.concatenate(
        [
            np.linspace(1.0, 0.68, len(dates) - 5),
            np.linspace(0.69, 0.74, 5),
        ]
        )
        close[-61:-5] = np.linspace(ret60_base, 0.68, 56)
        frame = _trend_frame(
            dates,
            close=close,
            ma20=0.70,
            ma60=0.72,
            ret20=0.08,
            ret60=0.07,
        )
        frame["atr"] = 0.02
        return frame

    dominant = _leader("dominant", 0.70, industry="independent_optical")
    runner = _leader("runner", 0.60, industry="independent_optical")
    runner.components["trend_persistence"] = 1.0 / 3.0
    reserve = _leader("reserve", 0.20, industry="independent_optical")
    panel = {
        "dominant": reversal_frame(0.69),
        "runner": reversal_frame(0.725),
        "reserve": reversal_frame(0.73),
    }
    irrelevant = {
        f"irrelevant_{index}": _trend_frame(
            dates,
            close=np.linspace(1.0, 1.1, len(dates)),
        )
        for index in range(irrelevant_count)
    }
    panel.update(irrelevant)
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        1,
        {
            "tech_ret120": -0.10,
            "risk_anchor_symbols": [],
            "risk_anchor_group_count": 0,
            "configured_user_universe_size": configured_universe_size,
        },
        ("isolated index weakness",),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-2:]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders={
                "dominant": dominant,
                "runner": runner,
                "reserve": reserve,
                **{
                    symbol: _leader(
                        symbol,
                        0.01,
                        industry=f"irrelevant_industry_{index}",
                    )
                    for index, symbol in enumerate(irrelevant)
                },
            },
            account=account,
            risk=risk,
        )

    assert account.strategic_cohort_symbols == ["dominant"]
    assert account.strategic_cohort_targets == {
        "dominant": pytest.approx(DEFAULT_CONFIG.strategic_dominant_max_weight)
    }
    _assert_unfilled_strategic_probe(account)
    assert account.candidate_tenure["strategic_dominant_epoch"] == 1

def test_ordinary_factor_cohort_still_waits_for_dynamic_anchors_to_arm() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    close = np.linspace(1.0, 1.5, len(dates))
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.85
    symbols = ("industry_a", "industry_b", "industry_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.96 - 0.01 * index, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(100.0)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "breadth20": 0.40,
            "broad_ret20": -0.08,
            "tech_ret20": -0.10,
            "broad_ret120": -0.15,
            "tech_ret120": -0.18,
            "risk_anchor_symbols": [],
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
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

def test_weak_regime_can_admit_the_dynamic_persistent_industry_route() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    symbols = ("weak_sync_a", "weak_sync_b", "weak_sync_c")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.96 - 0.01 * index, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        1,
        {
            "breadth20": 0.40,
            "broad_ret20": -0.08,
            "tech_ret20": -0.10,
            "broad_ret120": -0.15,
            "tech_ret120": -0.18,
            "risk_anchor_symbols": [],
            "risk_anchor_group_count": 0,
        },
        ("isolated market weakness",),
        "NONE",
    )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets: tuple[Target, ...] = ()

    for date in dates[-3:]:
        targets = allocator.allocate(
            date=date,
            opportunity=Opportunity.WEAK,
            risk=risk,
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(frame.loc[date, "close"]) for symbol in symbols},
        )

    _assert_unfilled_strategic_probe(account)
    assert {target.symbol for target in targets if target.weight > 0} == set(symbols)
    assert account.strategic_candidate_signature.startswith(
        "strategic_qualification:SECULAR:"
    )
    assert "evidence=persistent_industry" in account.strategic_candidate_signature

@pytest.mark.parametrize(
    ("member_count", "confirm_days"),
    (
        (
            2,
            DEFAULT_CONFIG.strategic_two_name_confirm_days,
        ),
        (
            1,
            DEFAULT_CONFIG.strategic_one_name_confirm_days,
        ),
    ),
)
def test_ordinary_partial_strategic_cohort_requires_synchronized_evidence(
    member_count: int,
    confirm_days: int,
) -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    selected = tuple(sorted(panel))[:member_count]
    panel = {symbol: panel[symbol] for symbol in selected}
    leaders = {symbol: leaders[symbol] for symbol in selected}
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-confirm_days:-1]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert account.strategic_epoch == 0
    allocator._initialize_strategic_cohort(
        date=dates[-1],
        user_panel=panel,
        leaders=leaders,
        account=account,
        risk=_normal_risk(),
    )

    assert account.strategic_epoch == 0
    assert account.strategic_cohort_symbols == []
    assert account.strategic_cohort_targets == {}

def test_single_name_strategic_cohort_rejects_a_nonexceptional_weak_leg() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    frame = _strategic_frame(dates)
    symbol = "ordinary"
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-(DEFAULT_CONFIG.strategic_cohort_confirm_days + 1) :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, DEFAULT_CONFIG.strategic_one_name_min_score - 0.01)},
            account=account,
            risk=_normal_risk(),
        )

    assert account.strategic_epoch == 0

def test_unqualified_universe_padding_cannot_authorize_a_partial_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    qualified = tuple(sorted(panel))[:2]
    broad_panel = {symbol: panel[symbol] for symbol in qualified}
    broad_leaders = {symbol: leaders[symbol] for symbol in qualified}
    weak_frame = _strategic_frame(dates)
    weak_frame["ret240"] = -0.20
    weak_frame["ret120"] = -0.10
    for index in range(8):
        symbol = f"weak_{index}"
        broad_panel[symbol] = weak_frame.copy()
        broad_leaders[symbol] = _leader(symbol, 0.20, industry=f"weak_group_{index}")
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_two_name_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=broad_panel,
            leaders=broad_leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert account.strategic_epoch == 0
    assert account.strategic_cohort_symbols == []

def test_choppy_observation_can_confirm_but_not_admit_a_strategic_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    date = dates[-1]
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for observed in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days - 1 : -1]:
        targets = allocator.allocate(
            date=observed,
            opportunity=Opportunity.CHOPPY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(frame.loc[observed, "close"]) for symbol, frame in panel.items()},
        )
        assert not any(target.reason_code == "strategic_cohort" for target in targets)
    assert account.strategic_epoch == 0
    assert account.candidate_tenure["strategic_cohort_qualification"] >= 2

    targets = allocator.allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )
    _assert_unfilled_strategic_probe(account)
    assert {target.symbol for target in targets if target.weight > 0} == set(account.strategic_cohort_symbols)

def test_recovery_regime_is_not_preempted_by_new_trailing_secular_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    date = dates[-1]
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}

    for observed in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days - 1 : -1]:
        targets = allocator.allocate(
            date=observed,
            opportunity=Opportunity.RECOVERY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(frame.loc[observed, "close"]) for symbol, frame in panel.items()},
        )
    assert targets == ()
    assert account.strategic_epoch == 0

    for _ in range(DEFAULT_CONFIG.strategic_cohort_confirm_days):
        targets = allocator.allocate(
            date=date,
            opportunity=Opportunity.STRONG_TREND,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
    _assert_unfilled_strategic_probe(account)
    assert sum(target.weight for target in targets) == pytest.approx(DEFAULT_CONFIG.max_gross)

def test_recovery_holding_evidence_precedes_shared_strategic_funding() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=50.0,
        positions={"old_anchor": Position("old_anchor", shares=50, avg_cost=1.0)},
        anchor_weights={"old_anchor": 0.50},
        recovery_anchor_date=str(dates[-40].date()),
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days - 1 : -1]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    # Whole-book funding requires current evidence for the retained holding.
    assert account.strategic_cohort_targets == {}
    assert account.anchor_weights == {"old_anchor": 0.50}
    panel["old_anchor"] = _trend_frame(dates)
    leaders["old_anchor"] = _leader("old_anchor", 0.30, industry="old_group")
    allocator._initialize_strategic_cohort(
        date=dates[-1], user_panel=panel, leaders=leaders, account=account, risk=_normal_risk(),
    )
    _assert_unfilled_strategic_probe(account)
    assert 0.0 < sum(account.strategic_cohort_targets.values()) <= 0.50
    assert "old_anchor" not in account.strategic_cohort_targets
    assert account.anchor_weights == {"old_anchor": 0.50}
    assert account.positions["old_anchor"].shares == 50
    assert account.cash == 50.0

def test_recovery_lock_cannot_veto_funded_strategic_participation() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    panel["old_anchor"] = _trend_frame(dates)
    leaders["old_anchor"] = _leader("old_anchor", 0.30, industry="old_group")
    account = AccountState(
        initial_cash=100.0,
        cash=50.0,
        positions={"old_anchor": Position("old_anchor", shares=50, avg_cost=1.0)},
        anchor_weights={"old_anchor": 0.50},
        recovery_anchor_date=str(dates[-40].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    # A legacy lock flag cannot veto independently funded participation.
    _assert_unfilled_strategic_probe(account)
    assert account.anchor_weights == {"old_anchor": 0.50}
    assert 0.0 < sum(account.strategic_cohort_targets.values()) <= 0.50
    assert "old_anchor" not in account.strategic_cohort_targets
    assert account.positions["old_anchor"].shares == 50
    assert account.cash == 50.0

def test_locked_recovery_cohort_cannot_be_preempted_by_strategic_discovery() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    anchors = ("locked_a", "locked_b", "locked_c")
    account = AccountState(
        initial_cash=100.0,
        cash=8.0,
        positions={
            symbol: Position(
                symbol,
                shares=shares,
                avg_cost=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
            for symbol, shares in zip(anchors, (60, 16, 16), strict=True)
        },
        anchor_weights={
            symbol: weight
            for symbol, weight in zip(anchors, (0.60, 0.16, 0.16), strict=True)
        },
        recovery_anchor_date=str(dates[-1].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for _ in range(DEFAULT_CONFIG.strategic_cohort_confirm_days):
        targets = allocator.allocate(
            date=dates[-1],
            opportunity=Opportunity.STRONG_TREND,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={
                **{symbol: 1.0 for symbol in anchors},
                **{
                    symbol: float(frame.loc[dates[-1], "close"])
                    for symbol, frame in panel.items()
                },
            },
        )

    assert account.strategic_epoch == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert account.anchor_weights == pytest.approx(
        {"locked_a": 0.60, "locked_b": 0.16, "locked_c": 0.16}
    )
    assert {target.symbol for target in targets if target.weight > 0} == set(anchors)

def test_relative_secular_evidence_needs_neither_170_percent_nor_short_cycle_maturity():
    dates = pd.bdate_range("2023-01-02", periods=246)
    close = np.linspace(1.0, 1.50, len(dates))
    frame = _strategic_frame(dates)
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.90
    symbols = ("relative_optical", "relative_compute", "relative_equipment")
    industries = ("optical", "compute", "equipment")
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {
        symbol: _leader(
            symbol,
            0.95 - index * 0.01,
            mature=False,
            industry=industry,
        )
        for index, (symbol, industry) in enumerate(zip(symbols, industries, strict=True))
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

    assert close[-1] / close[-241] - 1.0 < 1.70
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    assert set(account.strategic_cohort_symbols) == set(symbols)

def test_strategic_epoch_respects_risk_gate_without_global_exit_cooldown():
    dates = pd.bdate_range("2023-01-02", periods=290)
    panel, leaders = _dynamic_cohort_inputs(dates)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    unsafe = RiskAssessment(Risk.CAUTION, 1.0, 2, {}, ("two risk votes",), "NONE")
    account = AccountState.empty(100.0)

    allocator._initialize_strategic_cohort(
        date=dates[-45],
        user_panel=panel,
        leaders=leaders,
        account=account,
        risk=unsafe,
    )
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0
    assert account.candidate_tenure["strategic_long_cycle_open"] == 0

    account.strategic_last_exit_date = str(dates[-10].date())
    for date in dates[-3:]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )
    assert account.strategic_epoch == 0
    # Per-candidate confirmation replaces the account-wide exit cooldown.
    # Discovery can reserve an intent, but only a later real fill activates it.
    _assert_unfilled_strategic_probe(account)
    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_strategic_epoch_can_requalify_the_same_members_after_a_fresh_cooldown_streak():
    dates = pd.bdate_range("2023-01-02", periods=290)
    panel, leaders = _dynamic_cohort_inputs(dates)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    old_symbols = ["arbitrary_optical", "arbitrary_compute", "arbitrary_equipment"]
    old_signature = (
        "strategic_qualification:established:arbitrary_compute:compute,"
        "arbitrary_equipment:equipment,arbitrary_optical:optical"
    )
    account = AccountState.empty(100.0)
    account.strategic_epoch = 1
    account.strategic_previous_symbols = list(old_symbols)
    account.strategic_candidate_signature = old_signature
    account.strategic_last_exit_date = str(dates[-50].date())

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )
    _assert_unfilled_strategic_probe(account, realized_epoch_count=1)
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    assert set(account.strategic_cohort_symbols) == set(old_symbols)
    assert account.strategic_candidate_signature == (
        "strategic_qualification:SECULAR:arbitrary_compute:compute,"
        "arbitrary_equipment:equipment,arbitrary_optical:optical:evidence=established"
    )

def test_completed_strategic_owner_blocks_generic_handoff_before_rearm_date():
    dates = pd.bdate_range("2024-06-03", periods=200)
    symbols = ("optical_leader", "compute_leader", "equipment_leader")
    industries = ("optical", "compute", "equipment")
    panel = {symbol: _trend_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.01, industry=industry)
        for index, (symbol, industry) in enumerate(zip(symbols, industries, strict=True))
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.18,
            "tech_ret120": 0.75,
            "trend_health": 0.80,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    account.strategic_epoch = 1
    account.strategic_epochs_completed = 1
    account.strategic_last_exit_date = str(dates[-31].date())
    account.strategic_rearm_date = str(dates[-1].date())
    account.strategic_previous_symbols = list(symbols)
    account.active_leaders = [symbols[1], symbols[2]]
    account.dynamic_k = 2
    account.candidate_tenure["strategic_cohort_completed"] = 1
    account.candidate_tenure["leader_cycle_evidence"] = DEFAULT_CONFIG.leader_cycle_confirm_days - 1

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-2],
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert targets == ()
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 0

def test_legacy_rearm_date_cannot_manufacture_generic_entry():
    dates = pd.bdate_range("2024-06-03", periods=200)
    symbols = ("optical_leader", "compute_leader", "equipment_leader")
    industries = ("optical", "compute", "equipment")
    panel = {symbol: _trend_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.01, industry=industry)
        for index, (symbol, industry) in enumerate(zip(symbols, industries, strict=True))
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.18,
            "tech_ret120": 0.75,
            "trend_health": 0.80,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    account.strategic_epoch = 1
    account.strategic_epochs_completed = 1
    account.strategic_last_exit_date = str(dates[-31].date())
    account.strategic_rearm_date = str(dates[-1].date())
    account.strategic_previous_symbols = list(symbols)
    account.active_leaders = [symbols[1], symbols[2]]
    account.dynamic_k = 2
    account.candidate_tenure["strategic_cohort_completed"] = 1

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    # A legacy rearm date cannot substitute for each entrant's own evidence.
    assert targets == ()
    assert account.strategic_epoch == 1
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_legacy_handoff_marker_cannot_manufacture_generic_entry():
    dates = pd.bdate_range("2024-06-03", periods=200)
    symbols = ("optical_leader", "compute_leader", "equipment_leader")
    industries = ("optical", "compute", "equipment")
    panel = {symbol: _trend_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.01, industry=industry)
        for index, (symbol, industry) in enumerate(zip(symbols, industries, strict=True))
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.18,
            "tech_ret120": 0.75,
            "trend_health": 0.80,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    account.strategic_epoch = 1
    account.strategic_epochs_completed = 1
    account.strategic_last_exit_date = str(dates[-31].date())
    account.strategic_rearm_date = str(dates[-1].date())
    account.strategic_previous_symbols = list(symbols)
    account.active_leaders = [symbols[0]]
    account.dynamic_k = 1
    account.candidate_tenure.update(
        {
            "strategic_cohort_completed": 1,
            "leader_cycle_handoff_epoch": 1,
        }
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert targets == ()
    assert account.strategic_epoch == 1
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_partially_held_strategic_cohort_targets_every_missing_member():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("held_member", "missing_member_a", "missing_member_b")
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={
            symbols[0]: Position(
                symbols[0],
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
            )
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        candidate_tenure={"strategic_cohort_active": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.candidate_tenure.get("strategic_cohort_started", 0) == 0
    assert {target.symbol for target in targets if target.weight > 0} == set(symbols)
