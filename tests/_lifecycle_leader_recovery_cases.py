from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _leader,
    _normal_risk,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    plan_orders,
)
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Position,
    Risk,
    RiskAssessment,
    Target,
    Tranche,
)


def test_add1_add2_are_live_but_a_generic_satellite_is_not_auto_admitted():
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    frame = _trend_frame(dates)
    leader = _leader("core", 0.82)
    account = AccountState(
        initial_cash=100.0,
        cash=60.0,
        positions={
            "core": Position(
                "core",
                shares=40,
                avg_cost=0.90,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        active_leaders=["core"],
        dynamic_k=1,
        last_k_change_date=str(date.date()),
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    add1 = allocator.allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={"core": frame},
        leaders={"core": leader},
        account=account,
        prices={"core": 1.0},
    )
    add1_target = next(item for item in add1 if item.symbol == "core")
    assert add1_target.lifecycle == Lifecycle.ADD1.value
    assert add1_target.weight == pytest.approx(0.45)

    account.positions["core"].lifecycle = Lifecycle.ADD1.value
    account.positions["core"].tranches = [
        Tranche(
            "add1",
            Lifecycle.ADD1.value,
            40,
            0.90,
            str(dates[-10].date()),
            str(dates[-9].date()),
            1.0,
        )
    ]
    account.lifecycle_events = [
        {
            "date": str(dates[-10].date()),
            "symbol": "core",
            "from": Lifecycle.CORE.value,
            "to": Lifecycle.ADD1.value,
            "shares": 40,
            "reason": "test ADD1",
        }
    ]
    add2 = allocator.allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={"core": frame},
        leaders={"core": leader},
        account=account,
        prices={"core": 1.10},
    )
    add2_target = next(item for item in add2 if item.symbol == "core")
    assert add2_target.lifecycle == Lifecycle.ADD2.value
    assert add2_target.weight > 40 * 1.10 / 104.0

    account.lifecycle_events[0]["date"] = str(dates[-2].date())
    deferred = allocator.allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={"core": frame},
        leaders={"core": leader},
        account=account,
        prices={"core": 1.10},
    )
    deferred_target = next(item for item in deferred if item.symbol == "core")
    assert deferred_target.lifecycle == Lifecycle.CORE.value

    account.lifecycle_events[0]["date"] = str(dates[-10].date())
    chase_risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret5": 0.01, "tech_ret5": 0.07},
        (),
        "NONE",
    )
    chased = allocator.allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=chase_risk,
        user_panel={"core": frame},
        leaders={"core": leader},
        account=account,
        prices={"core": 1.10},
    )
    chased_target = next(item for item in chased if item.symbol == "core")
    assert chased_target.lifecycle == Lifecycle.CORE.value

    satellite_account = AccountState.empty(100.0)
    satellite_account.candidate_tenure["leader_cycle_armed"] = 1
    satellite = allocator.allocate(
        date=date,
        opportunity=Opportunity.TREND,
        risk=_normal_risk(),
        user_panel={"emerging": frame},
        leaders={
            "emerging": _leader(
                "emerging",
                0.80,
                mature=False,
                emerging=True,
                industry="equipment",
            )
        },
        account=satellite_account,
        prices={"emerging": 1.0},
    )
    assert satellite == ()
    assert satellite_account.satellite_entry_dates == {}

def test_effective_n_drives_dynamic_k_and_rotation_records_attribution():
    dates = pd.bdate_range("2025-01-02", periods=150)
    correlated = np.linspace(0.8, 1.0, len(dates))
    panel = {symbol: _trend_frame(dates, close=correlated) for symbol in ("one", "two", "three")}
    leaders = {
        "one": _leader("one", 0.82, industry="optical"),
        "two": _leader("two", 0.80, industry="equipment"),
        "three": _leader("three", 0.78, industry="material"),
    }
    account = AccountState.empty(100.0)
    account.candidate_tenure["leader_cycle_armed"] = 1
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_normal_risk(),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in panel},
    )
    assert account.dynamic_k == 2
    assert sum(item.weight > 0 for item in targets) == 2

    strong = _trend_frame(dates)
    weak = _trend_frame(dates, ma20=2.0, ma60=0.5, ret20=-0.10, ret60=0.10)
    challenger = _trend_frame(dates)
    rotation_panel = {"strong": strong, "weak": weak, "new": challenger}
    rotation_leaders = {
        "strong": _leader("strong", 0.90, industry="optical"),
        "weak": _leader("weak", 0.60, industry="equipment"),
        "new": _leader("new", 0.95, industry="material"),
    }
    rotation_account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=1.0,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
            )
            for symbol in ("strong", "weak")
        },
        active_leaders=["strong", "weak"],
        dynamic_k=2,
        last_k_change_date=str(dates[-3].date()),
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    rotation_targets = ()
    for date in dates[-3:]:
        rotation_targets = allocator.allocate(
            date=date,
            opportunity=Opportunity.TREND,
            risk=_normal_risk(),
            user_panel=rotation_panel,
            leaders=rotation_leaders,
            account=rotation_account,
            prices={symbol: 1.0 for symbol in rotation_panel},
        )
    assert rotation_account.replacement_events
    event = rotation_account.replacement_events[-1]
    assert (event["old_symbol"], event["new_symbol"]) == ("weak", "new")
    replacement = next(item for item in rotation_targets if item.symbol == "new")
    replaced = next(item for item in rotation_targets if item.symbol == "weak")
    assert replacement.weight > 0
    assert replaced.weight == 0
    assert replacement.origin_subsystem == replaced.origin_subsystem == OriginSubsystem.LEADER.value
    assert replacement.mechanism == replaced.mechanism == AttributionMechanism.LEADER_ROTATION.value
    assert replacement.replaces_symbol == "weak"
    assert replaced.replaces_symbol is None

def test_allocator_enforces_risk_cap_on_anchored_early_return():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("core1", "core2", "core3")
    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            symbol: Position(
                symbol,
                shares=shares,
                avg_cost=1.0,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
            )
            for symbol, shares in zip(symbols, (27, 27, 26), strict=True)
        },
        anchor_weights={symbol: weight for symbol, weight in zip(symbols, (0.27, 0.27, 0.26), strict=True)},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(Risk.RISK_OFF, 0.50, 3, {}, (), "NONE")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )
    assert sum(item.weight for item in targets) == pytest.approx(0.50)
    reduced = [item for item in targets if item.weight + 1e-12 < account.anchor_weights[item.symbol]]
    unchanged = [item for item in targets if item not in reduced]
    assert reduced
    assert all(item.reduction_policy == "RISK_PRIORITY" for item in reduced)
    assert all(item.reason_code == "risk_off" for item in reduced)
    assert all(item.exit_kind == "risk_off" for item in reduced)
    assert all("risk-off gross cap" in item.reason for item in reduced)
    assert all(item.reason_code != "risk_off" for item in unchanged)

def test_graduated_recovery_conviction_owner_survives_equal_lifecycle_risk_cut() -> None:
    symbols = ("conviction", "reserve_a", "reserve_b")
    positions = {
        symbol: Position(
            symbol,
            shares=20,
            avg_cost=1.0,
            highest_close=1.0,
            lifecycle=Lifecycle.RECOVERY.value,
            tranches=[
                Tranche(
                    f"{symbol}_recovery",
                    Lifecycle.RECOVERY.value,
                    20,
                    1.0,
                    "2025-01-02",
                    "2025-01-03",
                    1.0,
                    mae=-0.05,
                )
            ],
        )
        for symbol in symbols
    }
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions=positions,
        anchor_weights={symbol: 0.20 for symbol in symbols},
        recovery_conviction_symbol="conviction",
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    targets = tuple(
        Target(
            symbol=symbol,
            weight=0.20,
            lifecycle=Lifecycle.RECOVERY.value,
            reason="strategy target",
            alpha_score=0.50 if symbol == "conviction" else 0.70,
            confidence=0.80,
        )
        for symbol in symbols
    )

    reduced = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={symbol: 0.20 for symbol in symbols},
        account=account,
        gross_cap=0.40,
    )

    weights = {target.symbol: target.weight for target in reduced}
    assert weights["conviction"] == pytest.approx(0.20)
    assert sum(weights.values()) == pytest.approx(0.40)

def test_sector_guard_prefers_the_less_peak_damaged_equal_lifecycle() -> None:
    healthier, damaged = "healthier_core", "damaged_core"

    def core_position(symbol: str) -> Position:
        return Position(
            symbol,
            shares=40,
            avg_cost=0.70,
            highest_close=1.0,
            tranches=[
                Tranche(
                    f"{symbol}_core",
                    Lifecycle.CORE.value,
                    40,
                    0.70,
                    "2025-01-02",
                    "2025-01-03",
                    1.0,
                    mae=-0.05,
                )
            ],
        )

    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            healthier: core_position(healthier),
            damaged: core_position(damaged),
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    targets = (
        Target(healthier, 0.40, Lifecycle.CORE.value, 0.10, 1.0, "hold healthier"),
        Target(damaged, 0.40, Lifecycle.CORE.value, 0.99, 1.0, "hold damaged"),
    )

    reduced = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={healthier: 0.40, damaged: 0.40},
        account=account,
        gross_cap=0.40,
        risk_reason_code="sector_guard",
        prices={healthier: 0.90, damaged: 0.60},
    )

    assert {target.symbol: target.weight for target in reduced} == pytest.approx(
        {healthier: 0.40, damaged: 0.0}
    )

def test_drifted_anchor_actual_gross_cannot_bypass_nominal_risk_cap():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("core1", "core2", "core3")
    account = AccountState(
        initial_cash=2_000_000.0,
        cash=60_000.0,
        positions={
            symbol: Position(
                symbol,
                shares=shares,
                avg_cost=1.0,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
            )
            for symbol, shares in zip(symbols, (1_360_000, 380_000, 200_000), strict=True)
        },
        anchor_weights={symbol: weight for symbol, weight in zip(symbols, (0.60, 0.19, 0.10), strict=True)},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    risk = RiskAssessment(Risk.RISK_OFF, 0.90, 1, {}, (), "NONE")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert sum(target.weight for target in targets) <= 0.90
    core = next(target for target in targets if target.symbol == "core1")
    assert core.weight == pytest.approx(0.60)
    assert "portfolio risk-off gross cap" in core.reason
    assert core.reason_code == "risk_off"
    assert core.exit_kind == "risk_off"
    orders = plan_orders(
        signal_date=str(dates[-1].date()),
        targets=targets,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        cfg=DEFAULT_CONFIG,
    )
    assert [(order.symbol, order.side) for order in orders] == [("core1", "SELL")]

def test_locked_recovery_cohort_scales_missing_members_to_remaining_budget():
    dates = pd.bdate_range("2023-01-03", periods=150)
    frame = _trend_frame(dates)
    symbols = ("held", "missing_lead", "missing_secondary")
    account = AccountState(
        initial_cash=1_000.0,
        cash=485.0,
        positions={
            "held": Position(
                "held",
                shares=515,
                avg_cost=1.0,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        anchor_weights={
            "held": 0.20,
            "missing_lead": 0.60,
            "missing_secondary": 0.12,
        },
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=1_000.0,
        capital_peak=1_000.0,
    )
    risk = RiskAssessment(
        Risk.CAUTION,
        DEFAULT_CONFIG.recovery_target_gross,
        1,
        {},
        (),
        "RECOVERY",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert sum(weights.values()) == pytest.approx(DEFAULT_CONFIG.recovery_target_gross)
    assert weights["held"] == pytest.approx(0.515)
    assert weights["missing_lead"] == pytest.approx(0.3375)
    assert weights["missing_secondary"] == pytest.approx(0.0675)

def test_stale_single_recovery_anchor_graduates_on_confirmed_leader_cycle():
    dates = pd.bdate_range(
        "2023-01-03",
        periods=DEFAULT_CONFIG.recovery_cohort_graduation_days + 10,
    )
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            symbol: Position(
                symbol,
                shares=40,
                avg_cost=0.80,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in ("anchor", "new_core")
        },
        anchor_weights={"anchor": 0.35},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"leader_cycle_armed": 1},
        active_leaders=["anchor", "new_core"],
        dynamic_k=2,
        last_k_change_date=str(dates[-1].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in ("anchor", "new_core")},
        leaders={
            "anchor": _leader("anchor", 0.90, industry="optical"),
            "new_core": _leader("new_core", 0.88, industry="equipment"),
        },
        account=account,
        prices={"anchor": 1.0, "new_core": 1.0},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert account.anchor_weights == {}
    assert account.recovery_anchor_date == ""
    assert account.candidate_tenure["recovery_cohort_graduated"] == 1
    assert weights["anchor"] > 0
    assert weights["new_core"] > 0

def test_fully_exited_recovery_anchors_cannot_hijack_a_later_leader_book():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    held = ("new_compute_leader", "new_equipment_leader")
    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            symbol: Position(
                symbol,
                shares=40,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in held
        },
        # These symbols belonged to an older crash-recovery cohort and have
        # already been fully sold.  They must not remain a hidden target book.
        anchor_weights={"old_optical_anchor": 0.60, "old_pcb_anchor": 0.32},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={
            "leader_cycle_armed": 1,
            "recovery_cohort_locked": 1,
        },
        active_leaders=list(held),
        dynamic_k=2,
        last_k_change_date=str(dates[-1].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        held[0]: _leader(held[0], 0.92, industry="compute"),
        held[1]: _leader(held[1], 0.90, industry="equipment"),
    }

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in held},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in held},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert account.anchor_weights == {}
    assert account.recovery_anchor_date == ""
    assert account.candidate_tenure["recovery_cohort_graduated"] == 1
    assert all(weights[symbol] > 0 for symbol in held)
    assert not any(symbol.startswith("old_") for symbol in weights)

def test_weak_secular_market_allows_early_recovery_cohort_graduation():
    dates = pd.bdate_range(
        "2023-01-03",
        periods=DEFAULT_CONFIG.recovery_cohort_weak_graduation_days + 10,
    )
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            symbol: Position(
                symbol,
                shares=40,
                avg_cost=0.80,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in ("anchor", "new_core")
        },
        anchor_weights={"anchor": 0.35},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"leader_cycle_armed": 1},
        active_leaders=["anchor", "new_core"],
        dynamic_k=2,
        last_k_change_date=str(dates[-1].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    weak_risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.20, "tech_ret120": -0.25},
        (),
        "NONE",
    )

    PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=weak_risk,
        user_panel={symbol: frame for symbol in ("anchor", "new_core")},
        leaders={
            "anchor": _leader("anchor", 0.90, industry="optical"),
            "new_core": _leader("new_core", 0.88, industry="equipment"),
        },
        account=account,
        prices={"anchor": 1.0, "new_core": 1.0},
    )

    assert account.anchor_weights == {}
    assert account.candidate_tenure["recovery_cohort_graduated"] == 1

def test_graduation_day_retains_a_newly_promoted_recovery_book() -> None:
    dates = pd.bdate_range(
        "2023-01-03",
        periods=DEFAULT_CONFIG.recovery_cohort_weak_graduation_days + 10,
    )
    symbols = ("graduating_a", "graduating_b", "graduating_c")
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
            for symbol in symbols
        },
        anchor_weights={symbol: 0.30 for symbol in symbols},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    weak_risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.20, "tech_ret120": -0.25},
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=weak_risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.40) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.candidate_tenure["recovery_cohort_graduated"] == 1
    assert all(target.weight > 0 for target in targets)
