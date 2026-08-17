from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.leader import INDUSTRY, REFERENCE_UNIVERSE, credible_recovery_reserve
from uquant.portfolio import PortfolioAllocator
from uquant.risk import (
    REFERENCE_ANCHORS,
    _persistent_crisis_cap,
    _portfolio_drawdowns,
    _update_dynamic_anchors,
    assess_risk,
)
from uquant.types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


def _identity(
    *,
    signal_date: str,
    symbol: str,
    target_weight: float,
    lifecycle: str,
    origin_subsystem: str,
    mechanism: str,
    reduction_policy: str = ReductionPolicy.FIFO.value,
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
) -> dict[str, str | None]:
    industry = default_ai_universe().industry_of(symbol, signal_date)
    if industry == "unknown":
        industry = "optical"
    return {
        "event_id": derive_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=target_weight,
            lifecycle=lifecycle,
            origin_lifecycle=lifecycle,
            origin_subsystem=origin_subsystem,
            mechanism=mechanism,
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
            reduction_policy=reduction_policy,
            reason_code=reason_code,
            exit_kind=exit_kind,
        ),
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }


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
        components={
            "secular_score": score,
            "secular_confidence": 0.95,
            "industry_inference_confidence": 0.95,
            "unknown_industry": 0.0,
            "momentum60": 0.90,
            "momentum120": 0.90,
            "relative_strength": 0.90,
            "short_relative_strength": 0.90,
            "trend_persistence": 1.0,
            "breakout_quality": 0.90,
            "acceleration": 0.90,
            "industry_rotation_strength": 0.90,
        },
    )


def _normal_risk() -> RiskAssessment:
    return RiskAssessment(Risk.NORMAL, 1.0, 0, {"tech_ret120": 0.0}, (), "NONE")


def _frozen_caution() -> RiskAssessment:
    return RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {"transition_damage": 0.20},
        ("capital budget freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )


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


_DYNAMIC_ANCHOR_CANDIDATES = ("sz300308", "sh688008", "sh688012")


def _reference_context(
    frame: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, LeaderScore]]:
    """Return broad, multi-industry reference coverage with deterministic leaders."""
    scores = {symbol: 0.99 - 0.01 * index for index, symbol in enumerate(_DYNAMIC_ANCHOR_CANDIDATES)}
    panel = {symbol: frame.copy() for symbol in REFERENCE_UNIVERSE}
    leaders = {
        symbol: _leader(
            symbol,
            scores.get(symbol, 0.70),
            industry=INDUSTRY[symbol],
        )
        for symbol in REFERENCE_UNIVERSE
    }
    return panel, leaders


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
    assert _persistent_crisis_cap("COHORT_BREAK", DEFAULT_CONFIG) == pytest.approx(
        DEFAULT_CONFIG.concentrated_crisis_gross
    )
    assert _persistent_crisis_cap(
        "COHORT_BREAK",
        DEFAULT_CONFIG,
        reserve_backed=True,
    ) == pytest.approx(DEFAULT_CONFIG.risk_off_gross)
    assert _persistent_crisis_cap("INCOMPLETE_UNIVERSE_UNBACKED", DEFAULT_CONFIG) == pytest.approx(0.0)
    assert _persistent_crisis_cap("SEVERE", DEFAULT_CONFIG) == pytest.approx(
        DEFAULT_CONFIG.severe_crisis_gross
    )
    assert _persistent_crisis_cap("CONCENTRATED", DEFAULT_CONFIG) == pytest.approx(
        DEFAULT_CONFIG.concentrated_crisis_gross
    )
    assert _persistent_crisis_cap("MARKET", DEFAULT_CONFIG) == pytest.approx(0.50)
    assert _persistent_crisis_cap("INCOMPLETE_UNIVERSE", DEFAULT_CONFIG) == pytest.approx(0.50)


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


def test_recovery_substitution_does_not_sell_an_incumbent_for_a_generic_challenger():
    dates = pd.bdate_range("2025-01-02", periods=150)
    healthy = _trend_frame(dates)
    broken = healthy.copy()
    broken.loc[dates[-3] :, "close"] = 0.70
    broken.loc[dates[-3] :, "ma20"] = 1.00
    broken.loc[dates[-3] :, "ret20"] = -0.20
    broken.loc[dates[-3] :, "ret60"] = -0.10
    reserve = healthy.copy()
    reserve["ret60"] = 0.20
    reserve["ret120"] = 0.30
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "arbitrary_lead": Position("arbitrary_lead", shares=60, avg_cost=0.8),
            "arbitrary_weak": Position("arbitrary_weak", shares=30, avg_cost=0.8),
        },
        anchor_weights={"arbitrary_lead": 0.60, "arbitrary_weak": 0.30},
        recovery_anchor_date=str(dates[0].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        "arbitrary_lead": _leader("arbitrary_lead", 0.82, industry="optical"),
        "arbitrary_weak": _leader("arbitrary_weak", 0.45, mature=False, industry="pcb"),
        "arbitrary_challenger": _leader("arbitrary_challenger", 0.90, industry="compute"),
    }
    panel = {
        "arbitrary_lead": healthy,
        "arbitrary_weak": broken,
        "arbitrary_challenger": reserve,
    }
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets = None

    for date in dates[-3:]:
        targets = allocator._recovery_anchor_substitution(
            date=date,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            weights_now={"arbitrary_lead": 0.60, "arbitrary_weak": 0.30},
            anchor_elapsed=20,
        )

    assert targets is None
    assert account.anchor_weights == pytest.approx({"arbitrary_lead": 0.60, "arbitrary_weak": 0.30})
    assert not account.replacement_events


def test_recovery_substitution_rejects_an_overextended_challenger():
    dates = pd.bdate_range("2025-01-02", periods=150)

    def run(ret20: float) -> tuple[tuple[Target, ...] | None, AccountState]:
        healthy = _trend_frame(dates)
        broken = _trend_frame(dates)
        broken.loc[dates[-3] :, "close"] = 0.70
        broken.loc[dates[-3] :, "ma20"] = 1.00
        broken.loc[dates[-3] :, "ret20"] = -0.20
        broken.loc[dates[-3] :, "ret60"] = -0.10
        challenger = _trend_frame(dates, ret20=ret20, ret60=0.40)
        challenger["ret120"] = 0.30
        account = AccountState(
            initial_cash=100.0,
            cash=10.0,
            positions={
                "lead": Position("lead", shares=60, avg_cost=0.8),
                "weak": Position("weak", shares=30, avg_cost=0.8),
            },
            anchor_weights={"lead": 0.60, "weak": 0.30},
            recovery_anchor_date=str(dates[0].date()),
            operating_peak=150.0,
            capital_peak=100.0,
        )
        leaders = {
            "lead": _leader("lead", 0.85, industry="optical"),
            "weak": _leader("weak", 0.40, mature=False, industry="equipment"),
            "challenger": _leader("challenger", 0.90, industry="material"),
        }
        leaders["weak"].components.update(
            {
                "industry_rotation_strength": 0.30,
                "industry_breadth20": 0.20,
                "industry_confidence": 1.0,
            }
        )
        leaders["challenger"].components.update(
            {
                "industry_rotation_strength": 0.85,
                "industry_breadth20": 1.0,
                "industry_confidence": 1.0,
            }
        )
        panel = {"lead": healthy, "weak": broken, "challenger": challenger}
        allocator = PortfolioAllocator(DEFAULT_CONFIG)
        targets = None
        for date in dates[-DEFAULT_CONFIG.replacement_confirm_days :]:
            targets = allocator._recovery_anchor_substitution(
                date=date,
                risk=_normal_risk(),
                user_panel=panel,
                leaders=leaders,
                account=account,
                weights_now={"lead": 0.60, "weak": 0.30},
                anchor_elapsed=DEFAULT_CONFIG.recovery_add_window_days + 1,
            )
        return targets, account

    rejected, rejected_account = run(DEFAULT_CONFIG.recovery_substitution_max_ret20 + 1e-6)
    assert rejected is None
    assert rejected_account.anchor_weights == pytest.approx({"lead": 0.60, "weak": 0.30})
    assert not rejected_account.replacement_events

    admitted, admitted_account = run(DEFAULT_CONFIG.recovery_substitution_max_ret20)
    assert admitted is not None
    assert admitted_account.replacement_events[-1]["new_symbol"] == "challenger"
    assert admitted_account.anchor_weights == pytest.approx({"lead": 0.60, "challenger": 0.30})
    replacement = next(target for target in admitted if target.symbol == "challenger")
    assert replacement.origin_subsystem == OriginSubsystem.RECOVERY.value
    assert replacement.mechanism == AttributionMechanism.RECOVERY_SUBSTITUTION.value
    assert replacement.replaces_symbol == "weak"


def test_recovery_substitution_respects_transfer_cap_and_retains_lead_drift():
    dates = pd.bdate_range("2025-01-02", periods=150)
    healthy = _trend_frame(dates)
    broken = healthy.copy()
    broken.loc[dates[-3] :, "close"] = 0.70
    broken.loc[dates[-3] :, "ma20"] = 1.00
    broken.loc[dates[-3] :, "ret20"] = -0.20
    broken.loc[dates[-3] :, "ret60"] = -0.10
    reserve = _trend_frame(dates, ret20=0.10, ret60=0.40)
    reserve["ret120"] = 0.30
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "lead": Position("lead", shares=65, avg_cost=0.8),
            "weak": Position("weak", shares=25, avg_cost=0.8),
        },
        anchor_weights={"lead": 0.60, "weak": 0.25},
        recovery_anchor_date=str(dates[0].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        "lead": _leader("lead", 0.85, industry="optical"),
        "weak": _leader("weak", 0.40, mature=False, industry="equipment"),
        "challenger": _leader("challenger", 0.90, industry="material"),
    }
    leaders["weak"].components.update(
        {
            "industry_rotation_strength": 0.30,
            "industry_breadth20": 0.20,
            "industry_confidence": 1.0,
        }
    )
    leaders["challenger"].components.update(
        {
            "industry_rotation_strength": 0.85,
            "industry_breadth20": 1.0,
            "industry_confidence": 1.0,
        }
    )
    allocator = PortfolioAllocator(
        DEFAULT_CONFIG.override(replacement_transfer_cap=0.10)
    )
    targets = None
    for date in dates[-DEFAULT_CONFIG.replacement_confirm_days :]:
        targets = allocator._recovery_anchor_substitution(
            date=date,
            risk=_normal_risk(),
            user_panel={"lead": healthy, "weak": broken, "challenger": reserve},
            leaders=leaders,
            account=account,
            weights_now={"lead": 0.65, "weak": 0.25},
            anchor_elapsed=DEFAULT_CONFIG.recovery_add_window_days + 1,
            risk_neutral_only=True,
        )

    assert targets is not None
    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {"lead": 0.65, "weak": 0.0, "challenger": 0.10}
    )
    assert account.anchor_weights == pytest.approx(
        {"lead": 0.60, "challenger": 0.10}
    )


def test_config_rejects_an_invalid_unbacked_tail_threshold():
    with pytest.raises(ValueError, match="unbacked universe tail"):
        DEFAULT_CONFIG.override(unbacked_universe_tail_dd=DEFAULT_CONFIG.operating_dd_caution)


def _dynamic_cohort_inputs(
    dates: pd.DatetimeIndex,
) -> tuple[dict[str, pd.DataFrame], dict[str, LeaderScore]]:
    frame = _strategic_frame(dates)
    close = np.linspace(1.0, 5.0, len(dates))
    frame["close"] = close
    frame["ma20"] = close * 0.95
    frame["ma60"] = close * 0.85
    scores_and_groups = {
        "arbitrary_optical": (0.96, "optical"),
        "arbitrary_compute": (0.94, "compute"),
        "arbitrary_equipment": (0.92, "equipment"),
        "arbitrary_second_optical": (0.90, "optical"),
    }
    return (
        {symbol: frame.copy() for symbol in scores_and_groups},
        {
            symbol: _leader(symbol, score, industry=industry)
            for symbol, (score, industry) in scores_and_groups.items()
        },
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

    assert account.strategic_epoch == 1
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
    assert account.candidate_tenure["strategic_dominant_epoch"] == account.strategic_epoch


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

    assert account.strategic_epoch == 1
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

    for _ in range(DEFAULT_CONFIG.strategic_cohort_confirm_days):
        targets = allocator.allocate(
            date=date,
            opportunity=Opportunity.CHOPPY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
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
    assert account.strategic_epoch == 1
    assert {target.symbol for target in targets if target.weight > 0} == set(account.strategic_cohort_symbols)


def test_recovery_regime_is_not_preempted_by_new_trailing_secular_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
    account = AccountState.empty(100.0)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    date = dates[-1]
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}

    for _ in range(DEFAULT_CONFIG.strategic_cohort_confirm_days):
        targets = allocator.allocate(
            date=date,
            opportunity=Opportunity.RECOVERY,
            risk=_normal_risk(),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
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
    assert account.strategic_epoch == 1
    assert sum(target.weight for target in targets) == pytest.approx(DEFAULT_CONFIG.max_gross)


def test_disjoint_recovery_anchor_hands_off_to_confirmed_secular_cohort() -> None:
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

    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )

    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert set(account.strategic_cohort_symbols) == {
        "arbitrary_optical",
        "arbitrary_compute",
        "arbitrary_equipment",
    }
    assert account.anchor_weights == {}
    assert account.recovery_anchor_date == ""
    assert account.candidate_tenure["strategic_deferred_to_recovery"] == 0


def test_locked_disjoint_recovery_anchor_defers_confirmed_secular_cohort() -> None:
    dates = pd.bdate_range("2023-01-02", periods=246)
    panel, leaders = _dynamic_cohort_inputs(dates)
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

    assert account.strategic_epoch == 0
    assert account.anchor_weights == {"old_anchor": 0.50}
    assert account.candidate_tenure["strategic_deferred_to_recovery"] == 1


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


def test_strategic_epoch_respects_risk_gate_and_session_cooldown():
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
    assert account.candidate_tenure["strategic_cohort_qualification"] == 0

    account.strategic_last_exit_date = str(dates[-50].date())
    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._initialize_strategic_cohort(
            date=date,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_normal_risk(),
        )
    assert account.strategic_epoch == 1
    assert account.candidate_tenure["strategic_cohort_active"] == 1


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
    assert account.strategic_epoch == 2
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


def test_rearmed_strategic_owner_handoff_stages_one_generic_leader():
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

    positive = [target for target in targets if target.weight > 0]
    assert len(positive) == 1
    assert positive[0].symbol == symbols[0]
    assert positive[0].weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert account.dynamic_k == 1
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 1
    assert account.candidate_tenure.get("leader_cycle_handoff_epoch", 0) == 1


def test_completed_strategic_epoch_cannot_repeat_staged_generic_handoff():
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
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 0
    assert account.candidate_tenure.get("leader_cycle_evidence", 0) == 1


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


def test_reason_clean_caution_freeze_still_applies_one_anchor_diversification_cap():
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
        {symbol: DEFAULT_CONFIG.one_anchor_gross_cap}
    )
    assert targets[0].reason == "under-diversified recovery cap"


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


def test_capital_clean_caution_can_reach_the_empty_book_rebound_filter() -> None:
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

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )
    assert account.candidate_tenure["tactical_active"] == 1


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


def test_independent_shallow_rebound_breadth_confirms_one_tactical_probe() -> None:
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.WEAK,
        risk=caution,
        user_panel={symbol: frame for symbol in symbols},
        leaders=leaders,
        account=AccountState.empty(100.0),
        prices={symbol: 1.00 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbols[0]: DEFAULT_CONFIG.tactical_probe_weight}
    )


def test_still_oversold_shallow_rebound_confirms_one_tactical_probe() -> None:
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.61)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )


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


def test_oversold_base_with_modest_long_horizon_extension_can_probe() -> None:
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.63)},
        account=AccountState.empty(100.0),
        prices={symbol: 1.00},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )


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


def test_long_horizon_blowoff_pullback_is_not_a_tactical_rebound() -> None:
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
    assert account.candidate_tenure["tactical_cooldown"] == (
        DEFAULT_CONFIG.tactical_overheat_cooldown_days
    )

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
    assert account.candidate_tenure["tactical_cooldown"] == (
        DEFAULT_CONFIG.tactical_overheat_cooldown_days - 1
    )


def test_overextended_pullback_with_confirmed_current_reversal_can_probe() -> None:
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

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )
    assert account.candidate_tenure["tactical_cooldown"] == 0
    assert account.candidate_tenure["tactical_overheat_cooldown"] == 0


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


def test_independent_deep_crash_probe_does_not_require_broad_market_weakness() -> None:
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

    deep_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution_probe,
        user_panel={"deep": deep},
        leaders={"deep": _leader("deep", 0.90)},
        account=AccountState.empty(100.0),
        prices={"deep": 0.94},
    )
    shallow_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=caution_probe,
        user_panel={"shallow": shallow},
        leaders={"shallow": _leader("shallow", 0.90)},
        account=AccountState.empty(100.0),
        prices={"shallow": 0.94},
    )

    assert {target.symbol: target.weight for target in deep_targets} == pytest.approx(
        {"deep": DEFAULT_CONFIG.tactical_probe_weight}
    )
    assert shallow_targets == ()


def test_recovery_member_signature_must_persist_before_new_buys() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "new_a")
    close = np.linspace(0.80, 1.00, len(dates))
    panel = {}
    for symbol in symbols:
        frame = _trend_frame(dates, close=close)
        frame["ret120"] = -0.40
        panel[symbol] = frame
    leaders = {symbol: _leader(symbol, 0.90 - 0.01 * index) for index, symbol in enumerate(symbols)}
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={"lead": Position("lead", shares=60, avg_cost=1.0)},
        anchor_weights={"lead": 0.60},
        recovery_anchor_date=str(dates[-3].date()),
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.20, "tech_ret120": -0.20},
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    first = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )
    second = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )
    third = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in first} == pytest.approx({"lead": 0.60})
    assert {target.symbol: target.weight for target in second} == pytest.approx({"lead": 0.60})
    assert account.anchor_weights == pytest.approx({"lead": 0.60, "new_a": 0.20})
    assert {target.symbol: target.weight for target in third} == pytest.approx(account.anchor_weights)


def test_three_member_expansion_preserves_the_confirmed_tactical_anchor() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "new_a", "new_b")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            "lead": Position(
                "lead",
                shares=60,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        anchor_weights={"lead": 0.60},
        recovery_anchor_date=str(dates[-3].date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
        },
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    expected = {"lead": 0.60, "new_a": 0.16, "new_b": 0.16}
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected)
    assert account.anchor_weights == pytest.approx(expected)
    assert account.candidate_tenure["recovery_cohort_locked"] == 1


def test_three_confirmed_recovery_members_share_the_full_locked_budget() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    # Exactly three confirmed members fill all available seats without
    # selection ambiguity while preserving the crash winner's conviction ratio.
    gross = min(DEFAULT_CONFIG.max_gross, risk.target_gross_cap)
    lead = min(
        DEFAULT_CONFIG.max_symbol_weight,
        DEFAULT_CONFIG.tactical_rebound_weight,
        gross,
    )
    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {
            symbols[0]: lead,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )
    assert account.anchor_weights == pytest.approx(
        {
            symbols[0]: lead,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )
    assert account.candidate_tenure["recovery_cohort_locked"] == 1

    partially_filled = AccountState(
        initial_cash=300.0,
        cash=200.0,
        positions={
            symbols[0]: Position(
                symbols[0],
                shares=100,
                avg_cost=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
        },
        anchor_weights=dict(account.anchor_weights),
        recovery_anchor_date=str(dates[-1].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=300.0,
        capital_peak=300.0,
    )
    resumed_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=partially_filled,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in resumed_targets} == pytest.approx(
        {
            symbols[0]: 1.0 / 3.0,
            symbols[1]: (gross - lead) / 2,
            symbols[2]: (gross - lead) / 2,
        }
    )

    caution = RiskAssessment(
        Risk.CAUTION,
        0.70,
        1,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        ("capital repair remains incomplete",),
        "RECOVERY",
    )
    caution_account = AccountState.empty(100.0)
    caution_account.capital_budget_level = 1
    caution_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=caution,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=caution_account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    caution_weights = {target.symbol: target.weight for target in caution_targets}
    assert sum(caution_weights.values()) <= caution.target_gross_cap + 1e-12
    assert max(caution_weights.values(), default=0.0) <= (
        DEFAULT_CONFIG.tactical_rebound_weight + 1e-12
    )


def test_unconfirmed_simultaneous_recovery_members_keep_one_tactical_owner() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": -0.05,
            "tech_ret60": -0.05,
            "broad_ret120": 0.12,
            "tech_ret120": 0.10,
            "risk_anchor_group_count": 0,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    expected = {
        "deepest": DEFAULT_CONFIG.tactical_rebound_weight,
        "second": 0.16,
        "third": 0.16,
    }
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected)
    assert account.anchor_weights == pytest.approx(expected)
    assert account.candidate_tenure["recovery_cohort_locked"] == 1


@pytest.mark.parametrize("reported_universe_size", (3, 30))
def test_recovery_cohort_size_ignores_unrelated_universe_members(
    reported_universe_size: int,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
            "configured_user_universe_size": reported_universe_size,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert sum(weights.values()) == pytest.approx(DEFAULT_CONFIG.max_gross)
    assert weights[symbols[0]] > weights[symbols[1]] == pytest.approx(weights[symbols[2]])


def test_ambiguous_recovery_candidates_bound_the_first_deployment() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("deepest", "second", "third", "fourth")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret60": 0.05,
            "tech_ret60": 0.05,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
            "risk_anchor_group_count": DEFAULT_CONFIG.strategic_cohort_min_size,
        },
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=AccountState.empty(100.0),
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert len(targets) == 3
    assert sum(target.weight for target in targets) == pytest.approx(
        DEFAULT_CONFIG.recovery_expansive_universe_gross
    )


def test_locked_recovery_cohort_keeps_an_unfinished_owner_buy_target() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "second", "third")
    panel: dict[str, pd.DataFrame] = {}
    for index, symbol in enumerate(symbols):
        frame = _trend_frame(dates, close=np.linspace(0.80, 1.00, len(dates)))
        frame["ret120"] = -0.40 + 0.02 * index
        panel[symbol] = frame
    account = AccountState(
        initial_cash=100.0,
        cash=38.0,
        positions={
            "lead": Position("lead", shares=30, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value),
            "second": Position(
                "second", shares=16, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
            ),
            "third": Position(
                "third", shares=16, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
            ),
        },
        pending_orders=[
            PendingOrder(
                str(dates[-2].date()),
                "lead",
                "BUY",
                DEFAULT_CONFIG.tactical_rebound_weight,
                "recovery cohort construction",
                Lifecycle.RECOVERY.value,
                remaining_shares=30,
                attempts=1,
            )
        ],
        anchor_weights={"lead": 0.60, "second": 0.16, "third": 0.16},
        recovery_anchor_date=str(dates[-2].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.12,
            "tech_ret120": 0.10,
            "risk_anchor_group_count": 0,
        },
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=risk,
        user_panel=panel,
        leaders={
            symbol: _leader(symbol, 0.90 - 0.01 * index)
            for index, symbol in enumerate(symbols)
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        account.anchor_weights
    )


@pytest.mark.parametrize("restored_after_shock", [False, True])
def test_confirmed_caution_can_execute_an_armed_recovery_winner_trail(
    restored_after_shock: bool,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("trail_me", "keep_a", "keep_b")
    frames = {
        symbol: _trend_frame(dates, close=np.linspace(0.80, price, len(dates)))
        for symbol, price in zip(symbols, (0.88, 0.95, 0.95), strict=True)
    }
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            "trail_me": Position(
                "trail_me",
                shares=30,
                avg_cost=0.70,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
            "keep_a": Position(
                "keep_a",
                shares=30,
                avg_cost=0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
            "keep_b": Position(
                "keep_b",
                shares=30,
                avg_cost=0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            ),
        },
        anchor_weights={symbol: 0.30 for symbol in symbols},
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        last_shock_date=(str(dates[-10].date()) if restored_after_shock else ""),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        1.0,
        5,
        {
            "freeze_new_risk": True,
            "transition_damage": 0.60,
            "held_damage_ratio": 2.0 / 3.0,
            "sector_stress_ratio": 0.80,
        },
        ("confirmed multi-industry damage",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=caution,
        user_panel=frames,
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert (weights["trail_me"] == 0.0) is (not restored_after_shock)
    assert weights["keep_a"] > 0.0
    assert weights["keep_b"] > 0.0
    assert ("trail_me" not in account.anchor_weights) is (not restored_after_shock)


def test_confirmed_hard_risk_can_only_exit_an_armed_recovery_winner() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("trail_me", "drift_exit", "keep_a", "keep_b")
    frames = {
        symbol: _trend_frame(dates, close=np.linspace(0.80, price, len(dates)))
        for symbol, price in zip(symbols, (0.88, 0.88, 0.95, 0.95), strict=True)
    }
    account = AccountState(
        initial_cash=100.0,
        cash=50.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.70 if symbol == "trail_me" else 0.75,
                entry_date=str(dates[0].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.RECOVERY.value,
            )
            for symbol in symbols
        },
        anchor_weights={
            "trail_me": 0.30,
            "drift_exit": 0.10,
            "keep_a": 0.30,
            "keep_b": 0.30,
        },
        protected_weights={
            "trail_me": 0.30,
            "drift_exit": 0.10,
            "keep_a": 0.30,
            "keep_b": 0.30,
        },
        recovery_anchor_date=str(dates[0].date()),
        candidate_tenure={"recovery_cohort_locked": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    hard_risk = RiskAssessment(
        Risk.RISK_OFF,
        0.80,
        3,
        {
            "freeze_new_risk": True,
            "held_damage_ratio": 1.0 / 3.0,
            "sector_stress_ratio": 0.10,
        },
        ("confirmed synchronized holdings shock",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=2,
    )

    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    first_observation = allocator.allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=hard_risk,
        user_panel=frames,
        leaders={
            symbol: _leader(
                symbol,
                0.90,
                industry="equipment" if symbol == "keep_b" else "optical",
            )
            for symbol in symbols
        },
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )
    first_weights = {target.symbol: target.weight for target in first_observation}
    assert first_weights["trail_me"] > 0.0
    assert first_weights["drift_exit"] > account.anchor_weights["drift_exit"]
    assert "trail_me" in account.anchor_weights
    assert "trail_me" in account.protected_weights
    assert "drift_exit" in account.anchor_weights
    assert "drift_exit" in account.protected_weights

    continuing_hard_risk = RiskAssessment(
        Risk.RISK_OFF,
        0.80,
        3,
        hard_risk.evidence,
        ("awaiting synchronized repair confirmation",),
        "PERSISTENT_STRESS",
        freeze_new_risk=True,
        reduction_level=2,
    )
    # A one-day member bounce does not erase yesterday's independently
    # observed trail break while the same hard portfolio risk persists.
    frames["trail_me"].loc[date, "close"] = 0.95
    frames["drift_exit"].loc[date, "close"] = 0.95
    targets = allocator.allocate(
        date=date,
        opportunity=Opportunity.WEAK,
        risk=continuing_hard_risk,
        user_panel=frames,
        leaders={
            symbol: _leader(
                symbol,
                0.90,
                industry="equipment" if symbol == "keep_b" else "optical",
            )
            for symbol in symbols
        },
        account=account,
        prices={symbol: float(frames[symbol].loc[date, "close"]) for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert weights["trail_me"] == 0.0
    assert weights["drift_exit"] == 0.0
    assert 0.0 < weights["keep_a"] <= account.anchor_weights["keep_a"]
    assert 0.0 < weights["keep_b"] <= account.anchor_weights["keep_b"]
    assert sum(weights.values()) <= hard_risk.target_gross_cap
    assert "trail_me" not in account.anchor_weights
    assert "trail_me" not in account.protected_weights
    assert "drift_exit" not in account.anchor_weights
    assert "drift_exit" not in account.protected_weights


def test_confirmed_level1_repair_reaches_the_bounded_empty_book_probe() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_repair_candidate"
    close = np.ones(len(dates), dtype=float)
    close[-1] = 0.94
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 0.90,
            "ma60": 0.90,
            "ma120": 0.90,
            "ret5": -0.05,
            "ret20": -0.10,
            "ret60": -0.30,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    frozen_repair = RiskAssessment(
        Risk.CAUTION,
        1.0,
        1,
        {
            "transition_damage": 0.20,
            "freeze_new_risk": True,
            "broad_ret120": -0.20,
            "tech_ret120": -0.20,
        },
        ("level-1 capital repair",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState.empty(100.0)
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 2
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 0.94},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_probe_weight}
    )
    assert targets[0].lifecycle == Lifecycle.RECOVERY.value
    assert account.candidate_tenure["tactical_active"] == 1

    generic_account = AccountState.empty(100.0)
    generic_account.capital_budget_level = 1
    generic_account.capital_budget_repair_streak = 2
    generic_targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=generic_account,
        prices={symbol: 0.94},
    )
    assert generic_targets == ()


def test_caution_frozen_empty_book_deep_recovery_new_high_is_independently_confirmed() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_new_high"
    close = np.full(len(dates), 0.80)
    close[-1] = 1.00
    frame = _trend_frame(dates, close=close, ma20=0.90, ret20=0.10, ret60=-0.20)
    frame["ret120"] = -0.35
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=RiskAssessment(
            Risk.CAUTION,
            1.0,
            1,
            {"broad_ret120": 0.05, "tech_ret120": 0.05},
            (),
            "NONE",
            freeze_new_risk=True,
            reduction_level=1,
        ),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.70)},
        account=account,
        prices={symbol: 1.00},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_rebound_weight}
    )
    assert account.anchor_weights == pytest.approx(
        {symbol: DEFAULT_CONFIG.tactical_rebound_weight}
    )


def test_level1_repair_without_candidate_retains_existing_generic_core() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "held_generic_core"
    frame = _trend_frame(dates)
    frozen_repair = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"transition_damage": 0.20, "freeze_new_risk": True},
        ("level-1 capital repair",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={
            symbol: Position(
                symbol,
                shares=20,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        active_leaders=[symbol],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 2

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.20}
    )
    assert targets[0].lifecycle == Lifecycle.CORE.value
    assert targets[0].reason_code == "risk_freeze_hold"


def test_first_level1_repair_step_reopens_only_explicit_protected_intent() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "saved_restore"
    frame = _trend_frame(dates)
    frozen_repair = RiskAssessment(
        Risk.CAUTION,
        0.60,
        1,
        {"transition_damage": 0.20, "freeze_new_risk": True},
        ("level-1 protected restoration",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=1,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    protected = AccountState.empty(100.0)
    protected.protected_weights = {symbol: 0.60}
    protected.capital_budget_level = 1
    protected.capital_budget_repair_streak = 1

    restored = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=frozen_repair,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=protected,
        prices={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in restored} == pytest.approx({symbol: 0.60})
    assert restored[0].reason == "confirmed post-shock restoration"

    no_intent = AccountState.empty(100.0)
    no_intent.capital_budget_level = 1
    no_intent.capital_budget_repair_streak = 1
    assert (
        allocator.allocate(
            date=dates[-1],
            opportunity=Opportunity.TREND,
            risk=frozen_repair,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, 0.90)},
            account=no_intent,
            prices={symbol: 1.0},
        )
        == ()
    )


def test_synchronized_crisis_repair_reopens_only_protected_weights() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbols = ("lead", "reserve_a", "reserve_b")
    frame = _trend_frame(dates)
    confirmed_repair = RiskAssessment(
        Risk.CAUTION,
        0.50,
        4,
        {"transition_damage": 0.80, "freeze_new_risk": True},
        ("two-day synchronized leader repair",),
        "RECOVERY",
        freeze_new_risk=True,
        reduction_level=2,
        severity="COHORT_BREAK",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=75.0,
        positions={
            "lead": Position(
                "lead",
                shares=25,
                avg_cost=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
        },
        protected_weights={"lead": 0.60, "reserve_a": 0.16, "reserve_b": 0.16},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=confirmed_repair,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    weights = {target.symbol: target.weight for target in targets}
    assert sum(weights.values()) == pytest.approx(confirmed_repair.target_gross_cap)
    assert set(weights) == set(symbols)
    assert weights["lead"] > 0.25
    assert all(target.reason == "confirmed post-shock restoration" for target in targets)
    assert account.candidate_tenure.get("post_shock_restore_submitted", 0) == 1

    account.positions = {
        "lead": Position("lead", shares=59, avg_cost=1.0, lifecycle=Lifecycle.CORE.value),
        "reserve_a": Position(
            "reserve_a", shares=15, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
        ),
        "reserve_b": Position(
            "reserve_b", shares=15, avg_cost=1.0, lifecycle=Lifecycle.RECOVERY.value
        ),
    }
    account.cash = 11.0
    account.pending_orders.clear()
    normal = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")
    settled = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.RECOVERY,
        risk=normal,
        user_panel={symbol: frame for symbol in symbols},
        leaders={symbol: _leader(symbol, 0.90) for symbol in symbols},
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert account.candidate_tenure["post_shock_restore_complete"] == 1
    assert {target.symbol: target.weight for target in settled} == pytest.approx(
        {"lead": 0.59, "reserve_a": 0.15, "reserve_b": 0.15}
    )
    assert {target.reason for target in settled} == {
        "completed post-shock restoration; retain price drift"
    }


@pytest.mark.parametrize(
    "frozen",
    (
        RiskAssessment(
            Risk.CAUTION,
            1.0,
            1,
            {"transition_damage": 0.20},
            ("level-1 capital repair",),
            "NONE",
            freeze_new_risk=True,
            reduction_level=1,
        ),
        RiskAssessment(
            Risk.NORMAL,
            1.0,
            1,
            {"transition_damage": 0.20, "freeze_new_risk": True},
            ("continuous transition damage",),
            "NONE",
            reduction_level=1,
        ),
        RiskAssessment(
            Risk.RISK_OFF,
            0.50,
            3,
            {"transition_damage": 0.20},
            ("risk-off state",),
            "NONE",
            reduction_level=2,
        ),
    ),
    ids=("field", "evidence", "state"),
)
def test_every_freeze_source_persistently_blocks_empty_book_buys(
    frozen: RiskAssessment,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "deep_repair_candidate"
    close = np.ones(len(dates), dtype=float)
    close[-1] = 0.94
    frame = pd.DataFrame(
        {
            "close": close,
            "ma20": 0.90,
            "ma60": 0.90,
            "ma120": 0.90,
            "ret5": -0.05,
            "ret20": -0.10,
            "ret60": -0.30,
            "ret120": -0.40,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    account = AccountState.empty(100.0)
    account.capital_budget_level = 1
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for _ in range(5):
        targets = allocator.allocate(
            date=dates[-1],
            opportunity=Opportunity.CHOPPY,
            risk=frozen,
            user_panel={symbol: frame},
            leaders={symbol: _leader(symbol, 0.90)},
            account=account,
            prices={symbol: 0.94},
        )

        assert targets == ()
        assert (
            plan_orders(
                signal_date=str(dates[-1].date()),
                targets=targets,
                account=account,
                prices={symbol: 0.94},
                cfg=DEFAULT_CONFIG,
            )
            == ()
        )
        assert account.candidate_tenure.get("tactical_active", 0) == 0


def test_frozen_strategic_member_preserves_partial_sell_identity_and_cancels_buy():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    selling, buying = "strategic_sell", "strategic_buy"
    durable_sell = PendingOrder(
        "2026-01-05",
        selling,
        "SELL",
        0.10,
        "portfolio risk gross cap",
        Lifecycle.CORE.value,
        remaining_shares=10,
        order_id="O000000101",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    unfinished_buy = PendingOrder(
        "2026-01-05",
        buying,
        "BUY",
        0.30,
        "strategic cohort entry",
        Lifecycle.CORE.value,
        remaining_shares=10,
        order_id="O000000102",
    )
    account = AccountState(
        initial_cash=100.0,
        cash=60.0,
        positions={
            selling: Position(selling, shares=20, avg_cost=1.0),
            buying: Position(buying, shares=20, avg_cost=1.0),
        },
        pending_orders=[durable_sell, unfinished_buy],
        strategic_cohort_symbols=[selling, buying],
        strategic_cohort_targets={selling: 0.30, buying: 0.30},
        candidate_tenure={"strategic_cohort_active": 1, "strategic_cohort_started": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    frozen = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"freeze_new_risk": True},
        (),
        "RECOVERY",
        freeze_new_risk=True,
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=frozen,
        user_panel={selling: frame, buying: frame},
        leaders={selling: _leader(selling, 0.90), buying: _leader(buying, 0.89)},
        account=account,
        prices={selling: 1.0, buying: 1.0},
    )
    by_symbol = {target.symbol: target for target in targets}
    assert by_symbol[selling] == Target(
        selling,
        0.10,
        Lifecycle.CORE.value,
        0.0,
        0.0,
        "portfolio risk gross cap",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
    )
    assert by_symbol[buying].weight == pytest.approx(0.20)
    assert by_symbol[buying].reason_code == "risk_freeze_hold"
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=targets,
        account=account,
        prices={selling: 1.0, buying: 1.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=list(account.pending_orders),
        planned=planned,
        targets=targets,
    )
    assert merged == (durable_sell,)
    assert merged[0].order_id == "O000000101"
    assert merged[0].remaining_shares == 10


def test_partial_fill_direction_survives_real_daily_execute_replan_cycle():
    symbol = "sz000001"
    dates = pd.to_datetime(("2026-01-05", "2026-01-06", "2026-01-07"))
    sell_frame = pd.DataFrame(
        {
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 20_000.0,
            "amount": 200_000.0,
        },
        index=dates,
    )
    sell_order = PendingOrder(
        "2026-01-05",
        symbol,
        "SELL",
        0.30,
        "portfolio risk gross cap",
        Lifecycle.CORE.value,
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
        reason_code="risk_gross_cap",
        exit_kind="risk",
        **_identity(
            signal_date="2026-01-05",
            symbol=symbol,
            target_weight=0.30,
            lifecycle=Lifecycle.CORE.value,
            origin_subsystem=OriginSubsystem.RISK.value,
            mechanism=AttributionMechanism.RISK_GROSS_CAP.value,
            reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
            reason_code="risk_gross_cap",
            exit_kind="risk",
        ),
    )
    selling = AccountState(
        initial_cash=10_000.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=1_000,
                avg_cost=10.0,
                entry_date="2026-01-02",
                highest_close=10.0,
                tranches=[
                    Tranche(
                        "core",
                        Lifecycle.CORE.value,
                        1_000,
                        10.0,
                        "2026-01-02",
                        "2026-01-05",
                        10.0,
                        lowest_close=10.0,
                    )
                ],
            )
        },
        pending_orders=[sell_order],
        operating_peak=10_000.0,
        capital_peak=10_000.0,
    )
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    first_sell = planner.execute_open(
        date=dates[1],
        account=selling,
        panel={symbol: sell_frame},
    )
    assert len(first_sell) == 1
    assert first_sell[0].shares == 100
    ledger = selling.order_ledger[0]
    assert (ledger.requested_shares, ledger.filled_shares, ledger.remaining_shares) == (
        700,
        100,
        600,
    )

    previous_sells = list(selling.pending_orders)
    sell_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[1],
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: _leader(symbol, 0.90)},
        account=selling,
        prices={symbol: 10.0},
    )
    planned_sells = plan_orders(
        signal_date="2026-01-06",
        targets=sell_targets,
        account=selling,
        prices={symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    current_sells = merge_pending_orders(
        retained=previous_sells,
        planned=planned_sells,
        targets=sell_targets,
    )
    selling.pending_orders = list(
        reconcile_account_orders(
            account=selling,
            previous=previous_sells,
            current=current_sells,
            submitted_date="2026-01-06",
        )
    )
    assert selling.pending_orders[0].order_id == first_sell[0].order_id

    second_sell = planner.execute_open(
        date=dates[2],
        account=selling,
        panel={symbol: sell_frame},
    )
    assert second_sell[0].order_id == first_sell[0].order_id
    assert second_sell[0].shares == 100
    assert (ledger.requested_shares, ledger.filled_shares, ledger.remaining_shares) == (
        700,
        200,
        500,
    )

    star = "sh688008"
    buy_frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100_000.0,
            "amount": 10_000_000.0,
        },
        index=dates,
    )
    buying = AccountState.empty(1_000_000.0)
    buying.pending_orders = [
        PendingOrder(
            "2026-01-05",
            star,
            "BUY",
            0.60,
            "leader add",
            Lifecycle.CORE.value,
            **_identity(
                signal_date="2026-01-05",
                symbol=star,
                target_weight=0.60,
                lifecycle=Lifecycle.CORE.value,
                origin_subsystem=OriginSubsystem.LEADER.value,
                mechanism=AttributionMechanism.LEADER_SELECTION.value,
            ),
        )
    ]
    buy_planner = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=0.002))
    first_buy = buy_planner.execute_open(
        date=dates[1],
        account=buying,
        panel={star: buy_frame},
    )
    assert first_buy[0].shares == 200
    buy_ledger = buying.order_ledger[0]
    assert (buy_ledger.requested_shares, buy_ledger.filled_shares, buy_ledger.remaining_shares) == (
        5_900,
        200,
        5_700,
    )

    previous_buys = list(buying.pending_orders)
    buy_targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[1],
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={star: _leader(star, 0.90)},
        account=buying,
        prices={star: 100.0},
    )
    planned_buys = plan_orders(
        signal_date="2026-01-06",
        targets=buy_targets,
        account=buying,
        prices={star: 100.0},
        cfg=DEFAULT_CONFIG,
    )
    current_buys = merge_pending_orders(
        retained=previous_buys,
        planned=planned_buys,
        targets=buy_targets,
    )
    buying.pending_orders = list(
        reconcile_account_orders(
            account=buying,
            previous=previous_buys,
            current=current_buys,
            submitted_date="2026-01-06",
        )
    )
    assert buying.pending_orders == []
    assert buy_ledger.status == "CANCELLED"
    assert (buy_ledger.requested_shares, buy_ledger.filled_shares, buy_ledger.remaining_shares) == (
        5_900,
        200,
        5_700,
    )


def test_active_strategic_cohort_does_not_start_missing_buys_while_frozen():
    date = pd.Timestamp("2026-01-06")
    symbol = "unfilled_strategic_member"
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=1_000_000.0,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        candidate_tenure={"strategic_cohort_active": 1},
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=_frozen_caution(),
        user_panel={},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
    )

    assert targets == ()
    assert account.strategic_cohort_targets == {symbol: 0.30}
    assert account.candidate_tenure.get("strategic_cohort_active") == 1


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


@pytest.mark.parametrize(
    "guard_owner_key",
    ("strategic_damage_guard_active_epoch", "strategic_damage_trim_epoch"),
)
def test_strategic_damage_guard_preserves_trail_owner_until_restore_completes(
    guard_owner_key: str,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "guarded_secular_member"
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
        strategic_restore_weights={symbol: 0.30},
        strategic_candidate_signature="strategic_qualification:SECULAR:guarded",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            guard_owner_key: 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))
    guarded = RiskAssessment(
        Risk.CAUTION,
        DEFAULT_CONFIG.strategic_damage_guard_gross,
        2,
        {"freeze_new_risk": True, "transition_damage": 0.60},
        ("strategic transition damage",),
        "NONE",
        freeze_new_risk=True,
    )

    allocator._strategic_cohort_targets(
        date=date,
        risk=guarded,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_exit_bands == {}
    assert account.strategic_restore_weights == {symbol: 0.30}
    assert account.candidate_tenure[guard_owner_key] == 1

    still_damaged = RiskAssessment(
        Risk.NORMAL,
        1.0,
        4,
        {"transition_damage": DEFAULT_CONFIG.strategic_damage_guard_transition},
        (),
        "NONE",
    )
    allocator._strategic_cohort_targets(
        date=date,
        risk=still_damaged,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_restore_weights == {symbol: 0.30}
    assert account.candidate_tenure[guard_owner_key] == 1

    allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_exit_bands == {}
    assert account.strategic_restore_weights == {}
    assert account.candidate_tenure[guard_owner_key] == 0
    assert account.candidate_tenure["strategic_damage_guard_complete_epoch"] == 1


@pytest.mark.parametrize(
    ("capital_budget_owned", "expected_weight"),
    ((False, 0.10), (True, 0.29)),
)
def test_repaired_strategic_damage_guard_uses_a_decisive_next_profit_trail(
    capital_budget_owned: bool,
    expected_weight: float,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "repaired_guard_member"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 1.0
    frame.loc[date, "ma20"] = 1.1
    frame.loc[date, "ret20"] = -0.10
    frame.loc[date, "atr"] = 0.10
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_candidate_signature="strategic_qualification:SECULAR:repaired",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 0,
            "strategic_damage_guard_complete_epoch": 1,
            **({"strategic_guard_level2_epoch": 1} if capital_budget_owned else {}),
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(
        DEFAULT_CONFIG.override(min_trade_value=0.0)
    )._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx(
        {symbol: expected_weight}
    )


def test_post_guard_trail_exits_acute_damage_faster_than_gradual_damage() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("gradual", "acute")
    frames = {symbol: _trend_frame(dates) for symbol in symbols}
    for frame in frames.values():
        frame.loc[date, "close"] = 1.0
        frame.loc[date, "ma20"] = 1.1
        frame.loc[date, "ret20"] = -0.16
        frame.loc[date, "ret60"] = -0.02
        frame.loc[date, "ret120"] = 0.70
        frame.loc[date, "atr"] = 0.10
    frames["gradual"].loc[date, "ret5"] = -0.05
    frames["acute"].loc[date, "ret5"] = -0.10
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_candidate_signature="strategic_qualification:SECULAR:ranked",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 0,
            "strategic_damage_guard_complete_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(
        DEFAULT_CONFIG.override(min_trade_value=0.0)
    )._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel=frames,
        leaders={
            "gradual": _leader("gradual", 0.80),
            "acute": _leader("acute", 0.90),
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now={symbol: 0.30 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx(
        {"gradual": 0.13, "acute": 0.10}
    )


def test_dominant_strategic_owner_locks_profit_once_without_staged_churn() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "causal_dominant"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 33.0
    frame.loc[date, "ma20"] = 30.0
    frame.loc[date, "ret20"] = 0.30
    frame.loc[date, "atr"] = 1.0
    account = AccountState(
        initial_cash=3_300.0,
        cash=0.0,
        positions={symbol: Position(symbol, shares=100, avg_cost=10.0, highest_close=33.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_candidate_signature=(
            "strategic_qualification:EMERGING_SECULAR:causal_dominant,runner"
            ":evidence=reversal_industry"
        ),
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=3_300.0,
        capital_peak=3_300.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))

    locked = allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 33.0},
        weights_now={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in locked or ()} == pytest.approx(
        {symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross}
    )
    assert account.candidate_tenure["strategic_dominant_profit_lock_epoch"] == 1
    assert account.strategic_exit_bands == {}

    frame.loc[date, "close"] = 20.0
    frame.loc[date, "ma20"] = 25.0
    frame.loc[date, "ret20"] = -0.20
    held = allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 20.0},
        weights_now={symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross},
    )

    assert {target.symbol: target.weight for target in held or ()} == pytest.approx(
        {symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross}
    )
    assert account.strategic_exit_bands == {}


def test_dominant_owner_respects_symbol_cap_and_hard_crisis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = "causal_dominant"
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={symbol: Position(symbol, shares=100, avg_cost=1.0, highest_close=1.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (
            Target(symbol, 1.0, "CORE", 0.90, 1.0, "dominant strategic owner"),
        ),
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        0.82,
        2,
        {"transition_damage": 0.40},
        ("level-1 evidence freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    retained = allocator.allocate(
        date=pd.Timestamp("2025-01-02"),
        opportunity=Opportunity.TREND,
        risk=caution,
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )
    assert retained[0].weight == pytest.approx(
        DEFAULT_CONFIG.strategic_dominant_max_weight
    )

    crisis = RiskAssessment(
        Risk.CRISIS,
        0.25,
        5,
        {},
        ("hard crisis",),
        "SEVERE",
        freeze_new_risk=True,
        reduction_level=3,
    )
    reduced = allocator.allocate(
        date=pd.Timestamp("2025-01-03"),
        opportunity=Opportunity.WEAK,
        risk=crisis,
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )
    assert reduced[0].weight == pytest.approx(0.25)


def test_completed_strategic_epoch_clears_zero_exit_band_state():
    date = pd.Timestamp("2025-12-31")
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=["completed_member"],
        strategic_exit_bands={"completed_member": [0.0] * 5},
        strategic_active_bands={"completed_member": [True] * 5},
        protected_weights={"completed_member": 0.30, "unrelated_recovery": 0.20},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    result = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={},
        weights_now={},
    )

    assert result is None
    assert account.strategic_exit_bands == {}
    assert account.strategic_active_bands == {}
    assert account.protected_weights == {"unrelated_recovery": 0.20}
    assert account.candidate_tenure["strategic_cohort_started"] == 0
    assert account.strategic_epochs_completed == 1


def test_strategic_trail_exempts_a_winner_with_intact_structure():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    date = dates[-1]
    frame.loc[date, "close"] = 1.50
    frame.loc[date, "ma20"] = 1.00
    frame.loc[date, "ret20"] = 0.30
    frame.loc[date, "atr"] = 0.05
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            "winner": Position(
                "winner",
                shares=40,
                avg_cost=0.50,
                entry_date=str(dates[-60].date()),
                highest_close=2.00,
            )
        },
        strategic_cohort_symbols=["winner"],
        strategic_cohort_targets={"winner": 0.60},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={"winner": frame},
        leaders={"winner": _leader("winner", 0.95)},
        account=account,
        prices={"winner": 1.50},
    )

    assert account.strategic_exit_bands == {}
    assert next(target for target in targets if target.symbol == "winner").weight > 0


def test_completed_strategic_label_does_not_bypass_current_market_evidence():
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
    for _ in range(DEFAULT_CONFIG.leader_cycle_confirm_days):
        assert not allocator._update_leader_cycle_arm(
            opportunity=Opportunity.STRONG_TREND,
            risk=risk,
            leaders=leaders,
            account=account,
        )
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 0


def test_normal_level1_freeze_preserves_a_live_leader_owner() -> None:
    symbol = "live_leader"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0)},
        active_leaders=[symbol],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    freeze = RiskAssessment(
        Risk.NORMAL,
        1.0,
        1,
        {"freeze_new_risk": True},
        ("temporary level-1 capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    armed = PortfolioAllocator(DEFAULT_CONFIG)._update_leader_cycle_arm(
        opportunity=Opportunity.TREND,
        risk=freeze,
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
    )

    assert armed
    assert account.candidate_tenure["leader_cycle_armed"] == 1


def test_normal_level1_freeze_preserves_armed_core_when_label_is_transiently_absent() -> None:
    symbol = "unlabeled_live_core"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=60,
                avg_cost=1.0,
                lifecycle=Lifecycle.ADD2.value,
            )
        },
        active_leaders=[],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )
    freeze = RiskAssessment(
        Risk.NORMAL,
        1.0,
        1,
        {"freeze_new_risk": True},
        ("temporary level-1 capital freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    armed = PortfolioAllocator(DEFAULT_CONFIG)._update_leader_cycle_arm(
        opportunity=Opportunity.TREND,
        risk=freeze,
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
    )

    assert armed
    assert account.candidate_tenure["leader_cycle_armed"] == 1


def test_confirmed_live_core_waits_in_place_while_leader_owner_rearms() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("confirmed_core_a", "confirmed_core_b")
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in symbols
        },
        active_leaders=list(symbols),
        dynamic_k=2,
        last_k_change_date=str(date.date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.02, "tech_ret120": -0.02},
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={
            symbols[0]: _leader(symbols[0], 0.90, industry="optical"),
            symbols[1]: _leader(symbols[1], 0.88, industry="equipment"),
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.30 for symbol in symbols}
    )
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 0


def test_synchronized_impulse_tolerates_only_a_near_zero_slow_index_leg() -> None:
    leaders = {"impulse": _leader("impulse", 0.83)}

    def risk(weak_leg: float) -> RiskAssessment:
        return RiskAssessment(
            Risk.NORMAL,
            1.0,
            0,
            {
                "broad_ret120": 0.034,
                "tech_ret120": weak_leg,
                "ai_fast_return": 0.161,
                "declining_ratio": 0.0,
                "below_ma20_ratio": 0.0,
                "tech_speed": 0.114,
                "broad_speed": 0.157,
            },
            (),
            "NONE",
        )

    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    near_zero = AccountState.empty(100.0)
    still_weak = AccountState.empty(100.0)

    assert allocator._update_leader_cycle_arm(
        opportunity=Opportunity.TREND,
        risk=risk(-0.001),
        leaders=leaders,
        account=near_zero,
    )
    assert not allocator._update_leader_cycle_arm(
        opportunity=Opportunity.TREND,
        risk=risk(-0.02),
        leaders=leaders,
        account=still_weak,
    )


def test_completed_recovery_cycle_rearms_on_exceptional_current_leaders() -> None:
    leaders = {
        "one": _leader("one", 0.93, industry="optical"),
        "two": _leader("two", 0.91, industry="equipment"),
    }
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            "broad_ret120": 0.10,
            "tech_ret120": 0.12,
            "trend_health": 0.84,
        },
        (),
        "NONE",
    )
    account = AccountState.empty(100.0)
    account.candidate_tenure.update(
        {
            "recovery_cycle_rearm_pending": 1,
            "tactical_cooldown": 0,
        }
    )

    armed = PortfolioAllocator(DEFAULT_CONFIG)._update_leader_cycle_arm(
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        leaders=leaders,
        account=account,
    )

    assert armed
    assert account.candidate_tenure["leader_cycle_armed"] == 1
    assert account.candidate_tenure["recovery_cycle_rearm_pending"] == 0


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


def test_persistent_single_name_v_repair_is_a_fallback_not_a_fast_path_shortcut():
    dates = pd.bdate_range("2025-01-02", periods=80)
    date = dates[-1]
    market = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    protected = market.copy()
    protected.loc[dates[-2], "close"] = protected.loc[date, "close"]
    reference_panel, reference_leaders = _reference_context(market)

    def crisis_account() -> AccountState:
        return AccountState(
            initial_cash=100.0,
            cash=100.0,
            protected_weights={"protected": 0.60},
            risk=Risk.CRISIS.value,
            shock_state="PERSISTENT_STRESS",
            shock_severity="SEVERE",
            shock_start_date=str(dates[-20].date()),
            risk_streaks={"persistent_v_market_repair": (DEFAULT_CONFIG.fast_v_recovery_confirm_days - 1)},
            operating_peak=150.0,
            capital_peak=100.0,
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
            equity=100.0,
            cfg=DEFAULT_CONFIG,
        )

    fallback_account = crisis_account()
    fallback = assess(protected, fallback_account)
    assert fallback.state is Risk.CAUTION
    assert fallback.reasons == ("confirmed persistent V-recovery after extended single-name protection",)
    assert fallback_account.operating_peak == pytest.approx(100.0)

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


def test_profitable_restore_with_confirmed_market_damage_is_a_failed_restoration() -> None:
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
                entry_date=str(dates[-30].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        protected_weights={symbol: 1.0 / 3.0 for symbol in symbols},
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
    strategic_account = copy.deepcopy(account)
    strategic_account.candidate_tenure["strategic_cohort_active"] = 1
    anchored_account = copy.deepcopy(account)
    anchored_account.anchor_weights = {symbol: 1.0 / 3.0 for symbol in symbols}

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
    assert account.candidate_tenure["capital_guard_cooldown"] == (
        DEFAULT_CONFIG.capital_guard_cooldown_days
    )

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


def test_failed_restoration_retires_strategic_restore_before_early_return():
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
        ("capital drawdown relapse in restored holdings",),
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
        cash=15.0,
        positions={
            "held": Position(
                "held",
                shares=85,
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
    assert {target.symbol: target.weight for target in targets} == pytest.approx({"held": 0.85})
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


def test_tactical_expiry_remains_executable_through_a_caution_freeze() -> None:
    dates = pd.bdate_range("2025-10-01", periods=40)
    date = dates[-1]
    frame = _trend_frame(dates)
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

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=caution,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.80)},
        account=account,
        prices={symbol: 1.35},
    )

    assert next(target for target in targets if target.symbol == symbol).weight == 0.0
    assert account.candidate_tenure["tactical_active"] == 0
    assert account.candidate_tenure["recovery_cycle_rearm_pending"] == 1


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
