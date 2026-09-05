from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _DYNAMIC_ANCHOR_CANDIDATES,
    _leader,
    _reference_context,
    _risk_frame,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    plan_orders,
)
from uquant.leader import INDUSTRY
from uquant.portfolio import PortfolioAllocator
from uquant.risk import (
    REFERENCE_ANCHORS,
    _update_dynamic_anchors,
    assess_risk,
)
from uquant.types import (
    AccountState,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
)


def test_persistent_single_name_v_repair_is_a_fallback_not_a_fast_path_shortcut():
    dates = pd.bdate_range("2025-01-02", periods=80)
    date = dates[-1]
    market = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    protected = market.copy()
    protected.loc[dates[-2], "close"] = protected.loc[date, "close"]
    reference_panel, reference_leaders = _reference_context(market)

    def crisis_account() -> AccountState:
        return AccountState(
            initial_cash=200.0,
            cash=80.0,
            positions={
                "protected": Position("protected", 1, 120.0, str(dates[0].date()), 120.0),
            },
            protected_weights={"protected": 0.60},
            last_shock_date=str(dates[-20].date()),
            risk=Risk.CRISIS.value,
            shock_state="PERSISTENT_STRESS",
            shock_severity="SEVERE",
            shock_start_date=str(dates[-20].date()),
            risk_streaks={"persistent_v_market_repair": (DEFAULT_CONFIG.fast_v_recovery_confirm_days - 1)},
            operating_peak=300.0,
            capital_peak=200.0,
        )

    def assess(frame: pd.DataFrame, account: AccountState):
        return assess_risk(
            date=date,
            broad=market,
            tech=market,
            reference_panel=reference_panel,
            reference_returns=None,
            user_panel={"protected": frame},
            leaders=reference_leaders,
            account=account,
            equity=account.cash + account.positions["protected"].shares * frame.loc[date, "close"],
            cfg=DEFAULT_CONFIG,
        )

    fallback_account = crisis_account()
    fallback = assess(protected, fallback_account)
    assert fallback.state is Risk.CAUTION
    assert fallback.reasons == ("confirmed persistent V-recovery after extended single-name protection",)
    assert fallback_account.operating_peak == pytest.approx(200.0)

    # A positive one-day move is already advancing the ordinary fast-V streak;
    # the fallback must not use its own tenure to complete that route early.
    advancing = protected.copy()
    advancing.loc[date, "close"] = advancing.loc[dates[-2], "close"] * 1.01
    still_confirming = assess(advancing, crisis_account())
    assert still_confirming.state is Risk.CRISIS
    assert still_confirming.reasons == ("awaiting synchronized repair confirmation",)

def test_failed_restoration_triggers_capital_cooldown_and_retires_anchors():
    dates = pd.bdate_range("2025-01-02", periods=160)
    date = dates[-1]
    damaged = _risk_frame(dates, close=75.0, ma20=100.0, ret5=-0.10)
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    healthy["ret120"] = 0.10
    reference_panel, reference_leaders = _reference_context(healthy)
    symbols = ("failed_a", "failed_b", "failed_c")
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[-30].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        anchor_weights={symbol: 1.0 / 3.0 for symbol in symbols},
        protected_weights={symbol: 1.0 / 3.0 for symbol in symbols},
        last_shock_date=str(dates[-25].date()),
        risk=Risk.CAUTION.value,
        operating_peak=80.0,
        capital_peak=100.0,
        risk_events=[
            {
                "date": str(dates[-20].date()),
                "from": Risk.CRISIS.value,
                "to": Risk.CAUTION.value,
            }
        ],
    )
    assessment = assess_risk(
        date=date,
        broad=healthy,
        tech=healthy,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=account,
        equity=75.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.CRISIS
    assert assessment.target_gross_cap == pytest.approx(DEFAULT_CONFIG.market_crisis_gross)
    assert assessment.reasons == ("capital drawdown relapse in restored holdings",)
    assert account.candidate_tenure["capital_guard_cooldown"] == (DEFAULT_CONFIG.capital_guard_cooldown_days)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=assessment,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.80) for symbol in symbols},
        account=account,
        prices={symbol: 75.0 for symbol in symbols},
    )
    assert account.anchor_weights == {}
    assert account.protected_weights == {}
    assert sum(target.weight for target in targets) == pytest.approx(DEFAULT_CONFIG.market_crisis_gross)

def test_profitable_restore_drawdown_is_not_a_capital_failure() -> None:
    dates = pd.bdate_range("2025-01-02", periods=160)
    date = dates[-1]
    damaged = _risk_frame(dates, close=75.0, ma20=100.0, ret5=-0.10)
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    healthy["ret120"] = 0.10
    reference_panel, reference_leaders = _reference_context(healthy)
    symbols = ("profitable_a", "profitable_b", "profitable_c")
    account = AccountState(
        initial_cash=100.0,
        cash=75.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[-30].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        anchor_weights={symbol: 1.0 / 3.0 for symbol in symbols},
        protected_weights={symbol: 1.0 / 3.0 for symbol in symbols},
        last_shock_date=str(dates[-25].date()),
        risk=Risk.CAUTION.value,
        operating_peak=400.0,
        capital_peak=400.0,
        risk_events=[
            {
                "date": str(dates[-20].date()),
                "from": Risk.CRISIS.value,
                "to": Risk.CAUTION.value,
            }
        ],
    )

    assessment = assess_risk(
        date=date,
        broad=healthy,
        tech=healthy,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=account,
        equity=300.0,
        cfg=DEFAULT_CONFIG,
    )

    assert "capital drawdown relapse in restored holdings" not in assessment.reasons
    assert account.candidate_tenure.get("capital_guard_cooldown", 0) == 0

def test_profitable_restore_with_confirmed_market_damage_uses_ordinary_repair() -> None:
    dates = pd.bdate_range("2025-01-02", periods=160)
    date = dates[-1]
    damaged = _risk_frame(dates, close=75.0, ma20=100.0, ret5=-0.10)
    reference_panel, reference_leaders = _reference_context(damaged)
    symbols = ("profitable_a", "profitable_b", "profitable_c")
    account = AccountState(
        initial_cash=100.0,
        cash=75.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[-130].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        protected_weights={symbol: 1.0 / 3.0 for symbol in symbols},
        last_shock_date=str(dates[-100].date()),
        risk=Risk.CAUTION.value,
        operating_peak=330.0,
        capital_peak=400.0,
        risk_events=[
            {
                "date": str(dates[-20].date()),
                "from": Risk.CRISIS.value,
                "to": Risk.CAUTION.value,
            }
        ],
    )
    strategic_account = copy.deepcopy(account)
    strategic_account.candidate_tenure["strategic_cohort_active"] = 1
    anchored_account = copy.deepcopy(account)
    anchored_account.anchor_weights = {symbol: 1.0 / 3.0 for symbol in symbols}
    normalized_account = copy.deepcopy(account)
    normalized_account.risk = Risk.NORMAL.value
    unrearmed_account = copy.deepcopy(account)
    unrearmed_account.last_shock_date = str(dates[-10].date())
    unrearmed_account.risk_events.append(
        {
            "date": unrearmed_account.last_shock_date,
            "from": Risk.CAUTION.value,
            "to": Risk.CRISIS.value,
            "reasons": ["market-backed drawdown relapse in restored holdings"],
        }
    )
    severe_account = copy.deepcopy(account)
    severe_account.operating_peak = 400.0
    severely_damaged = _risk_frame(
        dates,
        close=75.0,
        ma20=100.0,
        ret5=DEFAULT_CONFIG.severe_shock_ret5 - 0.01,
    )
    severe_reference_panel, severe_reference_leaders = _reference_context(
        severely_damaged
    )

    assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=account,
        equity=300.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.CRISIS
    assert assessment.reasons == (
        "market-backed drawdown relapse in restored holdings",
    )
    assert account.candidate_tenure.get("capital_guard_cooldown", 0) == 0
    assert account.protected_weights

    normalized_assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=normalized_account,
        equity=300.0,
        cfg=DEFAULT_CONFIG,
    )

    assert "market-backed drawdown relapse in restored holdings" not in (
        normalized_assessment.reasons
    )
    assert normalized_account.candidate_tenure.get("capital_guard_cooldown", 0) == 0

    unrearmed_assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={symbol: damaged for symbol in symbols},
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=unrearmed_account,
        equity=300.0,
        cfg=DEFAULT_CONFIG,
    )

    assert "market-backed drawdown relapse in restored holdings" not in (
        unrearmed_assessment.reasons
    )
    assert unrearmed_account.candidate_tenure.get("capital_guard_cooldown", 0) == 0

    severe_assessment = assess_risk(
        date=date,
        broad=severely_damaged,
        tech=severely_damaged,
        reference_panel=severe_reference_panel,
        reference_returns=None,
        user_panel={symbol: severely_damaged for symbol in symbols},
        leaders={
            **severe_reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in symbols},
        },
        account=severe_account,
        equity=300.0,
        cfg=DEFAULT_CONFIG,
    )

    assert severe_assessment.reasons == (
        "market-backed portfolio break in incomplete restoration",
    )
    assert severe_account.candidate_tenure[
        "capital_guard_cooldown"
    ] == DEFAULT_CONFIG.capital_guard_cooldown_days

    for specialized_account in (strategic_account, anchored_account):
        specialized_assessment = assess_risk(
            date=date,
            broad=damaged,
            tech=damaged,
            reference_panel=reference_panel,
            reference_returns=None,
            user_panel={symbol: damaged for symbol in symbols},
            leaders={
                **reference_leaders,
                **{symbol: _leader(symbol, 0.80) for symbol in symbols},
            },
            account=specialized_account,
            equity=300.0,
            cfg=DEFAULT_CONFIG,
        )

        assert "market-backed drawdown relapse in restored holdings" not in (
            specialized_assessment.reasons
        )
        assert (
            specialized_account.candidate_tenure.get("capital_guard_cooldown", 0)
            == 0
        )

def test_profitable_market_backed_relapse_preserves_restoration_ownership() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    frame = _trend_frame(dates)
    symbols = ("restored_a", "restored_b")
    protected = {symbol: 0.30 for symbol in symbols}
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
            for symbol in symbols
        },
        protected_weights=dict(protected),
        candidate_tenure={"post_shock_restore_complete": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    guarded = RiskAssessment(
        Risk.CRISIS,
        DEFAULT_CONFIG.market_crisis_gross,
        4,
        {},
        ("market-backed drawdown relapse in restored holdings",),
        "CAPITAL_GUARD_COOLDOWN",
        freeze_new_risk=True,
        reduction_level=3,
        severity="SEVERE",
    )

    PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=guarded,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.protected_weights == protected
    assert account.candidate_tenure["post_shock_restore_complete"] == 1

@pytest.mark.parametrize(
    "reason",
    (
        "capital drawdown relapse in restored holdings",
        "market-backed portfolio break in incomplete restoration",
    ),
)
def test_failed_restoration_retires_strategic_restore_before_early_return(
    reason: str,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    frame = _trend_frame(dates)
    symbols = ("failed_strategic_a", "failed_strategic_b")
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
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_restore_weights={symbol: 0.30 for symbol in symbols},
        protected_weights={symbol: 0.30 for symbol in symbols},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    failed = RiskAssessment(
        Risk.CRISIS,
        DEFAULT_CONFIG.market_crisis_gross,
        4,
        {},
        (reason,),
        "CAPITAL_GUARD_COOLDOWN",
        freeze_new_risk=True,
        reduction_level=3,
        severity="SEVERE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=failed,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.strategic_restore_weights == {}
    assert account.protected_weights == {}
    assert account.strategic_cohort_targets == {}
    assert all(target.weight == 0.0 for target in targets)

def test_dynamic_risk_anchors_are_cross_industry_and_signature_confirmed():
    assert REFERENCE_ANCHORS == ()
    dates = pd.bdate_range("2025-10-01", periods=80)
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    _, leaders = _reference_context(healthy)
    account = AccountState.empty(100.0)

    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days - 1):
        assert (
            _update_dynamic_anchors(
                leaders=leaders,
                account=account,
                cfg=DEFAULT_CONFIG,
                allow_reanchor=True,
            )
            == ()
        )
    anchors = _update_dynamic_anchors(
        leaders=leaders,
        account=account,
        cfg=DEFAULT_CONFIG,
        allow_reanchor=True,
    )
    assert anchors == _DYNAMIC_ANCHOR_CANDIDATES
    assert len({INDUSTRY[symbol] for symbol in anchors}) == 3
    assert account.risk_anchor_signature == ",".join(anchors)
    assert account.risk_anchor_candidate_signature == ""
    assert account.risk_anchor_candidate_streak == 0

    replacements = ("sh603688", "sh603986", "sz002371")
    for offset, symbol in enumerate(replacements):
        leaders[symbol] = _leader(
            symbol,
            0.995 - 0.001 * offset,
            industry=INDUSTRY[symbol],
        )
    assert (
        _update_dynamic_anchors(
            leaders=leaders,
            account=account,
            cfg=DEFAULT_CONFIG,
            allow_reanchor=True,
        )
        == anchors
    )
    assert account.risk_anchor_candidate_streak == 1
    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days - 1):
        confirmed = _update_dynamic_anchors(
            leaders=leaders,
            account=account,
            cfg=DEFAULT_CONFIG,
            allow_reanchor=True,
        )
    assert confirmed == replacements
    assert account.risk_anchor_signature == ",".join(replacements)

def test_mature_recovery_cohort_breaks_on_persistent_market_backed_damage() -> None:
    dates = pd.bdate_range("2025-01-02", periods=160)
    date = dates[-1]
    damaged = _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10)
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    reference_panel, reference_leaders = _reference_context(damaged)
    held_symbols = ("damaged_a", "damaged_b", "healthy_c")
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[-120].date()),
                highest_close=120.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
            for symbol in held_symbols
        },
        anchor_weights={symbol: 0.30 for symbol in held_symbols},
        recovery_anchor_date=str(dates[-120].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        risk_streaks={
            "market_backed_recovery_break": (
                DEFAULT_CONFIG.concentrated_break_confirm_days - 1
            ),
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={
            held_symbols[0]: damaged,
            held_symbols[1]: damaged,
            held_symbols[2]: healthy,
        },
        leaders={
            **reference_leaders,
            held_symbols[0]: _leader(held_symbols[0], 0.80, industry="optical"),
            held_symbols[1]: _leader(held_symbols[1], 0.80, industry="equipment"),
            held_symbols[2]: _leader(held_symbols[2], 0.80, industry="materials"),
        },
        account=account,
        equity=90.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.CRISIS
    assert assessment.target_gross_cap == pytest.approx(DEFAULT_CONFIG.concentrated_crisis_gross)
    assert assessment.reasons == ("confirmed dynamic cohort structural break",)
    assert assessment.evidence["held_damage_ratio"] == pytest.approx(2.0 / 3.0)

def test_confirmed_caution_freezes_new_risk_without_creating_a_sell_order():
    dates = pd.bdate_range("2025-10-01", periods=80)
    date = dates[-1]
    damaged = _risk_frame(dates, close=85.0, ma20=100.0, ret5=-0.08)
    reference_panel, reference_leaders = _reference_context(damaged)
    account = AccountState.empty(100.0)
    account.risk_streaks["risk_caution"] = DEFAULT_CONFIG.caution_confirm_days - 1

    assessment = assess_risk(
        date=date,
        broad=damaged,
        tech=damaged,
        reference_panel=reference_panel,
        reference_returns=None,
        user_panel={},
        leaders=reference_leaders,
        account=account,
        equity=100.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.CAUTION
    assert assessment.votes >= 3
    assert assessment.target_gross_cap == pytest.approx(DEFAULT_CONFIG.max_gross)
    assert assessment.freeze_new_risk

    healthy = _trend_frame(dates)
    invested = AccountState(
        initial_cash=100.0,
        cash=45.0,
        positions={
            "held": Position(
                "held",
                shares=55,
                avg_cost=0.80,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        active_leaders=["held"],
        dynamic_k=1,
        operating_peak=100.0,
        capital_peak=100.0,
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.TREND,
        risk=assessment,
        user_panel={"held": healthy},
        leaders={"held": _leader("held", 0.85)},
        account=invested,
        prices={"held": 1.0},
    )
    assert {target.symbol: target.weight for target in targets} == pytest.approx({"held": 0.55})
    assert (
        plan_orders(
            signal_date=str(date.date()),
            targets=targets,
            account=invested,
            prices={"held": 1.0},
            cfg=DEFAULT_CONFIG,
        )
        == ()
    )

def test_confirmed_structural_exit_remains_executable_through_a_caution_freeze() -> None:
    dates = pd.bdate_range("2025-10-01", periods=40)
    frame = _trend_frame(
        dates, close=np.linspace(1.0, 0.80, len(dates)), ma20=1.0, ma60=1.05,
        ret20=-0.20, ret60=-0.20,
    )
    symbol = "rebound"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=60,
                avg_cost=1.0,
                entry_date=str(dates[-20].date()),
                highest_close=1.35,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        tactical_anchor_symbol=symbol,
        candidate_tenure={"tactical_active": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        2,
        {"freeze_new_risk": True, "transition_damage": 0.60},
        ("confirmed caution",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for observed in dates[-DEFAULT_CONFIG.replacement_confirm_days :]:
        targets = allocator.allocate(
            date=observed,
            opportunity=Opportunity.CHOPPY,
            risk=caution,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, 0.80, mature=False)},
            account=account,
            prices={symbol: float(frame.loc[observed, "close"])},
        )

    assert next(target for target in targets if target.symbol == symbol).weight == 0.0
    assert account.candidate_tenure["tactical_active"] == 0
    # Confirmed structural exit remains available through a buy freeze;
    # the target itself does not settle shares or credit expected sale proceeds.
    assert account.positions[symbol].shares == 60
    assert account.cash == 40.0

def test_unprofitable_tactical_time_expiry_waits_for_a_caution_freeze_to_clear() -> None:
    dates = pd.bdate_range("2025-10-01", periods=40)
    date = dates[-1]
    frame = _trend_frame(dates)
    symbol = "unprofitable_rebound"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=60,
                avg_cost=1.0,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        tactical_anchor_symbol=symbol,
        candidate_tenure={"tactical_active": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        2,
        {"freeze_new_risk": True, "transition_damage": 0.60},
        ("confirmed caution",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.80)},
        account=account,
        prices={symbol: 1.0},
    )

    assert not any(target.symbol == symbol and target.weight == 0.0 for target in targets)
    assert account.candidate_tenure["tactical_active"] == 1
    assert account.candidate_tenure.get("recovery_cycle_rearm_pending", 0) == 0

def test_strategic_cohort_has_no_immunity_from_a_confirmed_severe_cap():
    dates = pd.bdate_range("2025-10-01", periods=80)
    date = dates[-1]
    frame = _risk_frame(dates, close=90.0, ma20=100.0, ret5=-0.10)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            "arbitrary_strategic": Position(
                "arbitrary_strategic",
                shares=60,
                avg_cost=1.0,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
            )
        },
        strategic_cohort_symbols=["arbitrary_strategic"],
        strategic_cohort_targets={"arbitrary_strategic": 0.60},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_days": 30,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    severe = RiskAssessment(
        Risk.CRISIS,
        DEFAULT_CONFIG.severe_crisis_gross,
        5,
        {},
        ("confirmed cohort break",),
        "PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=3,
        severity="SEVERE",
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=severe,
        user_panel={"arbitrary_strategic": frame},
        leaders={"arbitrary_strategic": _leader("arbitrary_strategic", 0.90)},
        account=account,
        prices={"arbitrary_strategic": 1.0},
    )
    strategic = next(target for target in targets if target.symbol == "arbitrary_strategic")
    assert strategic.weight == pytest.approx(DEFAULT_CONFIG.severe_crisis_gross)
    assert strategic.reduction_policy == "RISK_PRIORITY"
    assert strategic.reason_code == "crisis"
    assert strategic.exit_kind == "crisis"

def test_narrow_market_two_of_three_anchor_damage_applies_graded_guard():
    dates = pd.bdate_range("2026-01-02", periods=160)
    date = dates[-1]
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    damaged = _risk_frame(dates, close=80.0, ma20=100.0, ret5=-0.10)
    broad = healthy.copy()
    tech = healthy.copy()
    broad["ret120"] = 0.05
    tech["ret120"] = 0.65
    reference_panel, reference_leaders = _reference_context(healthy)
    held_symbols = ("held1", "held2", "held3")
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={symbol: Position(symbol, shares=1, avg_cost=100.0) for symbol in held_symbols},
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
        leaders={
            **reference_leaders,
            **{symbol: _leader(symbol, 0.80) for symbol in held_symbols},
        },
        account=account,
        equity=90.0,
        cfg=DEFAULT_CONFIG,
    )

    assert assessment.state is Risk.RISK_OFF
    assert assessment.target_gross_cap == pytest.approx(DEFAULT_CONFIG.narrow_anchor_guard_gross)
    assert "narrow-market concentrated anchor damage" in assessment.reasons
