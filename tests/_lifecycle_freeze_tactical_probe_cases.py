from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _frozen_caution,
    _identity,
    _leader,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    merge_pending_orders,
    plan_orders,
)
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
)


def test_level_one_freeze_retains_partial_sell_and_cancels_partial_buy():
    symbol = "durable_direction"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    leader = _leader(symbol, 0.90)
    sell_identity = _identity(
        signal_date="2026-01-05",
        symbol=symbol,
        target_weight=0.30,
        lifecycle=Lifecycle.CORE.value,
        origin_subsystem=OriginSubsystem.RISK.value,
        mechanism=AttributionMechanism.RISK_GROSS_CAP.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )

    sell = PendingOrder(
        "2026-01-05",
        symbol,
        "SELL",
        0.30,
        "portfolio risk gross cap",
        Lifecycle.CORE.value,
        remaining_shares=300_000,
        attempts=1,
        order_id="O000000001",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
        **sell_identity,
    )
    selling = AccountState(
        initial_cash=1_000_000.0,
        cash=400_000.0,
        positions={symbol: Position(symbol, shares=600_000, avg_cost=1.0)},
        pending_orders=[sell],
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    sell_targets = allocator.allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: leader},
        account=selling,
        prices={symbol: 1.0},
    )
    assert sell_targets == (
        Target(
            symbol,
            0.30,
            Lifecycle.CORE.value,
            0.0,
            0.0,
            "portfolio risk gross cap",
            reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
            reason_code="risk_gross_cap",
            exit_kind="risk",
            **sell_identity,
        ),
    )
    replanned_sells = plan_orders(
        signal_date="2026-01-06",
        targets=sell_targets,
        account=selling,
        prices={symbol: 1.0},
        cfg=DEFAULT_CONFIG,
    )
    merged_sells = merge_pending_orders(
        retained=list(selling.pending_orders),
        planned=replanned_sells,
        targets=sell_targets,
    )
    assert merged_sells == (sell,)
    assert merged_sells[0].order_id == "O000000001"
    assert merged_sells[0].remaining_shares == 300_000

    buy = PendingOrder(
        "2026-01-05",
        symbol,
        "BUY",
        0.60,
        "leader add",
        Lifecycle.CORE.value,
        remaining_shares=400_000,
        attempts=1,
        order_id="O000000002",
    )
    buying = AccountState(
        initial_cash=1_000_000.0,
        cash=800_000.0,
        positions={symbol: Position(symbol, shares=200_000, avg_cost=1.0)},
        pending_orders=[buy],
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    buy_targets = allocator.allocate(
        date=pd.Timestamp("2026-01-06"),
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: leader},
        account=buying,
        prices={symbol: 1.0},
    )
    assert buy_targets[0].weight == pytest.approx(0.20)
    assert buy_targets[0].reason_code == "risk_freeze_hold"
    replanned_buys = plan_orders(
        signal_date="2026-01-06",
        targets=buy_targets,
        account=buying,
        prices={symbol: 1.0},
        cfg=DEFAULT_CONFIG,
    )
    assert replanned_buys == ()
    assert (
        merge_pending_orders(
            retained=list(buying.pending_orders),
            planned=replanned_buys,
            targets=buy_targets,
        )
        == ()
    )

def test_freeze_overlay_keeps_structural_sell_and_drops_replacement_buy() -> None:
    exiting, replacement = "broken_anchor", "replacement_anchor"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=700_000.0,
        positions={exiting: Position(exiting, shares=300_000, avg_cost=1.0)},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    structural_sell = Target(
        exiting,
        0.0,
        Lifecycle.RECOVERY.value,
        0.40,
        0.80,
        "recovery anchor exit: confirmed structural break",
        reason_code="recovery_exit",
        exit_kind="lifecycle",
        **_identity(
            signal_date="2026-01-06",
            symbol=exiting,
            target_weight=0.0,
            lifecycle=Lifecycle.RECOVERY.value,
            origin_subsystem=OriginSubsystem.RECOVERY.value,
            mechanism=AttributionMechanism.TACTICAL_REBOUND.value,
            reason_code="recovery_exit",
            exit_kind="lifecycle",
        ),
    )
    proposed_buy = Target(
        replacement,
        0.30,
        Lifecycle.RECOVERY.value,
        0.90,
        0.95,
        "recovery anchor entry: confirmed replacement",
    )

    frozen = PortfolioAllocator(DEFAULT_CONFIG)._frozen_existing_targets(
        strategy_targets=(structural_sell, proposed_buy),
        leaders={
            exiting: _leader(exiting, 0.40),
            replacement: _leader(replacement, 0.90),
        },
        account=account,
        weights_now={exiting: 0.30},
    )

    assert frozen == (structural_sell,)
    orders = plan_orders(
        signal_date="2026-01-06",
        targets=frozen,
        account=account,
        prices={exiting: 1.0, replacement: 1.0},
        cfg=DEFAULT_CONFIG,
    )
    assert [order.side for order in orders] == ["SELL"]

def test_normal_freeze_holds_exposure_and_risk_off_enforces_its_nonzero_cap():
    symbol = "held_leader"
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0)},
        active_leaders=[symbol],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    frozen_normal = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"freeze_new_risk": True},
        (),
        "NONE",
        freeze_new_risk=True,
    )

    held = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen_normal,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )
    assert {target.symbol: target.weight for target in held} == pytest.approx({symbol: 0.60})
    assert held[0].reason_code == "risk_freeze_hold"

    risk_off = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=RiskAssessment(Risk.RISK_OFF, 0.50, 3, {}, (), "NONE"),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )
    assert {target.symbol: target.weight for target in risk_off} == pytest.approx({symbol: 0.50})
    assert risk_off[0].reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert risk_off[0].reason_code == "risk_off"
    assert risk_off[0].exit_kind == "risk_off"

def test_caution_freeze_keeps_healthy_recovery_holding_within_hard_name_cap():
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "recovery_anchor"
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=60,
                avg_cost=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        anchor_weights={symbol: 0.60},
        recovery_anchor_date=str(dates[-2].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {"freeze_new_risk": True},
        (),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.60}
    )
    # The shared book keeps a healthy position within today's hard name cap;
    # a retired recovery-owner budget cannot impose another forced reduction.
    assert targets[0].reason_code == "risk_freeze_hold"
    assert account.cash == 40.0
    assert account.positions[symbol].shares == 60

def test_empty_book_freeze_cannot_open_a_tactical_probe() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_rebound_candidate"
    frame = _trend_frame(dates, ret20=-0.10, ret60=-0.40)
    frame["ret120"] = -0.40
    frozen = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {"broad_ret120": -0.10, "tech_ret120": 0.04},
        (),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=frozen,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert targets == ()
    assert account.candidate_tenure.get("tactical_active", 0) == 0

    restoring = AccountState.empty(100.0)
    restoring.protected_weights = {symbol: 0.60}
    restore_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=frozen,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=restoring,
        prices={symbol: 1.0},
    )
    assert restore_targets == ()
    assert restoring.protected_weights == {symbol: 0.60}

def test_capital_clean_caution_does_not_bypass_a_buy_freeze() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "bounded_rebound"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.03,
            "ret20": -0.20,
            "ret60": -0.30,
            "ret120": 0.15,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.10,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.WEAK,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.00},
    )

    # A rebound profile cannot replace an explicit risk-owned reentry grant.
    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_shallow_empty_book_rebound_does_not_justify_a_full_tactical_probe() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "shallow_rebound"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.03,
            "ret20": -0.18,
            "ret60": -0.30,
            "ret120": 0.15,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.10,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.WEAK,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert targets == ()

def test_independent_rebound_breadth_cannot_expand_a_frozen_account() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("shallow_design", "shallow_compute", "shallow_equipment")
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.03,
            "ret20": -0.18,
            "ret60": -0.30,
            "ret120": 0.15,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.10,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    industries = ("design", "compute", "equipment")
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.01, industry=industry)
        for index, (symbol, industry) in enumerate(
            zip(symbols, industries, strict=True)
        )
    }

    account = AccountState.empty(100.0)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.WEAK,
        risk=caution,
        user_panel={symbol: frame for symbol in symbols},
        leaders=leaders,
        account=account,
        prices={symbol: 1.00 for symbol in symbols},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_oversold_rebound_evidence_cannot_expand_a_frozen_account() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "still_oversold"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.10,
            "ret20": -0.18,
            "ret60": 0.25,
            "ret120": 0.50,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.40,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    account = AccountState.empty(100.0)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.61)},
        account=account,
        prices={symbol: 1.00},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_oversold_shallow_rebound_needs_medium_term_convexity() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "flat_oversold"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.10,
            "ret20": -0.18,
            "ret60": 0.19,
            "ret120": 0.60,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.40,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.61)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert targets == ()

def test_modest_extension_does_not_authorize_frozen_recovery_entry() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "oversold_base"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.08,
            "ret20": -0.16,
            "ret60": -0.02,
            "ret120": 0.16,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": -0.03,
            "tech_ret120": 0.06,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    account = AccountState.empty(100.0)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.63)},
        account=account,
        prices={symbol: 1.00},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_deep_tactical_rebound_needs_minimum_medium_term_convexity() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "weak_deep_pullback"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.04,
            "ret20": -0.21,
            "ret60": 0.05,
            "ret120": 0.30,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.40,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.61)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert targets == ()

def test_rebound_cooldown_metadata_cannot_clear_a_buy_freeze() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "overextended_pullback"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": -0.16,
            "ret20": -0.24,
            "ret60": 0.63,
            "ret120": 0.91,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": 0.10,
            "tech_ret120": 0.40,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.00},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}

    next_date = dates[-1] + pd.offsets.BDay()
    cooled = frame.copy()
    cooled.loc[next_date] = cooled.iloc[-1]
    cooled.loc[next_date, "ret120"] = 0.80
    cooled.loc[next_date, "ret20"] = -0.24
    cooled.loc[next_date, "ret5"] = -0.10
    targets = allocator.allocate(
        date=next_date,
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: cooled},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.00},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

def test_current_reversal_cannot_override_a_buy_freeze() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "current_reversal"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": 0.08,
            "ret20": -0.17,
            "ret60": 0.40,
            "ret120": 1.20,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": -0.03,
            "tech_ret120": 0.06,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    account = AccountState.empty(100.0)
    account.candidate_tenure.update(
        {"tactical_cooldown": 5, "tactical_overheat_cooldown": 1}
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.00},
    )

    assert targets == ()
    assert account.cash == 100.0
    assert account.positions == {}
    assert account.pending_orders == []

@pytest.mark.parametrize(
    ("ret5", "ret20", "ret120"),
    (
        (0.08, -0.17, 0.80),
        (0.08, -0.17, 1.20),
        (0.08, -0.24, 0.80),
        (0.01, -0.24, 0.80),
    ),
)
def test_low_quality_fast_reversal_does_not_open_an_empty_book(
    ret5: float,
    ret20: float,
    ret120: float,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "low_quality_reversal"
    close = np.linspace(0.80, 1.00, len(dates))
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 1.05,
            "ma60": 1.00,
            "ma120": 0.90,
            "ret5": ret5,
            "ret20": ret20,
            "ret60": 0.40,
            "ret120": ret120,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        4,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.80,
            "broad_ret120": -0.03,
            "tech_ret120": 0.06,
        },
        ("broad caution without a capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.70)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert targets == ()

def test_deep_and_shallow_crash_metadata_cannot_override_buy_freeze() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    close = np.ones(len(dates), dtype=float)
    close[-1] = 0.94
    deep = _trend_frame(dates, close=close, ret20=-0.10, ret60=-0.30)
    deep["ma120"] = 0.90
    deep["ret5"] = -0.05
    deep["ret120"] = -0.40
    shallow = deep.copy()
    shallow["ret20"] = -0.16
    shallow["ret120"] = -0.20
    caution_probe = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {
            "broad_ret120": 0.20,
            "tech_ret120": 0.20,
            "transition_damage": 0.47,
            "freeze_new_risk": False,
        },
        ("MA20 structural damage",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    deep_account = AccountState.empty(100.0)
    shallow_account = AccountState.empty(100.0)

    deep_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution_probe,
        user_panel={"deep": deep},
        leaders={"deep": _leader("deep", 0.90)},
        account=deep_account,
        prices={"deep": 0.94},
    )
    shallow_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution_probe,
        user_panel={"shallow": shallow},
        leaders={"shallow": _leader("shallow", 0.90)},
        account=shallow_account,
        prices={"shallow": 0.94},
    )

    assert deep_targets == ()
    assert shallow_targets == ()
    assert deep_account.cash == shallow_account.cash == 100.0
    assert deep_account.positions == shallow_account.positions == {}
    assert deep_account.pending_orders == shallow_account.pending_orders == []
