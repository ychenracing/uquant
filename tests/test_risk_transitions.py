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
    _strategic_grace_supported,
    _update_capital_budget_ladder,
    _update_dynamic_anchors,
    assess_risk,
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


def _isolated_transition_config(**overrides: object) -> SystemConfig:
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


def test_broad_strategic_grace_is_reserved_for_expansive_universes() -> None:
    assert _strategic_grace_supported(
        configured_universe_size=5,
        broad_compatibility=False,
        cfg=DEFAULT_CONFIG,
    )
    assert not _strategic_grace_supported(
        configured_universe_size=15,
        broad_compatibility=True,
        cfg=DEFAULT_CONFIG,
    )
    assert _strategic_grace_supported(
        configured_universe_size=20,
        broad_compatibility=True,
        cfg=DEFAULT_CONFIG,
    )


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
        cfg=_isolated_transition_config(),
        user_panel={"a": frame, "b": frame},
        equity=1_000.0,
    )

    assert assessment.state is Risk.NORMAL
    assert account.protected_weights == {}
    assert account.candidate_tenure["post_shock_restore_complete"] == 0


def test_transition_freeze_survives_noise_and_requires_consecutive_repair() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    damaged_1, damaged_2, noisy, repair_1, repair_2 = dates[-5:]
    states = {
        damaged_1: "damaged",
        damaged_2: "damaged",
        noisy: "between_thresholds",
        repair_1: "healthy",
        repair_2: "healthy",
    }
    cfg = _isolated_transition_config(
        transition_confirm_days=2,
        transition_repair_days=2,
        transition_damage_repair=0.30,
    )
    account = AccountState.empty(100.0)

    first = _assess(
        date=damaged_1,
        dates=dates,
        states=states,
        account=account,
        cfg=cfg,
    )
    assert not first.freeze_new_risk

    activated = _assess(
        date=damaged_2,
        dates=dates,
        states=states,
        account=account,
        cfg=cfg,
    )
    assert activated.freeze_new_risk
    assert account.risk_streaks["transition_damage_active"] == 1

    still_active = _assess(
        date=noisy,
        dates=dates,
        states=states,
        account=account,
        cfg=cfg,
    )
    assert cfg.transition_damage_repair < still_active.evidence["transition_damage"]
    assert still_active.evidence["transition_damage"] < cfg.transition_damage_freeze
    assert account.risk_streaks["transition_damage_active"] == 1
    assert account.risk_streaks["transition_damage_repair"] == 0

    one_repair = _assess(
        date=repair_1,
        dates=dates,
        states=states,
        account=account,
        cfg=cfg,
    )
    assert one_repair.freeze_new_risk
    assert account.risk_streaks["transition_damage_repair"] == 1

    repaired = _assess(
        date=repair_2,
        dates=dates,
        states=states,
        account=account,
        cfg=cfg,
    )
    assert not repaired.freeze_new_risk
    assert account.risk_streaks["transition_damage_active"] == 0
    assert account.risk_streaks["transition_damage_repair"] == 0


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
        transition_repair_days=2,
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

    active_transition = AccountState.empty(100.0)
    active_transition.risk_streaks["transition_damage_active"] = 1
    _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=active_transition,
        cfg=cfg,
    )

    damaged = AccountState.empty(100.0)
    _assess(date=date, dates=dates, states={date: "damaged"}, account=damaged, cfg=cfg)

    assert observed == [True, False, False, False]


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
    cfg = _isolated_transition_config(
        transition_overlay_enabled=False,
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

    assert sparse_first.state is Risk.CRISIS
    assert dense_first.state is Risk.CRISIS
    assert sparse_first.target_gross_cap == pytest.approx(dense_first.target_gross_cap)


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
    cfg = _isolated_transition_config()

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


def test_strategic_label_cannot_bypass_confirmed_capital_budget_damage() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    symbols = ("strategic_a", "strategic_b", "strategic_c")
    damaged = _damaged_holding_frame(dates)
    account = AccountState(
        initial_cash=260.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[0].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=225.0,
        capital_peak=260.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        sector_guard_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "damaged"},
        account=account,
        cfg=cfg,
        user_panel={symbol: damaged for symbol in symbols},
        equity=225.0,
    )

    assert account.capital_budget_level >= 2
    assert assessment.target_gross_cap <= cfg.capital_budget_level2_cap
    assert assessment.reduction_level >= 2


def test_mature_strategic_cohort_break_uses_concentrated_cohort_severity() -> None:
    dates = pd.bdate_range("2026-01-02", periods=160)
    symbols = ("strategic_a", "strategic_b", "strategic_c")
    damaged = _damaged_holding_frame(dates)
    account = AccountState(
        initial_cash=300.0,
        cash=15.0,
        positions={
            symbol: Position(
                symbol,
                shares=1,
                avg_cost=100.0,
                entry_date=str(dates[0].date()),
                highest_close=100.0,
            )
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 1.0 / 3.0 for symbol in symbols},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_cohort_days": DEFAULT_CONFIG.strategic_cohort_guard_days,
        },
        operating_peak=300.0,
        capital_peak=300.0,
    )
    cfg = _isolated_transition_config(transition_overlay_enabled=False)

    assessment: RiskAssessment | None = None
    for date in dates[-cfg.concentrated_break_confirm_days :]:
        assessment = _assess(
            date=date,
            dates=dates,
            states={date: "healthy"},
            account=account,
            cfg=cfg,
            user_panel={symbol: damaged for symbol in symbols},
            equity=240.0,
        )

    assert assessment is not None
    assert assessment.state is Risk.CRISIS
    assert assessment.severity == "COHORT_BREAK"
    assert account.shock_severity == "COHORT_BREAK"
    assert assessment.target_gross_cap == pytest.approx(cfg.concentrated_crisis_gross)
    assert assessment.reasons == ("confirmed dynamic cohort structural break",)


def test_chronic_overlay_cap_is_a_hard_minimum_on_fast_recovery_path() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    held = _market_frame(dates)
    account = AccountState.empty(100.0)
    account.cash = 50.0
    account.positions = {
        "held": Position(
            "held",
            shares=1,
            avg_cost=100.0,
            entry_date=str(dates[-30].date()),
            highest_close=110.0,
        )
    }
    account.risk = Risk.CRISIS.value
    account.shock_state = "PERSISTENT_STRESS"
    account.shock_severity = "MARKET"
    account.shock_start_date = str(dates[-10].date())
    account.protected_weights = {"held": 0.50}
    account.risk_streaks["independent_market_repair"] = DEFAULT_CONFIG.fast_v_recovery_confirm_days - 1
    account.chronic_level = 3
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        capital_budget_ladder_enabled=False,
        sector_guard_enabled=False,
    )

    assessment = _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
        user_panel={"held": held},
    )

    assert assessment.state is Risk.CAUTION
    assert assessment.shock_state == "FAST_V_RECOVERY"
    assert assessment.target_gross_cap == pytest.approx(cfg.chronic_severe_cap)
    assert assessment.target_gross_cap < cfg.fast_v_recovery_gross
    assert account.protected_weights == {"held": 0.50}
    assert account.shock_severity == "MARKET"
    assert account.operating_peak == pytest.approx(100.0)


def test_confirmed_acute_sector_evacuation_preempts_concentrated_crisis_cap() -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    first_shock, second_shock = dates[-2:]
    held = _market_frame(dates)
    held.loc[first_shock, ["close", "ma20", "ret5"]] = (91.0, 100.0, -0.10)
    held.loc[second_shock, ["close", "ma20", "ret5"]] = (85.54, 100.0, -0.13)
    broad = _market_frame(dates)
    tech = _market_frame(dates)
    broad["ret120"] = 0.0
    tech["ret120"] = 0.60
    reference_panel, reference_leaders, reference_returns = _reference_context(dates)
    symbols = ("held_a", "held_b", "held_c")
    account = AccountState(
        initial_cash=30_000.0,
        cash=0.0,
        positions={
            symbol: Position(symbol, shares=100, avg_cost=100.0)
            for symbol in symbols
        },
        operating_peak=30_000.0,
        capital_peak=30_000.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
        sector_recovery_ma=3,
    )

    assessment: RiskAssessment | None = None
    for date in (first_shock, second_shock):
        assessment = assess_risk(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=reference_returns,
            user_panel={symbol: held for symbol in symbols},
            leaders={
                **reference_leaders,
                **{symbol: _leader(symbol) for symbol in symbols},
            },
            account=account,
            equity=sum(
                account.positions[symbol].shares * float(held.loc[date, "close"])
                for symbol in symbols
            ),
            cfg=cfg,
        )

    assert assessment is not None
    assert account.sector_guard_active
    assert assessment.target_gross_cap == 0.0
    assert assessment.evidence["acute_sector_evacuation"] is True


def test_acute_overlay_preserves_existing_zero_gross_crisis_owner() -> None:
    dates = pd.bdate_range("2026-01-02", periods=131)
    first_shock, acute_shock, next_session = dates[-3:]
    broad = pd.DataFrame(
        {
            "close": np.linspace(100.0, 110.0, len(dates)),
            "ma20": 100.0,
            "ma60": 95.0,
            "ret5": 0.02,
            "ret10": 0.03,
            "ret20": 0.05,
            "ret60": 0.10,
            "ret120": 0.0,
        },
        index=dates,
    )
    tech = broad.copy()
    tech["ret120"] = 0.60
    reference = pd.DataFrame(
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
    reference_panel = {
        symbol: reference.copy() for symbol in REFERENCE_UNIVERSE
    }
    leaders = {
        symbol: _leader(symbol, score=0.90)
        for symbol in REFERENCE_UNIVERSE
    }
    close = np.full(len(dates), 100.0)
    close[-3:] = [91.0, 85.54, 85.54]
    held = pd.DataFrame(
        {
            "close": close,
            "ma20": 100.0,
            "ma60": 95.0,
            "ret5": 0.0,
            "ret20": 0.0,
            "ret60": 0.0,
            "ret120": 0.0,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    held.loc[first_shock, "ret5"] = -0.10
    held.loc[acute_shock:next_session, "ret5"] = -0.13
    symbols = ("held_a", "held_b")
    user_panel = {symbol: held.copy() for symbol in symbols}
    leaders.update({symbol: _leader(symbol) for symbol in symbols})
    account = AccountState(
        initial_cash=20_000.0,
        cash=0.0,
        positions={
            symbol: Position(symbol, shares=100, avg_cost=100.0)
            for symbol in symbols
        },
        operating_peak=20_000.0,
        capital_peak=20_000.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
        sector_recovery_ma=3,
    )

    assessments = [
        assess_risk(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=None,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            equity=200 * float(held.loc[date, "close"]),
            cfg=cfg,
        )
        for date in (first_shock, acute_shock, next_session)
    ]

    assert [item.target_gross_cap for item in assessments] == [0.0, 0.0, 0.0]
    assert assessments[0].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[0].reasons == (
        "unbacked incomplete-universe capital exit",
    )
    assert assessments[1].evidence["acute_sector_evacuation"] is True
    assert assessments[1].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[2].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[2].shock_state == "UNBACKED_COOLDOWN"


def test_protected_restore_cannot_use_overweight_members_to_hide_a_missing_member() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    symbols = ("overweight_a", "overweight_b", "missing_c")
    healthy = _market_frame(dates)
    account = AccountState.empty(100.0)
    account.cash = 0.0
    account.positions = {
        symbol: Position(
            symbol,
            shares=1,
            avg_cost=100.0,
            entry_date=str(dates[-30].date()),
            highest_close=110.0,
        )
        for symbol in symbols[:2]
    }
    account.protected_weights = {symbol: 0.30 for symbol in symbols}
    cfg = _isolated_transition_config()

    _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
        user_panel={symbol: healthy for symbol in symbols},
        equity=100.0,
    )

    assert account.protected_weights == {symbol: 0.30 for symbol in symbols}


def test_capital_budget_repairs_exactly_one_level_per_confirmation_window() -> None:
    account = AccountState.empty(100.0)
    account.capital_budget_level = 4
    repair_days = 3

    for expected_level in (3, 2, 1, 0):
        for _ in range(repair_days - 1):
            _update_capital_budget_ladder(
                account,
                observed_level=0,
                repair_confirmed=True,
                repair_days=repair_days,
            )
            assert account.capital_budget_level == expected_level + 1
        _update_capital_budget_ladder(
            account,
            observed_level=0,
            repair_confirmed=True,
            repair_days=repair_days,
        )
        assert account.capital_budget_level == expected_level
        assert account.capital_budget_repair_streak == 0


def test_capital_budget_relapse_escalates_immediately_and_resets_repair() -> None:
    account = AccountState.empty(100.0)
    account.capital_budget_level = 3
    account.capital_budget_repair_streak = 2

    _update_capital_budget_ladder(
        account,
        observed_level=4,
        repair_confirmed=False,
        repair_days=3,
    )

    assert account.capital_budget_level == 4
    assert account.capital_budget_repair_streak == 0
