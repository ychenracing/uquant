# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

from _recovery_restore_completion_cases import _restore_panel

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine, _attach_target_attribution
from uquant.execution import plan_orders
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

PRIMARY = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")


def _tactical_frame(*, ret20: float, ret120: float) -> pd.DataFrame:
    dates = pd.bdate_range("2025-07-01", periods=130)
    close = [1.0] * (len(dates) - 1) + [0.94]
    return pd.DataFrame(
        {
            "close": close,
            "ma120": 0.90,
            "ret5": -0.05,
            "ret20": ret20,
            "ret120": ret120,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )


def _tactical_targets(
    *,
    ret20: float,
    ret120: float,
    broad_ret120: float,
    tech_ret120: float,
) -> tuple[tuple[Target, ...], AccountState]:
    symbol = "deep_candidate"
    panel = _restore_panel([symbol])
    frame = panel[symbol]
    # Qualification reads trailing prices, so the path itself carries the signal.
    knots = [0, len(frame) - 121, len(frame) - 21, len(frame) - 1]
    levels = [1 / (1 + ret120), 1 / (1 + ret120), 1 / (1 + ret20), 1.0]
    close = np.exp(np.interp(np.arange(len(frame)), knots, np.log(levels)))
    frame["close"] = close
    frame["ma20"], frame["ma60"], frame["ma120"] = close * .99, close * .97, close * .95
    frame["ret20"], frame["ret120"] = ret20, ret120
    account = AccountState.empty(2_000_000.0)
    risk = RiskAssessment(Risk.NORMAL, 1.0, 0,
        {"broad_ret120": broad_ret120, "tech_ret120": tech_ret120}, (), "NONE")
    components = {key: .95 for key in (
        "secular_confidence", "industry_inference_confidence", "momentum60", "momentum120",
        "relative_strength", "short_relative_strength", "trend_persistence", "breakout_quality",
        "acceleration", "industry_rotation_strength",
    )}
    components.update(unknown_industry=0.0, secular_score=.75)
    leaders = {symbol: LeaderScore(symbol, .85, .95, True, False, "independent", components)}
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for observed in frame.index[-DEFAULT_CONFIG.leader_tenure_days:]:
        targets = allocator.allocate(
            date=observed, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
            leaders=leaders, account=account, prices={symbol: float(frame.loc[observed, "close"])},
        )
    return targets, account


def test_transitional_recovery_admits_only_promotable_deep_crash_candidate():
    targets, account = _tactical_targets(
        ret20=0.10,
        ret120=0.50,
        broad_ret120=-0.10,
        tech_ret120=0.04,
    )

    assert [(target.symbol, target.lifecycle) for target in targets] == [
        ("deep_candidate", Lifecycle.CORE.value)
    ]
    assert targets[0].weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert account.strategic_grant is None
    assert account.tactical_anchor_symbol == ""


def test_transitional_recovery_rejects_ordinary_rebound_candidate():
    targets, account = _tactical_targets(
        ret20=-0.20,
        ret120=-0.10,
        broad_ret120=-0.10,
        tech_ret120=0.04,
    )

    assert targets == ()
    assert account.candidate_tenure.get("tactical_active", 0) == 0
    assert account.tactical_anchor_symbol == ""


def _confirmed_legacy_exit(*, frozen: bool):
    symbol = "deep_candidate"
    frame = _tactical_frame(ret20=-.20, ret120=-.40)
    frame["ma20"] = frame["ma60"] = 1.0
    frame.loc[frame.index[-3:], "close"] = .94
    account = AccountState(
        initial_cash=100.0, cash=40.0,
        positions={symbol: Position(symbol, 60, .70, str(frame.index[0].date()),
                                    1.0, lifecycle=Lifecycle.RECOVERY.value)},
        tactical_anchor_symbol=symbol, protected_weights={symbol: .60},
        strategic_restore_weights={symbol: .60},
        candidate_tenure={"tactical_active": 1, "tactical_promotable": 0},
        operating_peak=120.0, capital_peak=125.0, capital_budget_level=1,
    )
    evidence = {"base_freeze_new_risk": False, "sentinel_freeze_new_risk": frozen,
                "freeze_new_risk": frozen}
    risk = RiskAssessment(Risk.NORMAL, 1.0, 0, evidence, (), "NONE", freeze_new_risk=frozen)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for index, observed in enumerate(frame.index[-3:]):
        targets = allocator.allocate(
            date=observed, opportunity=Opportunity.CHOPPY, risk=risk,
            user_panel={symbol: frame},
            leaders={symbol: LeaderScore(symbol, .5, .95, False, False, "independent", {})},
            account=account, prices={symbol: .94},
        )
        if index < 2:
            assert targets[0].weight > 0
            assert symbol in account.protected_weights
    assert {target.symbol: target.weight for target in targets} == {symbol: 0.0}
    assert targets[0].mechanism == "LEADER_LIFECYCLE_EXIT"
    assert symbol not in account.protected_weights
    assert symbol not in account.strategic_restore_weights
    assert account.tactical_anchor_symbol == ""
    assert account.candidate_tenure["tactical_active"] == 0
    assert account.positions[symbol].shares == 60
    assert account.cash == 40.0
    assert (account.operating_peak, account.capital_peak, account.capital_budget_level) == (120.0, 125.0, 1)
    return account


def test_final_tactical_exit_retires_stale_restore_owner() -> None:
    _confirmed_legacy_exit(frozen=False)


def test_sentinel_freeze_preserves_real_tactical_exit_completion_state() -> None:
    account = _confirmed_legacy_exit(frozen=True)
    assert "tactical_cooldown" not in account.candidate_tenure
    assert "recovery_cycle_rearm_pending" not in account.candidate_tenure


def test_sentinel_freeze_preserves_real_final_strategic_completion_state() -> None:
    symbol = "completed_member"
    date = pd.Timestamp("2025-12-31")
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=[symbol],
        strategic_exit_bands={symbol: [0.0] * 5},
        strategic_active_bands={symbol: [True] * 5},
        protected_weights={symbol: 0.30},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": True,
            "freeze_new_risk": True,
        },
        (),
        "NONE",
        freeze_new_risk=True,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel={},
        leaders={},
        account=account,
        prices={},
    )

    assert targets == ()
    assert account.candidate_tenure["strategic_cohort_active"] == 0
    assert account.candidate_tenure["strategic_cohort_completed"] == 1
    assert account.strategic_epochs_completed == 1
    assert account.strategic_last_exit_date == "2025-12-31"
    assert account.strategic_rearm_date == ""
    assert account.strategic_cohort_symbols == []


def test_sentinel_stale_recovery_release_cannot_commit_same_day_new_cohort() -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    candidates = ("new_a", "new_b", "new_c")
    frame = pd.DataFrame(
        {
            "close": [1.0] * 129 + [1.10],
            "ma20": 1.0,
            "ma60": 0.95,
            "ma120": 1.20,
            "ret5": 0.10,
            "ret20": -0.05,
            "ret60": -0.10,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        anchor_weights={"old_anchor": 0.40},
        recovery_anchor_date=str(dates[0].date()),
        recovery_conviction_symbol="old_conviction",
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": True,
            "freeze_new_risk": True,
            "broad_ret120": 0.10,
            "tech_ret120": 0.10,
            "risk_anchor_group_count": 3,
        },
        (),
        "NONE",
        freeze_new_risk=True,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: frame for symbol in candidates},
        leaders={
            symbol: LeaderScore(symbol, 0.90, 0.95, True, False, symbol, {})
            for symbol in candidates
        },
        account=account,
        prices={symbol: 1.10 for symbol in candidates},
    )

    assert targets == ()
    assert account.anchor_weights == {}
    assert account.recovery_anchor_date == ""
    assert account.recovery_conviction_symbol == "old_conviction"
    assert account.candidate_tenure["recovery_cohort_locked"] == 0
    assert account.candidate_tenure["recovery_cohort_graduated"] == 1


def test_confirmed_fast_recovery_hands_reduced_core_to_new_owner_without_raising_gross() -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    recovery_close = [2.0] * 119 + [0.70 + 0.04 * index for index in range(11)]
    recovery_frame = pd.DataFrame(
        {
            "close": recovery_close,
            "ma20": 0.80,
            "ma60": 0.90,
            "ma120": 1.20,
            "ret5": 0.10,
            "ret20": -0.05,
            "ret60": -0.10,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    incumbent_frame = recovery_frame.assign(ret120=0.10)
    account = AccountState(
        initial_cash=100.0,
        cash=60.0,
        positions={
            "old_core": Position(
                "old_core",
                shares=40,
                avg_cost=1.20,
                entry_date=str(dates[0].date()),
                lifecycle=Lifecycle.CORE.value,
            )
        },
        active_leaders=["old_core"],
        dynamic_k=1,
        last_k_change_date=str(dates[0].date()),
        capital_budget_level=2,
        last_shock_date=str(dates[-10].date()),
        protected_weights={"old_core": 0.40},
        candidate_tenure={"fast_v_recovery": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.CAUTION,
        0.40,
        0,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.10,
            "broad_ret120": -0.10,
            "tech_ret120": 0.04,
            "risk_anchor_group_count": 2,
        },
        ("confirmed fast V recovery",),
        "FAST_V_RECOVERY",
        freeze_new_risk=True,
        reduction_level=2,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={"old_core": incumbent_frame, "new_owner": recovery_frame},
        leaders={
            "old_core": LeaderScore("old_core", 0.55, 1.0, False, False, "old", {}),
            "new_owner": LeaderScore("new_owner", 0.85, 1.0, False, False, "new", {}),
        },
        account=account,
        prices={"old_core": 1.0, "new_owner": recovery_close[-1]},
    )

    # A fast-recovery label cannot grant a replacement while Base Risk freezes BUYs.
    assert {target.symbol: target.weight for target in targets} == pytest.approx({"old_core": .40})
    assert account.anchor_weights == {}
    assert account.protected_weights == {"old_core": .40}
    assert account.positions["old_core"].shares == 40
    assert account.cash == 60.0
    assert account.capital_budget_level == 2
    assert account.operating_peak == account.capital_peak == 100.0
    assert plan_orders(signal_date=str(dates[-1].date()), targets=targets, account=account,
                       prices={"old_core": 1.0, "new_owner": recovery_close[-1]}, cfg=DEFAULT_CONFIG) == ()


@pytest.mark.parametrize("continuous_holdings", (True, False), ids=("held", "exited"))
def test_ordinary_level1_restore_uses_the_risk_assessment_cap_directly(continuous_holdings: bool) -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    frame = pd.DataFrame(
        {
            "close": 1.0,
            "ma20": 0.9,
            "ma60": 0.8,
            "ma120": 0.7,
            "ret5": 0.05,
            "ret20": 0.10,
            "ret60": 0.20,
            "ret120": 0.30,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    owner = {"lead": 0.60, "member_1": 0.16, "member_2": 0.16}
    held = {"lead": 34, "member_1": 8, "member_2": 24} if continuous_holdings else {"member_2": 24}
    account = AccountState(
        initial_cash=100.0,
        cash=100.0 - sum(held.values()),
        positions={
            symbol: Position(
                symbol, shares=shares, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value,
                entry_date=str(dates[0].date()),
            ) for symbol, shares in held.items()
        },
        anchor_weights=dict(owner),
        protected_weights=dict(owner),
        capital_budget_level=1,
        capital_budget_repair_streak=1,
        last_shock_date=str(dates[-5].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        symbol: LeaderScore(symbol, 0.80, 1.0, False, False, symbol, {})
        for symbol in owner
    }
    risk = RiskAssessment(
        Risk.CAUTION,
        0.92,
        0,
        {"freeze_new_risk": True, "transition_damage": 0.30},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in owner},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in owner},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: shares / 100.0 for symbol, shares in held.items()}
    )
    assert account.protected_weights == pytest.approx(owner)
    thawed = replace(risk, freeze_new_risk=False, evidence={"freeze_new_risk": False})
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1], opportunity=Opportunity.RECOVERY, risk=thawed,
        user_panel=_restore_panel(owner, end=str(dates[-1].date())), leaders=leaders,
        account=account, prices={symbol: 1.0 for symbol in owner},
    )
    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"lead": .60, "member_1": .08, "member_2": .24} if continuous_holdings else {"member_2": .24}
    )
    if continuous_holdings:
        assert sum(target.weight for target in targets) == pytest.approx(risk.target_gross_cap)
    else:
        # Full exits end ordinary restoration rights; a new position still
        # needs qualification even after the account risk freeze is lifted.
        assert all(target.symbol not in {"lead", "member_1"} for target in targets)
        assert sum(target.weight for target in targets) < risk.target_gross_cap
    assert {symbol: position.shares for symbol, position in account.positions.items()} == held
    assert account.cash == 100.0 - sum(held.values())
    assert account.capital_budget_level == 1
    assert account.protected_weights == pytest.approx(owner)


def test_strong_two_index_market_does_not_mask_independent_deep_probe():
    targets, account = _tactical_targets(
        ret20=0.10,
        ret120=0.50,
        broad_ret120=0.12,
        tech_ret120=0.14,
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"deep_candidate": DEFAULT_CONFIG.core_admission_weight}
    )
    assert account.candidate_tenure.get("tactical_active", 0) == 0


def test_transitional_market_does_not_use_weak_market_early_graduation():
    dates = pd.bdate_range(
        "2026-01-02",
        periods=DEFAULT_CONFIG.recovery_cohort_weak_graduation_days + 10,
    )
    frame = pd.DataFrame(
        {
            "close": 1.0,
            "ma20": 0.95,
            "ma60": 0.90,
            "ma120": 0.80,
            "ret5": 0.01,
            "ret20": 0.05,
            "ret60": 0.10,
            "ret120": 0.15,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
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
        anchor_weights={"anchor": 0.40},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"leader_cycle_armed": 1},
        active_leaders=["anchor", "new_core"],
        dynamic_k=2,
        last_k_change_date=str(dates[-1].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        symbol: LeaderScore(symbol, 0.90, 0.95, True, False, symbol, {}) for symbol in account.positions
    }
    transitional_risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.10, "tech_ret120": 0.04},
        (),
        "NONE",
    )

    PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=transitional_risk,
        user_panel={symbol: frame for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in account.positions},
    )

    assert set(account.anchor_weights) == {"anchor"}
    assert account.candidate_tenure.get("recovery_cohort_graduated", 0) == 0


def test_recovery_breadth_perturbation_does_not_change_primary_path(data_dir):
    results = []
    for breadth in (DEFAULT_CONFIG.recovery_breadth_min * 0.90, DEFAULT_CONFIG.recovery_breadth_min * 1.10):
        result = ProductionEngine(data_dir, DEFAULT_CONFIG.override(recovery_breadth_min=breadth)).backtest(
            symbols=PRIMARY, start="2025-04-01", end="2026-06-30"
        )
        results.append(
            (
                result["final_wealth"],
                result["max_drawdown"],
                result["account_orders"],
                result["decision_digests"],
                result["legacy_decision_digests"],
            )
        )
    assert results[0][0:3] == pytest.approx(results[1][0:3])
    assert results[0][3] != results[1][3]
    assert results[0][4] == results[1][4]


def test_protected_restore_uses_risk_assessment_as_only_gross_cap():
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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0}) for symbol in account.positions}
    risk = RiskAssessment(Risk.CAUTION, 0.35, 0, {}, (), "RECOVERY")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=_restore_panel(account.positions),
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )
    assert sum(target.weight for target in targets) == pytest.approx(0.35)
    assert targets
    current = {"a": 0.60, "b": 0.30}
    reduced = [target for target in targets if target.weight + 1e-12 < current[target.symbol]]
    unchanged = [target for target in targets if target not in reduced]
    assert reduced
    assert all(target.exit_kind == "risk" for target in reduced)
    assert all(target.reason_code == "risk_gross_cap" for target in reduced)
    assert all(target.exit_kind == "strategy" for target in unchanged)


def test_confirming_recovery_alternative_prevents_secondary_restore_churn() -> None:
    account = AccountState(
        initial_cash=100.0,
        cash=52.0,
        positions={
            "lead": Position("lead", shares=34, avg_cost=1.0, entry_date="2026-01-01"),
            "secondary": Position(
                "secondary", shares=14, avg_cost=1.0, entry_date="2026-01-01"
            ),
        },
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"lead": 0.60, "secondary": 0.16},
        last_shock_date="2026-01-02",
        shock_severity="CONCENTRATED",
        replacement_tenure={"recovery_admission:alternative,lead": 2},
    )
    leaders = {
        symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0})
        for symbol in account.positions
    }
    risk = RiskAssessment(Risk.CAUTION, 1.0, 0, {}, (), "RECOVERY")

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=_restore_panel(account.positions),
        leaders=leaders,
        account=account,
        prices={"lead": 1.0, "secondary": 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"lead": 0.60, "secondary": 0.14}
    )
    assert next(target for target in targets if target.symbol == "secondary").reason == "retained core holding"
    restored = next(target for target in targets if target.symbol == "lead")
    assert restored.origin_subsystem == "RECOVERY"
    assert restored.mechanism == "POST_SHOCK_RESTORATION"
    assert account.replacement_tenure["recovery_admission:alternative,lead"] == 2


@pytest.mark.parametrize("shock", ("2026-01-02", "", "2025-12-31"), ids=("linked", "missing", "pre-entry"))
def test_caution_restore_can_buy_up_to_the_risk_owned_cap(shock: str) -> None:
    cfg = DEFAULT_CONFIG
    first_symbol = "sz300308"
    second_symbol = "sz300502"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=910_000.0,
        positions={
            first_symbol: Position(first_symbol, shares=60_000, avg_cost=1.0, entry_date="2026-01-01"),
            second_symbol: Position(second_symbol, shares=30_000, avg_cost=1.0, entry_date="2026-01-01"),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={first_symbol: 0.60, second_symbol: 0.30},
        last_shock_date=shock,
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0}) for symbol in account.positions}
    risk = RiskAssessment(Risk.CAUTION, 0.25, 0, {}, (), "RECOVERY")
    targets = PortfolioAllocator(cfg).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=_restore_panel(account.positions),
        leaders=leaders,
        account=account,
        prices={first_symbol: 1.0, second_symbol: 1.0},
    )
    targets = _attach_target_attribution(signal_date="2026-01-05", targets=targets)
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={first_symbol: 1.0, second_symbol: 1.0},
        cfg=cfg,
    )

    if shock == "2026-01-02":
        assert {target.symbol: target.weight for target in targets} == pytest.approx(
            {first_symbol: 0.22, second_symbol: 0.03}
        )
        assert sum(target.weight for target in targets) == pytest.approx(0.25)
        assert {order.side for order in planned} == {"BUY"}
        assert {order.symbol for order in planned} == {first_symbol}
    else:
        assert {target.symbol: target.weight for target in targets} == pytest.approx(
            {first_symbol: 0.06, second_symbol: 0.03}
        )
        assert planned == ()
    assert account.protected_weights == {first_symbol: 0.60, second_symbol: 0.30}
    assert account.cash == 910_000.0
    assert {symbol: position.shares for symbol, position in account.positions.items()} == {
        first_symbol: 60_000, second_symbol: 30_000,
    }



from _recovery_restore_completion_cases import (
    test_incomplete_protected_sell_keeps_global_lifecycle_priority_on_recovery_cap,
    test_full_normal_restore_reaches_original_targets_before_completion,
    test_completed_post_shock_restore_becomes_a_sticky_hold,
    test_capacity_limited_restore_keeps_one_durable_target_until_filled,
    test_restore_buy_closes_the_gap_between_no_trade_band_and_completion_line,
    test_satellite_restore_keeps_the_standard_no_trade_band,
    test_full_recovery_seat_cannot_remain_below_eighty_percent_restored,
)

from _recovery_post_shock_cases import (
    test_restoration_never_bypasses_the_absolute_minimum_ticket,
    test_post_shock_restore_is_buy_only_when_members_drift_apart,
    test_post_shock_restore_labels_required_sells_as_recovery_cohort,
    test_small_restore_gap_remains_executable_instead_of_hanging_forever,
)
