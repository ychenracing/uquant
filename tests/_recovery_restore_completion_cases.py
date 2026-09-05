from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import _attach_target_attribution
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


def _restore_panel(symbols, *, end="2026-01-07"):
    """Observable structure and independent, finite correlation history."""
    dates = pd.bdate_range(end=end, periods=150)
    panel = {}
    for index, symbol in enumerate(symbols):
        returns = .001 + .004 * np.sin(np.arange(len(dates)) * (.73 + index * .48))
        close = np.cumprod(1 + returns)
        close /= close[-1]
        panel[symbol] = pd.DataFrame({
            "close": close, "ma20": close * .99, "ma60": close * .97,
            "ma120": close * .95, "ret20": .10, "ret60": .20,
            "ret120": .30, "amount": 1_000_000_000.0,
        }, index=dates)
    return panel


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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0}) for symbol in account.positions}
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(Risk.CAUTION, 0.40, 0, {}, (), "RECOVERY"),
        user_panel=_restore_panel(account.positions),
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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0}) for symbol in account.positions}
    normal = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"),
        opportunity=Opportunity.RECOVERY,
        risk=normal,
        user_panel=_restore_panel(account.positions),
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx({"a": 0.60, "b": 0.40})
    assert account.candidate_tenure.get("post_shock_restore_complete", 0) == 0

    assert account.candidate_tenure.get("core_restored:a", -1) == -1
    assert account.candidate_tenure.get("core_restored:b", -1) == -1
    assert (account.positions["a"].shares, account.positions["b"].shares, account.cash) == (55, 35, 10.0)

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
    leaders = {symbol: LeaderScore(symbol, 0.8, 1.0, True, False, symbol, {"unknown_industry": 0.0}) for symbol in account.positions}
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
        user_panel=_restore_panel(account.positions),
        leaders=leaders,
        account=account,
        prices={"a": 1.0, "b": 1.0},
    )

    assert account.candidate_tenure["core_restored:a"] == 0
    assert account.candidate_tenure["core_restored:b"] == 0
    assert account.protected_weights == {"a": 0.60, "b": 0.30}
    assert {target.reason for target in restored} == {"retained core holding"}

    drift_prices = {"a": 1.10, "b": 0.90}
    sticky = allocator.allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel=_restore_panel(account.positions),
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

@pytest.mark.parametrize("restriction", ("none", "risk_cap", "industry", "correlation", "freeze"))
def test_capacity_limited_restore_keeps_one_durable_target_until_filled(restriction):
    account = AccountState(
        initial_cash=1_000_000.0, cash=700_000.0,
        positions={symbol: Position(symbol, shares, 1.0, "2025-01-02")
                   for symbol, shares in (("a", 200_000), ("b", 100_000))},
        pending_orders=[PendingOrder(
            "2026-01-02", "a", "BUY", .60, "core restoration", Lifecycle.CORE.value,
            remaining_shares=400_000, origin_subsystem="RECOVERY",
            mechanism="POST_SHOCK_RESTORATION", origin_lifecycle="CORE",
        )],
        protected_weights={"a": .60, "b": .30}, operating_peak=1_200_000.0,
        capital_peak=1_250_000.0,
    )
    leaders = {symbol: LeaderScore(symbol, .8, .95, False, False,
                                   "shared" if restriction == "industry" else symbol,
                                   {"unknown_industry": 0.0}) for symbol in account.positions}
    panel = _restore_panel(account.positions)
    if restriction == "correlation":
        panel["b"] = panel["a"].copy()
    risk = RiskAssessment(Risk.NORMAL, .50 if restriction == "risk_cap" else 1.0,
                          0, {}, (), "NONE", freeze_new_risk=restriction == "freeze")
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-05"), opportunity=Opportunity.RECOVERY,
        risk=risk, user_panel=panel, leaders=leaders, account=account,
        prices={"a": 1.0, "b": 1.0},
    )
    expected = {"none": {"a": .6, "b": .3}, "risk_cap": {"a": .4, "b": .1},
                "industry": {"a": .6, "b": .15}, "correlation": {"a": .6, "b": .15},
                "freeze": {"a": .2, "b": .1}}
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected[restriction])
    assert account.candidate_tenure.get("core_restored:a", -1) == -1
    assert account.positions["a"].shares == 200_000
    assert account.cash == 700_000.0
    assert (account.operating_peak, account.capital_peak) == (1_200_000.0, 1_250_000.0)

def test_restore_buy_closes_the_gap_between_no_trade_band_and_completion_line():
    cfg = DEFAULT_CONFIG.override(min_trade_value=0.0)
    symbol = "sz300502"
    target = Target(
        symbol,
        0.30,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
        origin_subsystem="RECOVERY",
        mechanism="POST_SHOCK_RESTORATION",
        origin_lifecycle=Lifecycle.RECOVERY.value,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=74.0,
        positions={
            symbol: Position(
                symbol,
                shares=26,
                avg_cost=1.0,
                entry_date="2026-01-01",
            )
        },
        protected_weights={symbol: 0.30},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    attributed_target = _attach_target_attribution(
        signal_date="2026-01-05",
        targets=(target,),
    )[0]
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=(attributed_target,),
        account=account,
        prices={symbol: 1.0},
        cfg=cfg,
    )

    assert len(planned) == 1
    assert planned[0].side == "BUY"
    assert planned[0].target_weight == pytest.approx(0.30)

    account.positions[symbol].shares = 29
    account.cash = 71.0
    assert (
        plan_orders(
            signal_date="2026-01-06",
            targets=(attributed_target,),
            account=account,
            prices={symbol: 1.0},
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
        origin_subsystem="RECOVERY",
        mechanism="POST_SHOCK_RESTORATION",
        origin_lifecycle=Lifecycle.RECOVERY.value,
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

def test_satellite_restore_keeps_the_standard_no_trade_band():
    cfg = DEFAULT_CONFIG.override(min_trade_value=0.0)
    target = Target(
        "satellite",
        0.12,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=92.0,
        positions={
            "satellite": Position(
                "satellite",
                shares=8,
                avg_cost=1.0,
                entry_date="2026-01-01",
            )
        },
        protected_weights={"satellite": 0.12},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    assert (
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=account,
            prices={"satellite": 1.0},
            cfg=cfg,
        )
        == ()
    )

def test_full_recovery_seat_cannot_remain_below_eighty_percent_restored() -> None:
    cfg = DEFAULT_CONFIG.override(min_trade_value=0.0)
    symbol = "sz300502"
    target = Target(
        symbol,
        0.16,
        Lifecycle.RECOVERY.value,
        0.8,
        1.0,
        "confirmed post-shock restoration",
        origin_subsystem="RECOVERY",
        mechanism="POST_SHOCK_RESTORATION",
        origin_lifecycle=Lifecycle.RECOVERY.value,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=87.9,
        positions={
            symbol: Position(
                symbol,
                shares=121,
                avg_cost=0.1,
                entry_date="2026-01-01",
            )
        },
        protected_weights={symbol: 0.16},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    target = _attach_target_attribution(
        signal_date="2026-01-05",
        targets=(target,),
    )[0]
    planned = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={symbol: 0.1},
        cfg=cfg,
    )

    assert [(order.side, order.symbol, order.target_weight) for order in planned] == [
        ("BUY", symbol, pytest.approx(0.16))
    ]
