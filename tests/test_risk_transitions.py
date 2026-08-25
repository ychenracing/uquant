# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
import pytest

import uquant.risk as risk_module
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.leader import INDUSTRY, REFERENCE_UNIVERSE
from uquant.risk import (
    _evidence_family_votes,
    _strategic_damage_guard_required,
    _update_dynamic_anchors,
    assess_risk,
    build_base_market_family_snapshot,
)
from uquant.types import AccountState, LeaderScore, Position, Risk, RiskAssessment


def _leader(
    symbol: str,
    *,
    score: float = 0.80,
    industry: str | None = None,
) -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=0.95,
        mature=True,
        emerging=False,
        industry=industry or INDUSTRY.get(symbol, "unknown"),
        components={"secular_score": score},
    )


def _market_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    close = np.linspace(100.0, 110.0, len(dates))
    return pd.DataFrame(
        {
            "close": close,
            "ma20": close * 0.95,
            "ma60": close * 0.90,
            "ret5": 0.05,
            "ret10": 0.08,
            "ret20": 0.10,
            "ret60": 0.20,
            "ret120": 0.30,
        },
        index=dates,
    )


def _reference_context(
    dates: pd.DatetimeIndex,
    states: Mapping[pd.Timestamp, str] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, LeaderScore], pd.DataFrame]:
    state_by_date = states or {}
    template = pd.DataFrame(
        {
            "close": 120.0,
            "ma20": 100.0,
            "ma60": 90.0,
            "ret5": 0.05,
            "ret20": 0.10,
            "ret60": 0.20,
            "ret120": 0.30,
        },
        index=dates,
    )
    for date, state in state_by_date.items():
        if state == "damaged":
            template.loc[date, ["close", "ma20", "ma60", "ret5", "ret60"]] = (
                70.0,
                100.0,
                90.0,
                -0.08,
                -0.10,
            )
        elif state == "between_thresholds":
            template.loc[date, ["close", "ma20", "ma60", "ret5", "ret60"]] = (
                90.0,
                100.0,
                80.0,
                0.01,
                0.10,
            )
        elif state != "healthy":
            raise ValueError(f"unsupported reference state: {state}")

    panel = {symbol: template.copy() for symbol in REFERENCE_UNIVERSE}
    leaders = {
        symbol: _leader(symbol, score=0.90 - index * 0.001) for index, symbol in enumerate(REFERENCE_UNIVERSE)
    }
    common_return = np.sin(np.linspace(0.0, 8.0, len(dates)))
    reference_returns = pd.DataFrame(
        {f"reference_{index}": common_return for index in range(4)},
        index=dates,
    )
    return panel, leaders, reference_returns


def _assess(
    *,
    date: pd.Timestamp,
    dates: pd.DatetimeIndex,
    states: Mapping[pd.Timestamp, str],
    account: AccountState,
    cfg: SystemConfig,
    user_panel: dict[str, pd.DataFrame] | None = None,
    equity: float = 100.0,
) -> RiskAssessment:
    market = _market_frame(dates)
    reference_panel, leaders, reference_returns = _reference_context(dates, states)
    for symbol in user_panel or {}:
        leaders.setdefault(symbol, _leader(symbol))
    return assess_risk(
        date=date,
        broad=market,
        tech=market,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        user_panel=user_panel or {},
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
    )


def _isolated_risk_config(**overrides: object) -> SystemConfig:
    return DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        capital_budget_ladder_enabled=False,
        sector_guard_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
        **overrides,
    )


def test_correlated_structure_indicators_cast_one_family_vote() -> None:
    families = _evidence_family_votes(
        {
            "sector_breadth_shock": True,
            "below_ma20_structure": True,
            "multi_industry_sync": True,
        }
    )

    assert families["breadth_structure"]
    assert sum(families.values()) == 1


def test_base_market_snapshot_owns_existing_market_family_thresholds() -> None:
    snapshot = build_base_market_family_snapshot(
        average_fast_return=DEFAULT_CONFIG.risk_fast_return,
        declining_ratio=DEFAULT_CONFIG.risk_breadth,
        below_ma20_ratio=DEFAULT_CONFIG.risk_below_ma20,
        sector_stress_ratio=0.50,
        median_correlation=DEFAULT_CONFIG.risk_correlation,
        volatility_ratio=DEFAULT_CONFIG.risk_volatility_ratio,
        tech_speed=-0.055,
        broad_speed=0.0,
        cfg=DEFAULT_CONFIG,
    )

    assert snapshot.indicator_active == {
        "sector_breadth_shock": True,
        "below_ma20_structure": True,
        "multi_industry_sync": True,
        "correlation_shock": True,
        "volatility_shock": True,
        "index_velocity": True,
    }
    assert snapshot.family_active == {
        "breadth_structure": True,
        "covariance_stress": True,
        "market_velocity": True,
    }


def test_base_market_snapshot_does_not_include_account_or_leadership_families() -> None:
    snapshot = build_base_market_family_snapshot(
        average_fast_return=0.0,
        declining_ratio=0.0,
        below_ma20_ratio=0.0,
        sector_stress_ratio=0.0,
        median_correlation=float("nan"),
        volatility_ratio=1.0,
        tech_speed=0.0,
        broad_speed=0.0,
        cfg=DEFAULT_CONFIG,
    )

    assert snapshot.family_active == {
        "breadth_structure": False,
        "covariance_stress": False,
        "market_velocity": False,
    }


def test_base_market_snapshot_preserves_formal_reason_order_with_leadership() -> None:
    snapshot = build_base_market_family_snapshot(
        average_fast_return=DEFAULT_CONFIG.risk_fast_return,
        declining_ratio=DEFAULT_CONFIG.risk_breadth,
        below_ma20_ratio=DEFAULT_CONFIG.risk_below_ma20,
        sector_stress_ratio=0.50,
        median_correlation=DEFAULT_CONFIG.risk_correlation,
        volatility_ratio=DEFAULT_CONFIG.risk_volatility_ratio,
        tech_speed=-0.055,
        broad_speed=0.0,
        cfg=DEFAULT_CONFIG,
    )

    indicators = snapshot.with_leadership(leader_failure=True)

    assert list(indicators) == [
        "sector_breadth_shock",
        "below_ma20_structure",
        "multi_industry_sync",
        "correlation_shock",
        "volatility_shock",
        "leader_failure",
        "index_velocity",
    ]


def test_true_crash_escalates_across_independent_evidence_families() -> None:
    families = _evidence_family_votes(
        {
            "index_velocity": True,
            "below_ma20_structure": True,
            "correlation_shock": True,
            "leader_failure": True,
            "live_book_damage": True,
            "capital_damage": True,
        }
    )

    assert sum(families.values()) == 6


def test_young_strategic_damage_guard_caps_exposure_without_retiring_owner() -> None:
    account = AccountState.empty(100.0)
    account.strategic_cohort_symbols = ["owner"]
    account.strategic_cohort_targets = {"owner": 1.0}
    account.strategic_candidate_signature = "strategic_qualification:SECULAR:owner"
    account.candidate_tenure.update(
        {
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": 20,
        }
    )

    assert _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )
    assert _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=1,
        cfg=DEFAULT_CONFIG,
    )
    assert account.strategic_cohort_targets == {"owner": 1.0}
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd - 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.operating_dd_caution,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )
    account.candidate_tenure["strategic_cohort_days"] = (
        DEFAULT_CONFIG.capital_budget_new_cohort_grace_days
    )
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )


def test_young_diversified_cohort_uses_only_one_transient_damage_trim() -> None:
    account = AccountState.empty(100.0)
    account.strategic_cohort_symbols = ["a", "b", "c"]
    account.strategic_cohort_targets = {
        "a": 1.0 / 3.0,
        "b": 1.0 / 3.0,
        "c": 1.0 / 3.0,
    }
    account.strategic_candidate_signature = "strategic_qualification:SECULAR:a,b,c"
    account.candidate_tenure.update(
        {
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": 20,
        }
    )

    assert _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )
    account.strategic_epoch = 3
    account.candidate_tenure["strategic_damage_trim_epoch"] = 3
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )


def test_completed_strategic_damage_guard_cannot_repeat_in_the_same_epoch() -> None:
    account = AccountState.empty(100.0)
    account.strategic_epoch = 3
    account.strategic_candidate_signature = "strategic_qualification:SECULAR:owner"
    account.candidate_tenure.update(
        {
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": 20,
            "strategic_damage_guard_complete_epoch": 3,
        }
    )

    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )

    account.strategic_epoch = 4
    assert _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )

    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 4
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )

    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_external_risk_epoch"] = 4
    assert not _strategic_damage_guard_required(
        account=account,
        operating_drawdown=DEFAULT_CONFIG.strategic_damage_guard_dd + 0.01,
        transition_damage=DEFAULT_CONFIG.strategic_damage_guard_transition + 0.01,
        votes=2,
        cfg=DEFAULT_CONFIG,
    )


def test_active_strategic_guard_owns_a_level2_cap_without_affecting_other_books() -> None:
    account = AccountState.empty(100.0)
    account.strategic_epoch = 3
    account.capital_budget_level = 2
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 3

    assert risk_module._strategic_guard_level2_overlay_required(account)

    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_damage_guard_complete_epoch"] = 3
    assert not risk_module._strategic_guard_level2_overlay_required(account)

    account.strategic_epoch = 0
    assert not risk_module._strategic_guard_level2_overlay_required(account)


def test_active_strategic_damage_guard_keeps_its_cap_until_strategy_completes() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    account = AccountState.empty(100.0)
    account.strategic_epoch = 3
    account.strategic_candidate_signature = "strategic_qualification:SECULAR:owner"
    account.candidate_tenure.update(
        {
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": 20,
            "strategic_damage_guard_active_epoch": 3,
        }
    )

    assessment = _assess(
        date=dates[-1],
        dates=dates,
        states={},
        account=account,
        cfg=_isolated_risk_config(),
    )

    assert assessment.evidence["strategic_damage_guard"] is True
    assert assessment.freeze_new_risk is True
    assert assessment.target_gross_cap == pytest.approx(
        DEFAULT_CONFIG.strategic_damage_guard_gross
    )

    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_damage_guard_complete_epoch"] = 3
    completed = _assess(
        date=dates[-1],
        dates=dates,
        states={},
        account=account,
        cfg=_isolated_risk_config(),
    )

    assert completed.evidence["strategic_damage_guard"] is False
    assert completed.target_gross_cap == pytest.approx(DEFAULT_CONFIG.max_gross)


def test_recorded_economic_restore_clears_protection_after_price_drift() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    frame = _market_frame(dates)
    account = AccountState(
        initial_cash=1_000.0,
        cash=120.0,
        positions={
            "a": Position("a", shares=6, avg_cost=100.0, entry_date="2026-01-01"),
            "b": Position("b", shares=2, avg_cost=100.0, entry_date="2026-01-01"),
        },
        protected_weights={"a": 0.60, "b": 0.30},
        candidate_tenure={"post_shock_restore_complete": 1},
        shock_state="RECOVERY",
        shock_severity="SEVERE",
        risk=Risk.NORMAL.value,
        operating_peak=1_000.0,
        capital_peak=1_000.0,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={},
        account=account,
        cfg=_isolated_risk_config(),
        user_panel={"a": frame, "b": frame},
        equity=1_000.0,
    )

    assert assessment.state is Risk.NORMAL
    assert account.protected_weights == {}
    assert account.candidate_tenure["post_shock_restore_complete"] == 0


def test_transition_damage_observation_does_not_create_a_standalone_freeze() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    damaged_dates = dates[-4:]
    states = {date: "damaged" for date in damaged_dates}
    cfg = _isolated_risk_config()
    account = AccountState.empty(100.0)

    assessments = [
        _assess(
            date=date,
            dates=dates,
            states=states,
            account=account,
            cfg=cfg,
        )
        for date in damaged_dates
    ]

    assert all(not item.freeze_new_risk for item in assessments)
    assert not any(key.startswith("transition_damage_") for key in account.risk_streaks)


def test_reanchor_pause_and_missing_candidate_break_confirmation_streak() -> None:
    anchors = ["sz300308", "sh688008", "sh688012"]
    account = AccountState.empty(100.0)
    account.risk_anchor_symbols = list(anchors)
    account.risk_anchor_signature = ",".join(anchors)
    account.risk_anchor_candidate_signature = "sh600487,sh601869"
    account.risk_anchor_candidate_streak = 3
    candidates = {
        "sh600487": _leader("sh600487", score=0.99),
        "sh601869": _leader("sh601869", score=0.98),
    }

    assert _update_dynamic_anchors(
        leaders=candidates,
        account=account,
        cfg=DEFAULT_CONFIG,
        allow_reanchor=False,
    ) == tuple(anchors)
    assert account.risk_anchor_candidate_signature == ""
    assert account.risk_anchor_candidate_streak == 0

    account.risk_anchor_candidate_signature = "sh600487,sh601869"
    account.risk_anchor_candidate_streak = 3
    assert _update_dynamic_anchors(
        leaders={},
        account=account,
        cfg=DEFAULT_CONFIG,
        allow_reanchor=True,
    ) == tuple(anchors)
    assert account.risk_anchor_candidate_signature == ""
    assert account.risk_anchor_candidate_streak == 0


@pytest.mark.parametrize(
    "candidates",
    (
        {
            "sh600487": _leader("sh600487", score=0.99),
            "sh688008": _leader("sh688008", score=0.98),
        },
        {
            symbol: _leader(symbol, score=0.99 - index * 0.01, industry="one_group")
            for index, symbol in enumerate(("sh600487", "sh688008", "sh688012"))
        },
    ),
    ids=("incomplete_count", "insufficient_groups"),
)
def test_invalid_anchor_candidate_cannot_evict_complete_sentinels(
    candidates: dict[str, LeaderScore],
) -> None:
    anchors = ["sz300308", "sh688008", "sh688012"]
    account = AccountState.empty(100.0)
    account.risk_anchor_symbols = list(anchors)
    account.risk_anchor_signature = ",".join(anchors)

    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = _update_dynamic_anchors(
            leaders=candidates,
            account=account,
            cfg=DEFAULT_CONFIG,
            allow_reanchor=True,
        )

    assert observed == tuple(anchors)
    assert account.risk_anchor_symbols == anchors
    assert account.risk_anchor_signature == ",".join(anchors)
    assert account.risk_anchor_candidate_signature == ""
    assert account.risk_anchor_candidate_streak == 0


def test_assess_risk_only_reanchors_during_a_confirmed_healthy_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    cfg = DEFAULT_CONFIG.override(
        chronic_overlay_enabled=False,
        capital_budget_ladder_enabled=False,
        sector_guard_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
    )
    observed: list[bool] = []

    def capture_allow_reanchor(**kwargs: object) -> tuple[str, ...]:
        observed.append(bool(kwargs["allow_reanchor"]))
        account = kwargs["account"]
        assert isinstance(account, AccountState)
        return tuple(account.risk_anchor_symbols)

    monkeypatch.setattr(risk_module, "_update_dynamic_anchors", capture_allow_reanchor)

    healthy = AccountState.empty(100.0)
    _assess(date=date, dates=dates, states={date: "healthy"}, account=healthy, cfg=cfg)

    previous_caution = AccountState.empty(100.0)
    previous_caution.risk = Risk.CAUTION.value
    _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=previous_caution,
        cfg=cfg,
    )

    damaged = AccountState.empty(100.0)
    _assess(date=date, dates=dates, states={date: "damaged"}, account=damaged, cfg=cfg)

    assert observed == [True, False, False]


def _damaged_holding_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": np.linspace(100.0, 75.0, len(dates)),
            "ma20": 100.0,
            "ret5": -0.10,
        },
        index=dates,
    )


def _rearm_account(dates: pd.DatetimeIndex) -> AccountState:
    account = AccountState.empty(100.0)
    account.cash = 0.0
    account.positions = {
        symbol: Position(
            symbol,
            shares=1,
            avg_cost=100.0,
            entry_date=str(dates[0].date()),
            highest_close=100.0,
        )
        for symbol in ("dense", "sparse")
    }
    account.last_shock_date = str(dates[-10].date())
    return account


def test_shock_rearm_uses_canonical_tech_calendar_not_panel_order() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    dense = _damaged_holding_frame(dates)
    sparse = _damaged_holding_frame(pd.DatetimeIndex((dates[0], date)))
    cfg = _isolated_risk_config(
        shock_rearm_days=5,
        incomplete_universe_rearm_days=5,
    )
    states = {date: "damaged"}

    sparse_first = _assess(
        date=date,
        dates=dates,
        states=states,
        account=_rearm_account(dates),
        cfg=cfg,
        user_panel={"sparse": sparse, "dense": dense},
        equity=75.0,
    )
    dense_first = _assess(
        date=date,
        dates=dates,
        states=states,
        account=_rearm_account(dates),
        cfg=cfg,
        user_panel={"dense": dense, "sparse": sparse},
        equity=75.0,
    )
    irrelevant_member = _assess(
        date=date,
        dates=dates,
        states=states,
        account=_rearm_account(dates),
        cfg=cfg,
        user_panel={
            "sparse": sparse,
            "dense": dense,
            "unheld_irrelevant": dense,
        },
        equity=75.0,
    )

    assert sparse_first.state is Risk.CRISIS
    assert dense_first.state is Risk.CRISIS
    assert irrelevant_member.state is Risk.CRISIS
    assert sparse_first.target_gross_cap == pytest.approx(dense_first.target_gross_cap)
    assert sparse_first.target_gross_cap == pytest.approx(
        irrelevant_member.target_gross_cap
    )
    assert sparse_first.severity == irrelevant_member.severity


def test_chronic_overlay_cap_is_a_hard_minimum_on_normal_path() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    account = AccountState.empty(100.0)
    account.chronic_level = 3
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        capital_budget_ladder_enabled=False,
        sector_guard_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
    )

    assert assessment.state is Risk.NORMAL
    assert assessment.target_gross_cap == pytest.approx(cfg.chronic_severe_cap)
    assert assessment.reduction_level == 2


def test_confirmed_risk_off_always_reduces_gross_without_extreme_vote_bundle() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    account = AccountState.empty(100.0)
    account.risk = Risk.RISK_OFF.value
    cfg = _isolated_risk_config()

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
    )

    # One healthy observation is not enough to repair a confirmed state, but
    # the state must still carry its advertised level-2 gross cap.  RISK_OFF
    # cannot be merely a label that leaves the portfolio at 100%.
    assert assessment.state is Risk.RISK_OFF
    assert assessment.target_gross_cap == pytest.approx(cfg.risk_off_gross)
    assert assessment.reduction_level == 2



from _risk_transition_strategic_cap_cases import (
    test_strategic_label_cannot_bypass_confirmed_capital_budget_damage,
    test_mature_strategic_cohort_break_uses_concentrated_cohort_severity,
    test_chronic_overlay_cap_is_a_hard_minimum_on_fast_recovery_path,
    test_confirmed_acute_sector_evacuation_preempts_concentrated_crisis_cap,
    test_first_full_book_fast_shock_triggers_acute_evacuation,
    test_single_live_holding_fast_shock_uses_same_acute_evacuation_owner,
)

from _risk_transition_overlay_budget_cases import (
    test_acute_overlay_preserves_existing_zero_gross_crisis_owner,
    test_protected_restore_cannot_use_overweight_members_to_hide_a_missing_member,
    test_capital_budget_repairs_exactly_one_level_per_confirmation_window,
    test_capital_budget_repair_requires_drawdown_recovery,
    test_single_core_strategic_crisis_uses_concentrated_severity,
    test_capital_budget_relapse_escalates_immediately_and_resets_repair,
)
