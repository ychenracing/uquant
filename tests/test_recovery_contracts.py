from __future__ import annotations

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.execution import plan_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    LeaderScore,
    Lifecycle,
    Opportunity,
    PendingOrder,
    Position,
    Risk,
    RiskAssessment,
    Target,
    Tranche,
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
    frame = _tactical_frame(ret20=ret20, ret120=ret120)
    date = frame.index[-1]
    symbol = "deep_candidate"
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": broad_ret120,
            "tech_ret120": tech_ret120,
        },
        (),
        "NONE",
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel={symbol: frame},
        leaders={
            symbol: LeaderScore(
                symbol,
                0.90,
                0.95,
                True,
                False,
                "independent",
                {},
            )
        },
        account=account,
        prices={symbol: 0.94},
    )
    return targets, account


def test_transitional_recovery_admits_only_promotable_deep_crash_candidate():
    targets, account = _tactical_targets(
        ret20=-0.10,
        ret120=-0.40,
        broad_ret120=-0.10,
        tech_ret120=0.04,
    )

    assert [(target.symbol, target.lifecycle) for target in targets] == [
        ("deep_candidate", Lifecycle.RECOVERY.value)
    ]
    assert account.candidate_tenure["tactical_active"] == 1
    assert account.candidate_tenure["tactical_promotable"] == 1
    assert account.tactical_anchor_symbol == "deep_candidate"


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


def test_final_tactical_exit_retires_stale_restore_owner() -> None:
    symbol = "deep_candidate"
    frame = _tactical_frame(ret20=-0.10, ret120=-0.40)
    date = frame.index[-1]
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=60,
                avg_cost=0.70,
                lifecycle=Lifecycle.RECOVERY.value,
                entry_date=str(frame.index[0].date()),
            )
        },
        tactical_anchor_symbol=symbol,
        protected_weights={symbol: 0.60},
        strategic_restore_weights={symbol: 0.60},
        candidate_tenure={"tactical_active": 1, "tactical_promotable": 0},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel={symbol: frame},
        leaders={symbol: LeaderScore(symbol, 0.90, 0.95, True, False, "independent", {})},
        account=account,
        prices={symbol: 0.94},
    )

    assert {target.symbol: target.weight for target in targets} == {symbol: 0.0}
    assert symbol not in account.protected_weights
    assert symbol not in account.strategic_restore_weights
    assert account.tactical_anchor_symbol == ""


def test_strong_two_index_market_does_not_mask_independent_deep_probe():
    targets, account = _tactical_targets(
        ret20=-0.10,
        ret120=-0.40,
        broad_ret120=0.12,
        tech_ret120=0.14,
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"deep_candidate": DEFAULT_CONFIG.tactical_rebound_weight}
    )
    assert account.candidate_tenure["tactical_active"] == 1


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
            )
        )
    assert results[0][0:3] == pytest.approx(results[1][0:3])
    assert results[0][3] == results[1][3]


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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
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
    assert sum(target.weight for target in targets) == pytest.approx(0.35)
    assert all("confirmed post-shock restoration" in target.reason for target in targets)
    current = {"a": 0.60, "b": 0.30}
    reduced = [target for target in targets if target.weight + 1e-12 < current[target.symbol]]
    unchanged = [target for target in targets if target not in reduced]
    assert reduced
    assert all(target.exit_kind == "risk" for target in reduced)
    assert all(target.reason_code == "risk_gross_cap" for target in reduced)
    assert all(target.exit_kind == "strategy" for target in unchanged)


def test_caution_restore_can_buy_up_to_the_risk_owned_cap() -> None:
    cfg = DEFAULT_CONFIG.override(min_trade_value=0.0)
    account = AccountState(
        initial_cash=100.0,
        cash=91.0,
        positions={
            "a": Position("a", shares=6, avg_cost=1.0, entry_date="2026-01-01"),
            "b": Position("b", shares=3, avg_cost=1.0, entry_date="2026-01-01"),
        },
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"a": 0.60, "b": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(Risk.CAUTION, 0.25, 0, {}, (), "RECOVERY")
    targets = PortfolioAllocator(cfg).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={"a": 1.0, "b": 1.0},
        cfg=cfg,
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"a": 1.0 / 6.0, "b": 1.0 / 12.0}
    )
    assert sum(target.weight for target in targets) == pytest.approx(0.25)
    assert {order.side for order in planned} == {"BUY"}


def test_incomplete_protected_sell_keeps_global_lifecycle_priority_on_recovery_cap() -> None:
    account = AccountState(
        initial_cash=100.0,
        cash=30.0,
        positions={
            "mixed": Position(
                "mixed",
                shares=40,
                avg_cost=1.0,
                tranches=[
                    Tranche(
                        "mixed_core",
                        Lifecycle.CORE.value,
                        20,
                        1.0,
                        "2026-01-01",
                        "2026-01-02",
                        1.0,
                    ),
                    Tranche(
                        "mixed_satellite",
                        Lifecycle.SATELLITE.value,
                        20,
                        1.0,
                        "2026-01-03",
                        "2026-01-04",
                        1.0,
                    ),
                ],
            ),
            "add2": Position(
                "add2",
                shares=30,
                avg_cost=1.0,
                tranches=[
                    Tranche(
                        "add2_lot",
                        Lifecycle.ADD2.value,
                        30,
                        1.0,
                        "2026-01-03",
                        "2026-01-04",
                        1.0,
                    )
                ],
            ),
        },
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"mixed": 0.40, "add2": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(Risk.CAUTION, 0.40, 0, {}, (), "RECOVERY"),
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"mixed": 1.0, "add2": 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"mixed": 0.20, "add2": 0.20}
    )


def test_full_normal_restore_reaches_original_targets_before_completion() -> None:
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "a": Position("a", shares=55, avg_cost=1.0, entry_date="2026-01-01"),
            "b": Position("b", shares=35, avg_cost=1.0, entry_date="2026-01-01"),
        },
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"a": 0.60, "b": 0.40},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    normal = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=normal,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx({"a": 0.60, "b": 0.40})
    assert account.candidate_tenure.get("post_shock_restore_complete", 0) == 0

    account.positions["a"].shares = 60
    account.positions["b"].shares = 40
    account.cash = 0.0
    PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.RECOVERY,
        risk=normal,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )
    assert account.candidate_tenure["post_shock_restore_complete"] == 1


def test_completed_post_shock_restore_becomes_a_sticky_hold():
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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    restored = allocator.allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert account.candidate_tenure["post_shock_restore_complete"] == 1
    assert account.protected_weights == {"a": 0.60, "b": 0.30}
    assert {target.reason for target in restored} == {"completed post-shock restoration; retain price drift"}

    drift_prices = {"a": 1.10, "b": 0.90}
    sticky = allocator.allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices=drift_prices,
    )
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=sticky,
        account=account,
        prices=drift_prices,
        cfg=DEFAULT_CONFIG,
    )

    assert planned == ()
    assert account.protected_weights == {"a": 0.60, "b": 0.30}


def test_capacity_limited_restore_keeps_one_durable_target_until_filled():
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={
            "a": Position("a", shares=20, avg_cost=1.0, entry_date="2026-01-01"),
            "b": Position("b", shares=10, avg_cost=1.0, entry_date="2026-01-01"),
        },
        pending_orders=[
            PendingOrder(
                signal_date="2026-01-04",
                symbol="a",
                side="BUY",
                target_weight=0.60,
                reason="confirmed post-shock restoration",
                lifecycle=Lifecycle.RECOVERY.value,
                remaining_shares=40,
            )
        ],
        operating_peak=100.0,
        capital_peak=100.0,
        protected_weights={"a": 0.60, "b": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    partial = allocator.allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
    assert {target.symbol: target.weight for target in partial} == pytest.approx({"a": 0.60, "b": 0.30})

    account.pending_orders.clear()
    account.positions["a"].shares = 60
    account.positions["b"].shares = 30
    account.cash = 10.0
    completed = allocator.allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert account.candidate_tenure["post_shock_restore_complete"] == 1
    assert {target.reason for target in completed} == {"completed post-shock restoration; retain price drift"}


def test_restore_buy_closes_the_gap_between_no_trade_band_and_completion_line():
    cfg = DEFAULT_CONFIG.override(min_trade_value=0.0)
    target = Target(
        "restore",
        0.30,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=74.0,
        positions={
            "restore": Position(
                "restore",
                shares=26,
                avg_cost=1.0,
                entry_date="2026-01-01",
            )
        },
        protected_weights={"restore": 0.30},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    planned = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={"restore": 1.0},
        cfg=cfg,
    )

    assert len(planned) == 1
    assert planned[0].side == "BUY"
    assert planned[0].target_weight == pytest.approx(0.30)

    account.positions["restore"].shares = 29
    account.cash = 71.0
    assert (
        plan_orders(
            signal_date="2026-01-06",
            targets=(target,),
            account=account,
            prices={"restore": 1.0},
            cfg=cfg,
        )
        == ()
    )

    # A saved target below the 95% completion line can still be economically
    # complete when its absolute gap is smaller than the dedicated 1% restore
    # threshold.  This keeps planning and lifecycle settlement on the same
    # executable boundary instead of creating a permanent micro-order loop.
    micro_target = Target(
        "micro_restore",
        0.08,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
    )
    micro_account = AccountState(
        initial_cash=100.0,
        cash=92.9,
        positions={
            "micro_restore": Position(
                "micro_restore",
                shares=71,
                avg_cost=0.1,
                entry_date="2026-01-01",
            )
        },
        protected_weights={"micro_restore": 0.08},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    assert (
        plan_orders(
            signal_date="2026-01-06",
            targets=(micro_target,),
            account=micro_account,
            prices={"micro_restore": 0.1},
            cfg=cfg,
        )
        == ()
    )


def test_restoration_never_bypasses_the_absolute_minimum_ticket() -> None:
    target = Target(
        "restore",
        0.30,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
    )
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=716_000.0,
        positions={
            "restore": Position(
                "restore",
                shares=284_000,
                avg_cost=1.0,
                entry_date="2026-01-01",
            )
        },
        protected_weights={"restore": 0.30},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )

    planned = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={"restore": 1.0},
        cfg=DEFAULT_CONFIG,
    )

    assert DEFAULT_CONFIG.min_trade_value > 0.30 * 1_000_000.0 - 284_000.0
    assert planned == ()


def test_post_shock_restore_is_buy_only_when_members_drift_apart():
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=100_000.0,
        positions={
            "winner": Position(
                "winner",
                shares=6_600,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
            "laggard": Position(
                "laggard",
                shares=2_400,
                avg_cost=100.0,
                entry_date="2026-01-01",
            ),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={"winner": 0.60, "laggard": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"winner": 100.0, "laggard": 100.0},
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={"winner": 100.0, "laggard": 100.0},
        cfg=DEFAULT_CONFIG,
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"winner": 0.60, "laggard": 0.30}
    )
    assert [(order.side, order.symbol) for order in planned] == [("BUY", "laggard")]


def test_small_restore_gap_remains_executable_instead_of_hanging_forever():
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=140_000.0,
        positions={
            "a": Position("a", shares=5_600, avg_cost=100.0, entry_date="2026-01-01"),
            "b": Position("b", shares=3_000, avg_cost=100.0, entry_date="2026-01-01"),
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
        protected_weights={"a": 0.60, "b": 0.30},
        shock_severity="SEVERE",
    )
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, "test", {}) for symbol in account.positions}
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={symbol: pd.DataFrame() for symbol in account.positions},
        leaders=leaders,
        account=account,
        prices={"a": 100.0, "b": 100.0},
    )
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=targets,
        account=account,
        prices={"a": 100.0, "b": 100.0},
        cfg=DEFAULT_CONFIG,
    )

    assert account.candidate_tenure.get("post_shock_restore_complete", 0) == 0
    assert {target.reason for target in targets} == {
        "confirmed post-shock restoration",
        "post-shock restoration; retain winner drift",
    }
    assert [(order.side, order.symbol) for order in planned] == [("BUY", "a")]
