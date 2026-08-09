from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from unified_ai_quant.config import DEFAULT_CONFIG
from unified_ai_quant.engine import attribution
from unified_ai_quant.execution import plan_orders
from unified_ai_quant.leader import credible_recovery_reserve
from unified_ai_quant.portfolio import PortfolioAllocator
from unified_ai_quant.risk import (
    REFERENCE_ANCHORS,
    _persistent_crisis_cap,
    _portfolio_drawdowns,
    assess_risk,
)
from unified_ai_quant.types import (
    AccountState,
    Fill,
    LeaderScore,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
    Tranche,
)


def _trend_frame(
    dates: pd.DatetimeIndex,
    *,
    close: np.ndarray | None = None,
    ma20: float = 0.9,
    ma60: float = 0.8,
    ret20: float = 0.20,
    ret60: float = 0.40,
) -> pd.DataFrame:
    values = np.asarray(close if close is not None else np.linspace(0.8, 1.0, len(dates)))
    return pd.DataFrame(
        {
            "close": values,
            "ma20": ma20,
            "ma60": ma60,
            "ret20": ret20,
            "ret60": ret60,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )


def _leader(
    symbol: str,
    score: float,
    *,
    mature: bool = True,
    emerging: bool = False,
    industry: str = "optical",
) -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=0.95,
        mature=mature,
        emerging=emerging,
        industry=industry,
        components={},
    )


def _normal_risk() -> RiskAssessment:
    return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")


def _strategic_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    close = np.linspace(1.0, 3.0, len(dates))
    return pd.DataFrame(
        {
            "close": close,
            "ma20": close * 0.95,
            "ma60": close * 0.85,
            "ret20": 0.20,
            "ret60": 0.50,
            "atr": 0.05,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )


def test_operating_drawdown_resets_flat_without_erasing_capital_drawdown():
    account = AccountState(
        initial_cash=100.0,
        cash=90.0,
        positions={"held": Position("held", shares=1, avg_cost=100.0)},
        operating_peak=120.0,
        capital_peak=120.0,
    )

    operating, capital = _portfolio_drawdowns(account, 90.0)
    assert operating == pytest.approx(0.25)
    assert capital == pytest.approx(0.25)

    account.positions.clear()
    operating, capital = _portfolio_drawdowns(account, 90.0)
    assert operating == pytest.approx(0.0)
    assert capital == pytest.approx(0.25)
    assert account.operating_peak == pytest.approx(90.0)
    assert account.capital_peak == pytest.approx(120.0)

    account.positions["new"] = Position("new", shares=1, avg_cost=95.0)
    operating, capital = _portfolio_drawdowns(account, 95.0)
    assert operating == pytest.approx(0.0)
    assert capital == pytest.approx(1.0 - 95.0 / 120.0)


def test_persistent_crisis_cap_preserves_each_route_semantics():
    assert _persistent_crisis_cap(
        "INCOMPLETE_UNIVERSE", DEFAULT_CONFIG, strategic_active=False
    ) == pytest.approx(DEFAULT_CONFIG.incomplete_universe_crisis_gross)
    assert _persistent_crisis_cap(
        "INCOMPLETE_UNIVERSE_UNBACKED", DEFAULT_CONFIG, strategic_active=False
    ) == pytest.approx(0.0)
    assert _persistent_crisis_cap(
        "CONCENTRATED", DEFAULT_CONFIG, strategic_active=False
    ) == pytest.approx(DEFAULT_CONFIG.concentrated_crisis_gross)
    assert _persistent_crisis_cap(
        "MARKET", DEFAULT_CONFIG, strategic_active=False
    ) == pytest.approx(DEFAULT_CONFIG.crisis_gross)
    assert _persistent_crisis_cap(
        "SEVERE", DEFAULT_CONFIG, strategic_active=True
    ) == pytest.approx(DEFAULT_CONFIG.strategic_cohort_crisis_gross)


def test_confirmed_recovery_pair_uses_full_gross_with_bounded_lead():
    account = AccountState.empty(100.0)
    account.anchor_weights = {"lead": 0.60, "reserve": 0.32}
    account.candidate_tenure["confirmed_anchor_pair"] = 1

    proposed, changed = PortfolioAllocator(DEFAULT_CONFIG)._cap_underdiversified(
        dict(account.anchor_weights), account
    )

    assert changed is True
    assert proposed == pytest.approx({"lead": 0.60, "reserve": 0.40})
    assert sum(proposed.values()) == pytest.approx(DEFAULT_CONFIG.max_gross)
    assert account.candidate_tenure["confirmed_pair_balanced"] == 1


def test_recovery_reserve_requires_causal_strength_and_independent_industry():
    dates = pd.bdate_range("2025-01-02", periods=DEFAULT_CONFIG.min_history)
    frame = _trend_frame(dates)
    frame["ret120"] = 0.16
    score = _leader("reserve", 0.59, industry="equipment")

    assert credible_recovery_reserve(
        score=score,
        frame=frame,
        date=dates[-1],
        occupied_industries={"optical"},
        cfg=DEFAULT_CONFIG,
    )
    assert not credible_recovery_reserve(
        score=score,
        frame=frame,
        date=dates[-1],
        occupied_industries={"equipment"},
        cfg=DEFAULT_CONFIG,
    )
    assert not credible_recovery_reserve(
        score=_leader("reserve", 0.57, industry="equipment"),
        frame=frame,
        date=dates[-1],
        occupied_industries={"optical"},
        cfg=DEFAULT_CONFIG,
    )


def test_config_rejects_overlapping_or_invalid_recovery_reserves():
    with pytest.raises(ValueError, match="two unique non-core"):
        DEFAULT_CONFIG.override(
            strategic_reserve_symbols=(
                DEFAULT_CONFIG.strategic_cohort_symbols[0],
                "sh688008",
            )
        )
    with pytest.raises(ValueError, match="unbacked universe tail"):
        DEFAULT_CONFIG.override(
            unbacked_universe_tail_dd=DEFAULT_CONFIG.operating_dd_caution
        )


def test_strategic_cohort_is_fixed_causal_and_one_target_per_symbol():
    dates = pd.bdate_range("2023-01-02", periods=243)
    symbols = DEFAULT_CONFIG.strategic_cohort_symbols
    frame = _strategic_frame(dates)
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {symbol: _leader(symbol, 0.90) for symbol in symbols}
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets = ()
    for decision_date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        targets = allocator.allocate(
            date=decision_date,
            opportunity=Opportunity.CHOPPY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={
                symbol: float(frame.loc[decision_date, "close"])
                for symbol in symbols
            },
        )
    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert account.strategic_cohort_symbols == list(symbols)
    assert {target.symbol for target in targets} == set(symbols)
    assert sum(target.weight for target in targets) == pytest.approx(1.0)
    assert len({target.symbol for target in targets}) == len(targets)

    extra = "unrelated"
    second_account = AccountState.empty(100.0)
    second = ()
    for decision_date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        second = PortfolioAllocator(DEFAULT_CONFIG).allocate(
            date=decision_date,
            opportunity=Opportunity.CHOPPY,
            risk=_normal_risk(),
            user_panel={**panel, extra: frame.copy()},
            leaders={**leaders, extra: _leader(extra, 0.99)},
            account=second_account,
            prices={
                **{
                    symbol: float(frame.loc[decision_date, "close"])
                    for symbol in symbols
                },
                extra: float(frame.loc[decision_date, "close"]),
            },
        )
    assert [(item.symbol, item.weight) for item in second] == [
        (item.symbol, item.weight) for item in targets
    ]


def test_strategic_cohort_waits_for_independent_risk_disagreement_to_clear():
    dates = pd.bdate_range("2023-01-02", periods=246)
    symbols = DEFAULT_CONFIG.strategic_cohort_symbols
    frame = _strategic_frame(dates)
    panel = {symbol: frame.copy() for symbol in symbols}
    leaders = {symbol: _leader(symbol, 0.90) for symbol in symbols}
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    unsafe_caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        2,
        {},
        ("breadth and leader risk disagree with entry",),
        "NONE",
    )

    for decision_date in dates[-6:-3]:
        targets = allocator.allocate(
            date=decision_date,
            opportunity=Opportunity.CHOPPY,
            risk=unsafe_caution,
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={
                symbol: float(frame.loc[decision_date, "close"])
                for symbol in symbols
            },
        )
        assert not any(item.weight > 0 for item in targets)
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0

    # The strategic gate must not consume or rewrite the causal initialization
    # path for stress universes that do not contain the complete fixed cohort.
    incomplete = AccountState.empty(100.0)
    allocator._initialize_strategic_cohort(
        date=dates[-3],
        user_panel={symbols[0]: frame.copy()},
        account=incomplete,
        risk=unsafe_caution,
    )
    assert incomplete.candidate_tenure["strategic_long_cycle_initial_check"] == 1
    assert incomplete.candidate_tenure["strategic_long_cycle_open"] == 0
    assert incomplete.candidate_tenure["strategic_cohort_qualification"] == 0

    benign_caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {},
        ("single residual risk vote",),
        "NONE",
    )
    for decision_date in dates[-3:]:
        targets = allocator.allocate(
            date=decision_date,
            opportunity=Opportunity.CHOPPY,
            risk=benign_caution,
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={
                symbol: float(frame.loc[decision_date, "close"])
                for symbol in symbols
            },
        )
    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert {item.symbol for item in targets if item.weight > 0} == set(symbols)


def test_failed_initial_long_cycle_check_cannot_retroactively_activate():
    dates = pd.bdate_range("2023-01-02", periods=245)
    symbols = DEFAULT_CONFIG.strategic_cohort_symbols
    weak = _strategic_frame(dates)
    weak["close"] = np.linspace(1.0, 1.5, len(dates))
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets = allocator.allocate(
        date=dates[-4],
        opportunity=Opportunity.WEAK,
        risk=_normal_risk(),
        user_panel={symbol: weak for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.60, mature=False) for symbol in symbols},
        account=account,
        prices={symbol: 1.5 for symbol in symbols},
    )
    assert account.candidate_tenure["strategic_long_cycle_open"] == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert not any(item.weight > 0 for item in targets)

    strong = _strategic_frame(dates)
    second = ()
    for date in dates[-3:]:
        second = allocator.allocate(
            date=date,
            opportunity=Opportunity.WEAK,
            risk=_normal_risk(),
            user_panel={symbol: strong for symbol in symbols},
            leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
            account=account,
            prices={symbol: float(strong.loc[date, "close"]) for symbol in symbols},
        )
    assert account.candidate_tenure.get("strategic_cohort_evaluated", 0) == 0
    assert account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    assert not any(item.weight > 0 for item in second)


def test_long_cycle_uses_persistent_return_evidence_not_one_day_threshold():
    dates = pd.bdate_range("2023-01-02", periods=245)
    symbols = DEFAULT_CONFIG.strategic_cohort_symbols
    frame = _strategic_frame(dates)
    frame.loc[dates[:5], "close"] = 1.0
    frame.loc[dates[-5:], "close"] = (2.95, 2.92, 2.91, 2.80, 2.91)
    panel = {symbol: frame.copy() for symbol in symbols}
    cfg = DEFAULT_CONFIG.override(strategic_cohort_min_ret240=1.87)
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(cfg)
    targets = ()

    for decision_date in dates[-3:]:
        targets = allocator.allocate(
            date=decision_date,
            opportunity=Opportunity.CHOPPY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
            account=account,
            prices={symbol: float(frame.loc[decision_date, "close"]) for symbol in symbols},
        )

    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert {item.symbol for item in targets if item.weight > 0} == set(symbols)


def test_synchronized_reversal_uses_two_day_confirmation_and_top_pair():
    dates = pd.bdate_range("2022-01-03", periods=245)
    symbols = DEFAULT_CONFIG.strategic_cohort_symbols
    endpoints = dict(zip(symbols, (1.00, 0.96, 0.92), strict=True))
    panel = {}
    for symbol, endpoint in endpoints.items():
        close = np.concatenate(
            (
                np.linspace(2.0, 0.80, len(dates) - 6),
                np.linspace(0.80, endpoint, 6),
            )
        )
        panel[symbol] = _trend_frame(dates, close=close)
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.10, "tech_ret120": -0.02},
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    first = allocator.allocate(
        date=dates[-2],
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel=panel,
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: float(panel[symbol].loc[dates[-2], "close"]) for symbol in symbols},
    )
    assert account.candidate_tenure["strategic_cohort_qualification"] == 1
    assert not any(item.weight > 0 for item in first)

    second = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel=panel,
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: endpoints[symbol] for symbol in symbols},
    )
    positive = {item.symbol: item.weight for item in second if item.weight > 0}
    assert account.candidate_tenure["strategic_reversal_entry"] == 1
    assert positive == pytest.approx({symbols[0]: 0.60, symbols[1]: 0.40})


def test_completed_strategic_cycle_can_rearm_without_weak_market_alignment():
    leaders = {
        "one": _leader("one", 0.90),
        "two": _leader("two", 0.88, industry="equipment"),
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.10, "tech_ret120": -0.10},
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for _ in range(DEFAULT_CONFIG.leader_cycle_confirm_days):
        assert not allocator._update_leader_cycle_arm(
            opportunity=Opportunity.STRONG_TREND,
            risk=risk,
            leaders=leaders,
            account=account,
        )

    account.candidate_tenure["strategic_cohort_completed"] = 1
    armed = False
    for _ in range(DEFAULT_CONFIG.leader_cycle_confirm_days):
        armed = allocator._update_leader_cycle_arm(
            opportunity=Opportunity.STRONG_TREND,
            risk=risk,
            leaders=leaders,
            account=account,
        )
    assert armed is True


def test_add1_add2_and_satellite_are_live_allocator_states():
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
    assert len(satellite) == 1
    assert satellite[0].lifecycle == Lifecycle.SATELLITE.value
    assert satellite[0].weight == pytest.approx(DEFAULT_CONFIG.satellite_weight)


def test_effective_n_drives_dynamic_k_and_rotation_records_attribution():
    dates = pd.bdate_range("2025-01-02", periods=150)
    correlated = np.linspace(0.8, 1.0, len(dates))
    panel = {
        symbol: _trend_frame(dates, close=correlated)
        for symbol in ("one", "two", "three")
    }
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
    assert next(item for item in rotation_targets if item.symbol == "new").weight > 0
    assert next(item for item in rotation_targets if item.symbol == "weak").weight == 0


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
    assert all("risk gross cap" in item.reason for item in targets)


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
            for symbol, shares in zip(
                symbols, (1_360_000, 380_000, 200_000), strict=True
            )
        },
        anchor_weights={
            symbol: weight
            for symbol, weight in zip(symbols, (0.60, 0.19, 0.10), strict=True)
        },
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
    assert "portfolio risk gross cap" in core.reason
    orders = plan_orders(
        signal_date=str(dates[-1].date()),
        targets=targets,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        cfg=DEFAULT_CONFIG,
    )
    assert [(order.symbol, order.side) for order in orders] == [("core1", "SELL")]


def test_locked_recovery_cohort_scales_missing_members_to_remaining_budget():
    dates = pd.bdate_range("2022-01-03", periods=150)
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
    assert sum(weights.values()) == pytest.approx(
        DEFAULT_CONFIG.recovery_target_gross
    )
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


def _risk_frame(
    dates: pd.DatetimeIndex,
    *,
    close: float,
    ma20: float,
    ret5: float,
) -> pd.DataFrame:
    trend = np.linspace(100.0, close, len(dates))
    return pd.DataFrame(
        {
            "close": trend,
            "ma20": ma20,
            "ma60": ma20 * 1.05,
            "ret5": ret5,
            "ret10": ret5,
            "ret20": ret5,
            "ret60": ret5,
        },
        index=dates,
    )


def test_risk_uses_reference_anchors_and_any_held_tail_break():
    assert REFERENCE_ANCHORS == ("sz300308", "sz300394", "sz300502")
    dates = pd.bdate_range("2025-10-01", periods=80)
    date = dates[-1]
    reference_panel = {
        symbol: _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10)
        for symbol in REFERENCE_ANCHORS
    }
    user_panel = {
        "damaged": _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10),
        "healthy": _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.01),
    }
    leaders = {
        symbol: _leader(symbol, 0.80) for symbol in reference_panel
    }
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={
            "damaged": Position("damaged", shares=1, avg_cost=100.0),
            "healthy": Position("healthy", shares=1, avg_cost=100.0),
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    assessment = assess_risk(
        date=date,
        broad=_risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10),
        tech=_risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10),
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=80.0,
        cfg=DEFAULT_CONFIG,
    )
    assert assessment.state is Risk.CRISIS
    assert assessment.evidence["held_damage_ratio"] == pytest.approx(0.5)
    assert "confirmed concentrated leader break" in assessment.reasons

    disabled = assess_risk(
        date=date,
        broad=_risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10),
        tech=_risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10),
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel=user_panel,
        leaders=leaders,
        account=AccountState.empty(100.0),
        equity=80.0,
        cfg=DEFAULT_CONFIG.override(risk_overlay_enabled=False),
    )
    assert disabled.state is Risk.NORMAL
    assert disabled.evidence["counterfactual_risk_overlay_disabled"] is True


def test_confirmed_caution_reduces_gross_before_account_drawdown():
    dates = pd.bdate_range("2025-10-01", periods=80)
    date = dates[-1]
    damaged = _risk_frame(dates, close=85.0, ma20=100.0, ret5=-0.08)
    account = AccountState.empty(100.0)
    account.risk_streaks["risk_caution"] = DEFAULT_CONFIG.caution_confirm_days - 1

    assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel={symbol: damaged for symbol in REFERENCE_ANCHORS},
        reference_returns=None,
        user_panel={},
        leaders={symbol: _leader(symbol, 0.80) for symbol in REFERENCE_ANCHORS},
        account=account,
        equity=100.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.CAUTION
    assert assessment.votes >= DEFAULT_CONFIG.caution_gross_min_votes
    assert assessment.target_gross_cap == pytest.approx(DEFAULT_CONFIG.caution_gross)

    healthy = _risk_frame(dates, close=85.0, ma20=80.0, ret5=0.01)
    invested = AccountState(
        initial_cash=100.0,
        cash=15.0,
        positions={"held": Position("held", shares=1, avg_cost=80.0)},
        risk_streaks={
            "risk_caution": DEFAULT_CONFIG.caution_confirm_days - 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    preserved = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel={symbol: damaged for symbol in REFERENCE_ANCHORS},
        reference_returns=None,
        user_panel={"held": healthy},
        leaders={symbol: _leader(symbol, 0.80) for symbol in REFERENCE_ANCHORS},
        account=invested,
        equity=100.0,
        cfg=DEFAULT_CONFIG,
    )

    assert preserved.state is Risk.CAUTION
    assert preserved.target_gross_cap == pytest.approx(0.85)


def test_strategic_cohort_risk_preserves_mature_core_but_caps_confirmed_break():
    dates = pd.bdate_range("2025-10-01", periods=80)
    date = dates[-1]
    damaged = _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10)
    reference_panel = {
        symbol: damaged.copy() for symbol in REFERENCE_ANCHORS
    }
    leaders = {symbol: _leader(symbol, 0.80) for symbol in reference_panel}

    def run(account: AccountState, equity: float):
        return assess_risk(
            date=date,
            broad=damaged,
            tech=damaged,
            reference_panel=reference_panel,
            reference_returns=None,
            user_panel={"damaged": damaged},
            leaders=leaders,
            account=account,
            equity=equity,
            cfg=DEFAULT_CONFIG,
        )

    preserved = AccountState(
        initial_cash=100.0,
        cash=5.0,
        positions={"damaged": Position("damaged", shares=1, avg_cost=100.0)},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_days": 30,
            "strategic_profit_armed": 0,
        },
        risk_streaks={"risk_risk_off": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    preserve_assessment = run(preserved, 85.0)
    assert preserve_assessment.state is Risk.RISK_OFF
    assert preserve_assessment.target_gross_cap == pytest.approx(1.0)
    assert not preserved.protected_weights

    guarded = AccountState(
        initial_cash=100.0,
        cash=5.0,
        positions={"damaged": Position("damaged", shares=1, avg_cost=100.0)},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_days": DEFAULT_CONFIG.strategic_cohort_guard_days,
            "strategic_profit_armed": 1,
        },
        risk_streaks={"concentrated_break": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    guard_assessment = run(guarded, 85.0)
    assert guard_assessment.state is Risk.CRISIS
    assert guard_assessment.target_gross_cap == pytest.approx(
        DEFAULT_CONFIG.strategic_cohort_crisis_gross
    )

    tail = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={"damaged": Position("damaged", shares=1, avg_cost=100.0)},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_days": 30,
            "strategic_profit_armed": 0,
        },
        risk_streaks={
            "strategic_tail_break": (
                DEFAULT_CONFIG.strategic_cohort_tail_confirm_days - 1
            )
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    tail_assessment = run(tail, 80.0)
    assert tail_assessment.state is Risk.CRISIS
    assert tail_assessment.target_gross_cap == pytest.approx(
        DEFAULT_CONFIG.strategic_cohort_crisis_gross
    )


def test_narrow_market_two_of_three_anchor_damage_applies_graded_guard():
    dates = pd.bdate_range("2026-01-02", periods=160)
    date = dates[-1]
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    damaged = _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10)
    broad = healthy.copy()
    tech = healthy.copy()
    broad["ret120"] = 0.05
    tech["ret120"] = 0.65
    reference_panel = {
        symbol: healthy.copy() for symbol in REFERENCE_ANCHORS
    }
    held_symbols = ("held1", "held2", "held3")
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            symbol: Position(symbol, shares=1, avg_cost=100.0)
            for symbol in held_symbols
        },
        anchor_weights={symbol: 0.30 for symbol in held_symbols},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    assessment = assess_risk(
        date=date,
        broad=broad,
        tech=tech,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in held_symbols},
        leaders={symbol: _leader(symbol, 0.80) for symbol in reference_panel},
        account=account,
        equity=90.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.RISK_OFF
    assert assessment.target_gross_cap == pytest.approx(
        DEFAULT_CONFIG.narrow_anchor_guard_gross
    )
    assert "narrow-market concentrated anchor damage" in assessment.reasons


def test_lifecycle_and_reason_attribution_reconciles_realized_fills():
    fills = [
        Fill(
            signal_date="2025-01-02",
            fill_date="2025-01-03",
            symbol="sz300308",
            side="BUY",
            shares=100,
            price=10.0,
            gross_value=1_000.0,
            commission=5.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="confirmed mature leader core",
            lifecycle="CORE",
        ),
        Fill(
            signal_date="2025-02-02",
            fill_date="2025-02-03",
            symbol="sz300308",
            side="SELL",
            shares=100,
            price=12.0,
            gross_value=1_200.0,
            commission=5.0,
            stamp_duty=1.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="rotation exit: replacement confirmed",
            lifecycle="CORE",
        ),
    ]
    result = attribution(fills)
    assert result["by_lifecycle"]["core"]["realized_pnl"] == pytest.approx(189.0)
    assert result["by_reason"]["rotation"]["fills"] == 1
    assert result["open_shares_by_lifecycle"]["core"] == 0
