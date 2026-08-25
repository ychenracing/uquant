from __future__ import annotations

import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _leader,
    _normal_risk,
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
    Tranche,
)


@pytest.mark.parametrize(
    ("opportunity", "expected_cap"),
    (
        (Opportunity.CHOPPY, DEFAULT_CONFIG.choppy_target_gross),
        (Opportunity.WEAK, DEFAULT_CONFIG.weak_gross),
    ),
)
def test_opportunity_budget_caps_new_risk_without_selling_existing_core(
    opportunity: Opportunity,
    expected_cap: float,
):
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("leader_a", "leader_b", "leader_c")
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=50_000.0,
        positions={
            symbols[0]: Position(symbols[0], shares=400_000, avg_cost=1.0, highest_close=1.2),
            symbols[1]: Position(symbols[1], shares=300_000, avg_cost=1.0, highest_close=1.2),
            symbols[2]: Position(symbols[2], shares=250_000, avg_cost=1.0, highest_close=1.2),
        },
        active_leaders=list(symbols),
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    leaders = {
        symbols[0]: _leader(symbols[0], 0.90, industry="compute"),
        symbols[1]: _leader(symbols[1], 0.80, industry="memory"),
        symbols[2]: _leader(symbols[2], 0.70, industry="equipment"),
    }

    targets = PortfolioAllocator(DEFAULT_CONFIG)._leader_targets(
        date=dates[-1],
        opportunity=opportunity,
        risk=_normal_risk(),
        user_panel={symbol: _trend_frame(dates) for symbol in symbols},
        leaders=leaders,
        account=account,
        weights_now={symbols[0]: 0.40, symbols[1]: 0.30, symbols[2]: 0.25},
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert targets is not None
    assert sum(target.weight for target in targets) == pytest.approx(0.95)
    assert not any("opportunity gross contraction" in target.reason for target in targets)

    proposed = {symbol: weight for symbol, weight in zip(symbols, (0.40, 0.30, 0.25), strict=True)}
    capped = PortfolioAllocator(DEFAULT_CONFIG)._cap_opportunity_gross(
        proposed=proposed,
        gross_cap=expected_cap,
        weights_now={},
        leaders=leaders,
        reasons={},
        opportunity=opportunity,
    )
    assert sum(capped.values()) == pytest.approx(expected_cap)

def test_risk_liquidated_strategic_exit_band_is_settled_without_reentry():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbol = "risk_liquidated_member"
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_exit_bands={symbol: [0.06] * 5},
        strategic_active_bands={symbol: [True] * 5},
        strategic_restore_weights={symbol: 0.30},
        protected_weights={symbol: 0.30},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    kwargs = {
        "risk": _normal_risk(),
        "user_panel": {symbol: frame},
        "leaders": {symbol: _leader(symbol, 0.90)},
        "account": account,
        "prices": {symbol: 1.0},
        "weights_now": {},
    }

    targets = allocator._strategic_cohort_targets(date=dates[-2], **kwargs)

    assert targets == ()
    assert account.strategic_cohort_targets == {}
    assert account.strategic_exit_bands == {}
    assert account.strategic_active_bands == {}
    assert account.strategic_restore_weights == {}
    assert account.protected_weights == {}

    assert allocator._strategic_cohort_targets(date=dates[-1], **kwargs) is None
    assert account.candidate_tenure["strategic_cohort_active"] == 0
    assert account.candidate_tenure["strategic_cohort_completed"] == 1
    assert account.strategic_epochs_completed == 1

def test_strategic_restore_waits_for_every_member_but_settles_a_satisfied_pending_buy():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("restored_a", "restored_b", "missing_c")
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=1.0,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
            )
            for symbol in symbols[:2]
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_restore_weights={symbol: 0.30 for symbol in symbols},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    # This unit fixture uses a normalized 100-unit account. Disable the
    # production absolute ticket so the test isolates per-member restoration
    # and pending-order durability rather than minimum-notional settlement.
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))

    targets = allocator._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now={symbols[0]: 0.30, symbols[1]: 0.30},
    )

    observed = {target.symbol: target.weight for target in targets or ()}
    assert set(observed) == set(symbols)
    assert observed == pytest.approx({symbol: 0.30 for symbol in symbols})
    assert account.strategic_restore_weights == {symbol: 0.30 for symbol in symbols}

    account.positions[symbols[2]] = Position(
        symbols[2],
        shares=30,
        avg_cost=1.0,
        entry_date=str(dates[-20].date()),
        highest_close=1.0,
    )
    account.cash = 10.0
    account.pending_orders = [
        PendingOrder(
            signal_date=str(dates[-2].date()),
            symbol=symbols[2],
            side="BUY",
            target_weight=0.30,
            reason="strategic restore",
            lifecycle=Lifecycle.CORE.value,
            remaining_shares=1,
        )
    ]
    all_restored = {symbol: 0.30 for symbol in symbols}
    allocator._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now=all_restored,
    )
    assert account.strategic_restore_weights == {}

    account.pending_orders.clear()
    allocator._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now=all_restored,
    )
    assert account.strategic_restore_weights == {}

def test_strategic_restore_completes_against_scaled_attainable_weights() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("drift_winner_a", "drift_winner_b", "restored_member")
    saved = dict(zip(symbols, (0.335, 0.325, 0.337), strict=True))
    weights_now = dict(zip(symbols, (0.345, 0.335, 0.318), strict=True))
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={
            symbol: Position(symbol, shares=30, avg_cost=1.0, highest_close=1.0)
            for symbol in symbols
        },
        pending_orders=[
            PendingOrder(
                signal_date=str(dates[-2].date()),
                symbol=symbols[2],
                side="BUY",
                target_weight=0.328,
                reason="scaled strategic restore",
                lifecycle=Lifecycle.CORE.value,
                remaining_shares=1,
            )
        ],
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        strategic_restore_weights=saved,
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now=weights_now,
    )

    assert account.strategic_restore_weights == {}
    assert account.candidate_tenure["strategic_damage_guard_active_epoch"] == 0
    assert account.candidate_tenure["strategic_damage_guard_complete_epoch"] == 1

def test_strategic_restore_caps_winner_drift_before_outer_risk_reduction() -> None:
    """Winner drift plus saved loser weights must not bypass the hard gross cap."""

    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("drift_winner", "restore_a", "restore_b")
    account = AccountState(
        initial_cash=100.0,
        cash=17.0,
        positions={
            symbols[0]: Position(symbols[0], shares=35, avg_cost=1.0, highest_close=1.0),
            symbols[1]: Position(symbols[1], shares=32, avg_cost=1.0, highest_close=1.0),
            symbols[2]: Position(symbols[2], shares=16, avg_cost=1.0, highest_close=1.0),
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        strategic_restore_weights=dict(zip(symbols, (0.345, 0.34, 0.315), strict=True)),
        strategic_candidate_signature="strategic_qualification:reversal_industry:drift_winner,restore_a,restore_b",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 1,
        },
        capital_budget_level=2,
        operating_peak=100.0,
        capital_peak=100.0,
    )
    bounded_repair = RiskAssessment(
        Risk.NORMAL,
        0.82,
        0,
        {"transition_damage": 0.0},
        (),
        "PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=2,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0)).allocate(
        date=dates[-1],
        opportunity=Opportunity.STRONG_TREND,
        risk=bounded_repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert sum(target.weight for target in targets if target.weight > 0.0) == pytest.approx(0.82)
    assert max(target.weight for target in targets) <= DEFAULT_CONFIG.max_symbol_weight

def test_strategic_restore_settles_an_unexecutable_subthreshold_gap() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "micro_strategic_restore"
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=92.9,
        positions={
            symbol: Position(
                symbol,
                shares=71,
                avg_cost=0.1,
                entry_date=str(dates[-20].date()),
                highest_close=0.1,
            )
        },
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.08},
        strategic_restore_weights={symbol: 0.08},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))

    targets = allocator._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 0.1},
        weights_now={symbol: 0.071},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx({symbol: 0.08})
    assert account.strategic_restore_weights == {}

def test_strategic_restore_scales_only_to_the_explicit_risk_cap_until_normal():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("restore_a", "restore_b", "restore_c")
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(symbol, shares=30, avg_cost=1.0, highest_close=1.0) for symbol in symbols[:2]
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_restore_weights={symbol: 0.30 for symbol in symbols},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))
    common = {
        "date": dates[-1],
        "user_panel": {symbol: frame for symbol in symbols},
        "leaders": {symbol: _leader(symbol, 0.90) for symbol in symbols},
        "account": account,
        "prices": {symbol: 1.0 for symbol in symbols},
    }
    caution = RiskAssessment(
        Risk.CAUTION,
        0.60,
        1,
        {
            "freeze_new_risk": False,
            "transition_damage": (
                DEFAULT_CONFIG.transition_damage_repair
                + DEFAULT_CONFIG.strategic_damage_guard_transition
            )
            / 2.0,
            "operating_drawdown": 0.0,
            "capital_drawdown": 0.0,
        },
        (),
        "RECOVERY",
        freeze_new_risk=True,
    )

    partial = allocator.allocate(
        opportunity=Opportunity.TREND,
        risk=caution,
        **common,
    )
    assert {target.symbol: target.weight for target in partial or ()} == pytest.approx(
        {symbol: 0.20 for symbol in symbols}
    )
    assert account.strategic_restore_weights == {symbol: 0.30 for symbol in symbols}

    full = allocator.allocate(
        opportunity=Opportunity.TREND,
        risk=_normal_risk(),
        **common,
    )
    assert {target.symbol: target.weight for target in full or ()} == pytest.approx(
        {symbol: 0.30 for symbol in symbols}
    )
    assert account.strategic_restore_weights == {symbol: 0.30 for symbol in symbols}

def test_reason_clean_level2_normal_can_restore_a_durable_strategic_cohort_within_cap():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("held_a", "held_b", "missing_c")
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(symbol, shares=30, avg_cost=1.0, highest_close=1.0)
            for symbol in symbols[:2]
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_restore_weights={symbol: 0.30 for symbol in symbols},
        strategic_candidate_signature="strategic_qualification:reversal_industry:held_a,held_b,missing_c",
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        capital_budget_level=2,
        operating_peak=100.0,
        capital_peak=100.0,
    )
    bounded_repair = RiskAssessment(
        Risk.NORMAL,
        0.60,
        0,
        {"transition_damage": 0.0},
        (),
        "PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=2,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0)).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=bounded_repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx(
        {symbol: 0.20 for symbol in symbols}
    )
    assert account.strategic_restore_weights == {symbol: 0.30 for symbol in symbols}

def test_synchronized_restore_retires_missing_members_without_user_industry_breadth() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    held, missing_a, missing_b = "held_anchor", "missing_a", "missing_b"
    symbols = (held, missing_a, missing_b)
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={held: Position(held, shares=20, avg_cost=1.0, highest_close=1.0)},
        anchor_weights={symbol: 0.30 for symbol in symbols},
        protected_weights={symbol: 0.30 for symbol in symbols},
        recovery_anchor_date=str(dates[-30].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    repair = RiskAssessment(
        Risk.CAUTION,
        0.92,
        1,
        {"transition_damage": DEFAULT_CONFIG.transition_damage_repair},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    leaders = {
        held: _leader(held, 0.90, industry="optical"),
        missing_a: _leader(missing_a, 0.89, industry="optical"),
        missing_b: _leader(missing_b, 0.88, industry="memory"),
    }

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx({held: 0.20})
    assert account.anchor_weights == pytest.approx({held: 0.20})
    assert account.protected_weights == {}
    assert account.candidate_tenure["recovery_cohort_locked"] == 0

def test_single_industry_pool_does_not_require_impossible_external_industry_support() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    held, missing_a, missing_b = "held_anchor", "missing_a", "missing_b"
    symbols = (held, missing_a, missing_b)
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={held: Position(held, shares=20, avg_cost=1.0, highest_close=1.0)},
        anchor_weights={symbol: 0.30 for symbol in symbols},
        protected_weights={symbol: 0.30 for symbol in symbols},
        recovery_anchor_date=str(dates[-30].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    repair = RiskAssessment(
        Risk.CAUTION,
        0.92,
        1,
        {"transition_damage": DEFAULT_CONFIG.transition_damage_repair},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.01, industry="optical")
        for index, symbol in enumerate(symbols)
    }

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert set(weights) == set(symbols)
    assert all(weight > 0.0 for weight in weights.values())
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

def test_homogeneous_recovery_cohort_can_restore_with_unrelated_pool_industries() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    held, missing_a, missing_b = "held_anchor", "missing_a", "missing_b"
    anchors = (held, missing_a, missing_b)
    unrelated = ("compute_watch", "equipment_watch")
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={held: Position(held, shares=20, avg_cost=1.0, highest_close=1.0)},
        anchor_weights={symbol: 0.30 for symbol in anchors},
        protected_weights={symbol: 0.30 for symbol in anchors},
        recovery_anchor_date=str(dates[-30].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    repair = RiskAssessment(
        Risk.CAUTION,
        0.92,
        1,
        {"transition_damage": DEFAULT_CONFIG.transition_damage_repair},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    leaders = {
        **{
            symbol: _leader(symbol, 0.90 - index * 0.01, industry="optical")
            for index, symbol in enumerate(anchors)
        },
        unrelated[0]: _leader(unrelated[0], 0.87, industry="compute"),
        unrelated[1]: _leader(unrelated[1], 0.86, industry="equipment"),
    }

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=repair,
        user_panel={symbol: frame for symbol in (*anchors, *unrelated)},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in (*anchors, *unrelated)},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert set(weights) == set(anchors)
    assert all(weight > 0.0 for weight in weights.values())
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

def test_incomplete_strategic_sell_keeps_global_lifecycle_priority_on_recovery_cap():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    mixed, add2 = "strategic_mixed", "strategic_add2"
    account = AccountState(
        initial_cash=100.0,
        cash=30.0,
        positions={
            mixed: Position(
                mixed,
                shares=40,
                avg_cost=1.0,
                highest_close=1.0,
                tranches=[
                    Tranche(
                        "strategic_core",
                        Lifecycle.CORE.value,
                        20,
                        1.0,
                        "2026-01-01",
                        "2026-01-02",
                        1.0,
                    ),
                    Tranche(
                        "strategic_satellite",
                        Lifecycle.SATELLITE.value,
                        20,
                        1.0,
                        "2026-01-03",
                        "2026-01-04",
                        1.0,
                    ),
                ],
            ),
            add2: Position(
                add2,
                shares=30,
                avg_cost=1.0,
                highest_close=1.0,
                tranches=[
                    Tranche(
                        "strategic_add2_lot",
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
        strategic_cohort_symbols=[mixed, add2],
        strategic_cohort_targets={mixed: 0.40, add2: 0.30},
        strategic_restore_weights={mixed: 0.40, add2: 0.30},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.CAUTION,
        0.40,
        1,
        {"transition_damage": DEFAULT_CONFIG.transition_damage_repair},
        (),
        "RECOVERY",
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel={mixed: frame, add2: frame},
        leaders={mixed: _leader(mixed, 0.90), add2: _leader(add2, 0.89)},
        account=account,
        prices={mixed: 1.0, add2: 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx({mixed: 0.20, add2: 0.20})

def test_strategic_risk_capture_merges_members_without_losing_a_missing_restore():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbols = ("capture_a", "capture_b", "already_missing")
    account = AccountState(
        initial_cash=100.0,
        cash=20.0,
        positions={
            symbols[0]: Position(symbols[0], shares=50, avg_cost=1.0, highest_close=1.0),
            symbols[1]: Position(symbols[1], shares=30, avg_cost=1.0, highest_close=1.0),
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_restore_weights={symbols[2]: 0.30},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    blocked = RiskAssessment(
        Risk.RISK_OFF,
        0.60,
        4,
        {"transition_damage": 0.80},
        ("confirmed damage",),
        "NONE",
        freeze_new_risk=True,
    )

    PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=dates[-1],
        risk=blocked,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now={symbols[0]: 0.50, symbols[1]: 0.30},
    )

    assert account.strategic_restore_weights == pytest.approx(
        {symbols[0]: 0.4375, symbols[1]: 0.2625, symbols[2]: 0.30}
    )
    assert sum(account.strategic_restore_weights.values()) == pytest.approx(DEFAULT_CONFIG.max_gross)

def test_unrelated_protection_does_not_exempt_a_strategic_disaster_exit():
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 0.70
    symbol = "broken_strategic"
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={symbol: Position(symbol, shares=30, avg_cost=1.0, highest_close=1.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_restore_weights={symbol: 0.30},
        protected_weights={"unrelated": 0.20},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 0.70},
        weights_now={symbol: 0.21},
    )

    assert targets is not None
    assert {target.symbol: target.weight for target in targets} == {symbol: 0.0}
    assert account.strategic_cohort_targets == {}
    assert account.strategic_restore_weights == {}
    assert account.protected_weights == {"unrelated": 0.20}

def test_existing_strategic_exit_band_idempotently_cancels_recaptured_restore_rights():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    exiting, untouched = "exiting_member", "untouched_member"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            exiting: Position(exiting, shares=30, avg_cost=1.0, highest_close=1.0),
            untouched: Position(untouched, shares=30, avg_cost=1.0, highest_close=1.0),
        },
        strategic_cohort_symbols=[exiting, untouched],
        strategic_cohort_targets={exiting: 0.30, untouched: 0.30},
        strategic_exit_bands={exiting: [0.06] * 5},
        strategic_active_bands={exiting: [False] * 5},
        strategic_restore_weights={exiting: 0.30},
        protected_weights={exiting: 0.30, untouched: 0.30},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={exiting: frame, untouched: frame},
        leaders={exiting: _leader(exiting, 0.90), untouched: _leader(untouched, 0.89)},
        account=account,
        prices={exiting: 1.0, untouched: 1.0},
        weights_now={exiting: 0.30, untouched: 0.30},
    )

    assert exiting not in account.strategic_restore_weights
    assert exiting not in account.protected_weights
    assert account.protected_weights == {untouched: 0.30}

def test_started_strategic_member_without_durable_buy_intent_is_retired():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    symbol = "broker_liquidated_member"
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={},
    )

    assert targets == ()
    assert account.strategic_cohort_targets == {}

def test_crisis_liquidated_transition_impulse_member_cannot_reuse_old_restore_rights() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "liquidated_impulse_member"
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_restore_weights={symbol: 0.30},
        protected_weights={symbol: 0.30},
        strategic_candidate_signature=(
            "strategic_qualification:transition_impulse:liquidated_impulse_member:optical"
        ),
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=dates[-1],
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={},
    )

    assert targets == ()
    assert account.strategic_cohort_targets == {}
    assert account.strategic_restore_weights == {}
    assert account.protected_weights == {}

def test_transition_impulse_exits_once_when_every_atr_band_breaks() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "broken_impulse_member"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 1.0
    frame.loc[date, "ma20"] = 1.1
    frame.loc[date, "ret20"] = -0.10
    frame.loc[date, "atr"] = 0.05
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_candidate_signature=(
            "strategic_qualification:transition_impulse:broken_impulse_member:optical"
        ),
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert targets is not None
    assert {target.symbol: target.weight for target in targets} == {symbol: 0.0}
    assert account.strategic_cohort_targets == {}
