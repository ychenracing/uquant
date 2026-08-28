# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.leader import INDUSTRY, REFERENCE_UNIVERSE, credible_recovery_reserve
from uquant.portfolio import PortfolioAllocator
from uquant.risk import (
    _persistent_crisis_cap,
    _portfolio_drawdowns,
)
from uquant.types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    OriginSubsystem,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
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



from _lifecycle_strategic_discovery_cases import (
    test_strategic_cohort_discovers_arbitrary_symbols_without_a_static_prior,
    test_strategic_rank_prefers_a_confirmed_industry_cluster_over_one_high_scoring_outsider,
    test_strategic_established_route_rejects_broken_medium_term_structure,
    test_strategic_transition_route_needs_no_high_240_day_secular_score,
    test_synchronized_industry_impulse_is_causal_and_signature_order_invariant,
    test_synchronized_impulse_rejects_low_quality_medium_term_rebound,
    test_established_cohort_rejects_a_broadly_negative_market_rebound,
    test_strategic_cohort_defers_while_both_market_legs_remain_in_recovery,
    test_full_strategic_cohort_requires_existing_high_confidence_breadth,
    test_strategic_cohort_rejects_a_broad_index_blowoff,
    test_full_strategic_cohort_requires_independent_risk_anchor_coverage,
    test_absolute_ret240_can_admit_without_a_symbol_specific_prior,
    test_persistent_startup_exception_defers_an_overextended_cohort,
)

from _lifecycle_strategic_cohort_cases import (
    test_persistent_industry_outranks_a_shorter_established_group,
    test_broad_established_group_rejects_weak_median_persistence,
    test_synchronized_reversal_is_tagged_as_emerging_secular,
    test_decisive_synchronized_reversal_concentrates_one_dominant_owner,
    test_ordinary_factor_cohort_still_waits_for_dynamic_anchors_to_arm,
    test_weak_regime_can_admit_the_dynamic_persistent_industry_route,
    test_ordinary_partial_strategic_cohort_requires_synchronized_evidence,
    test_single_name_strategic_cohort_rejects_a_nonexceptional_weak_leg,
    test_unqualified_universe_padding_cannot_authorize_a_partial_cohort,
    test_choppy_observation_can_confirm_but_not_admit_a_strategic_cohort,
    test_recovery_regime_is_not_preempted_by_new_trailing_secular_cohort,
    test_disjoint_recovery_anchor_hands_off_to_confirmed_secular_cohort,
    test_locked_disjoint_recovery_anchor_defers_confirmed_secular_cohort,
    test_locked_recovery_cohort_cannot_be_preempted_by_strategic_discovery,
    test_relative_secular_evidence_needs_neither_170_percent_nor_short_cycle_maturity,
    test_strategic_epoch_respects_risk_gate_and_session_cooldown,
    test_strategic_epoch_can_requalify_the_same_members_after_a_fresh_cooldown_streak,
    test_completed_strategic_owner_blocks_generic_handoff_before_rearm_date,
    test_rearmed_strategic_owner_handoff_stages_one_generic_leader,
    test_completed_strategic_epoch_cannot_repeat_staged_generic_handoff,
    test_partially_held_strategic_cohort_targets_every_missing_member,
)

from _lifecycle_freeze_tactical_probe_cases import (
    test_level_one_freeze_retains_partial_sell_and_cancels_partial_buy,
    test_freeze_overlay_keeps_structural_sell_and_drops_replacement_buy,
    test_normal_freeze_holds_exposure_and_risk_off_enforces_its_nonzero_cap,
    test_reason_clean_caution_freeze_still_applies_one_anchor_diversification_cap,
    test_empty_book_freeze_cannot_open_a_tactical_probe,
    test_capital_clean_caution_can_reach_the_empty_book_rebound_filter,
    test_shallow_empty_book_rebound_does_not_justify_a_full_tactical_probe,
    test_independent_shallow_rebound_breadth_confirms_one_tactical_probe,
    test_still_oversold_shallow_rebound_confirms_one_tactical_probe,
    test_oversold_shallow_rebound_needs_medium_term_convexity,
    test_oversold_base_with_modest_long_horizon_extension_can_probe,
    test_deep_tactical_rebound_needs_minimum_medium_term_convexity,
    test_long_horizon_blowoff_pullback_is_not_a_tactical_rebound,
    test_overextended_pullback_with_confirmed_current_reversal_can_probe,
    test_low_quality_fast_reversal_does_not_open_an_empty_book,
    test_independent_deep_crash_probe_does_not_require_broad_market_weakness,
)

from _lifecycle_recovery_admission_cases import (
    test_recovery_member_signature_must_persist_before_new_buys,
    test_three_member_expansion_preserves_the_confirmed_tactical_anchor,
    test_three_confirmed_recovery_members_share_the_full_locked_budget,
    test_unconfirmed_simultaneous_recovery_members_keep_one_tactical_owner,
    test_recovery_cohort_size_ignores_unrelated_universe_members,
    test_ambiguous_recovery_candidates_bound_the_first_deployment,
    test_locked_recovery_cohort_keeps_an_unfinished_owner_buy_target,
    test_confirmed_caution_can_execute_an_armed_recovery_winner_trail,
)

from _lifecycle_protected_repair_cases import (
    test_confirmed_hard_risk_can_only_exit_an_armed_recovery_winner,
    test_confirmed_level1_repair_reaches_the_bounded_empty_book_probe,
    test_caution_frozen_empty_book_deep_recovery_new_high_is_independently_confirmed,
    test_level1_repair_without_candidate_retains_existing_generic_core,
    test_first_level1_repair_step_reopens_only_explicit_protected_intent,
    test_synchronized_crisis_repair_reopens_only_protected_weights,
    test_generic_protected_restore_waits_for_existing_confirmation_before_expansion,
)

from _lifecycle_freeze_execution_cases import (
    test_every_freeze_source_persistently_blocks_empty_book_buys,
    test_frozen_strategic_member_preserves_partial_sell_identity_and_cancels_buy,
    test_partial_fill_direction_survives_real_daily_execute_replan_cycle,
    test_active_strategic_cohort_does_not_start_missing_buys_while_frozen,
)

from _lifecycle_strategic_restore_cases import (
    test_opportunity_budget_caps_new_risk_without_selling_existing_core,
    test_risk_liquidated_strategic_exit_band_is_settled_without_reentry,
    test_strategic_restore_waits_for_every_member_but_settles_a_satisfied_pending_buy,
    test_strategic_restore_completes_against_scaled_attainable_weights,
    test_strategic_restore_caps_winner_drift_before_outer_risk_reduction,
    test_strategic_restore_settles_an_unexecutable_subthreshold_gap,
    test_strategic_restore_scales_only_to_the_explicit_risk_cap_until_normal,
    test_reason_clean_level2_normal_can_restore_a_durable_strategic_cohort_within_cap,
    test_synchronized_restore_retires_missing_members_without_user_industry_breadth,
    test_single_industry_pool_does_not_require_impossible_external_industry_support,
    test_homogeneous_recovery_cohort_can_restore_with_unrelated_pool_industries,
    test_incomplete_strategic_sell_keeps_global_lifecycle_priority_on_recovery_cap,
    test_strategic_risk_capture_merges_members_without_losing_a_missing_restore,
    test_unrelated_protection_does_not_exempt_a_strategic_disaster_exit,
    test_existing_strategic_exit_band_idempotently_cancels_recaptured_restore_rights,
    test_started_strategic_member_without_durable_buy_intent_is_retired,
    test_crisis_liquidated_transition_impulse_member_cannot_reuse_old_restore_rights,
    test_transition_impulse_exits_once_when_every_atr_band_breaks,
)

from _lifecycle_strategic_guard_cases import (
    test_bounded_probe_trail_preserves_full_epoch_exit_timing,
    test_strategic_damage_guard_preserves_trail_owner_until_restore_completes,
    test_repaired_strategic_damage_guard_uses_a_decisive_next_profit_trail,
    test_post_guard_trail_exits_acute_damage_faster_than_gradual_damage,
    test_dominant_strategic_owner_locks_profit_once_without_staged_churn,
    test_dominant_owner_respects_symbol_cap_and_hard_crisis,
    test_dominant_level1_retention_never_buys_up_to_the_exception_cap,
    test_dominant_level1_retention_requires_every_bounded_predicate,
    test_completed_strategic_epoch_clears_zero_exit_band_state,
    test_strategic_trail_exempts_a_winner_with_intact_structure,
    test_completed_strategic_label_does_not_bypass_current_market_evidence,
    test_normal_level1_freeze_preserves_a_live_leader_owner,
    test_normal_level1_freeze_preserves_armed_core_when_label_is_transiently_absent,
    test_confirmed_live_core_waits_in_place_while_leader_owner_rearms,
    test_partially_unconfirmed_core_does_not_bypass_leader_owner_rearm,
    test_slow_market_owner_cohort_reuses_existing_lifecycle_exit_confirmation,
    test_synchronized_impulse_tolerates_only_a_near_zero_slow_index_leg,
    test_completed_recovery_cycle_rearms_on_exceptional_current_leaders,
)

from _lifecycle_leader_recovery_cases import (
    test_add1_add2_are_live_but_a_generic_satellite_is_not_auto_admitted,
    test_effective_n_drives_dynamic_k_and_rotation_records_attribution,
    test_allocator_enforces_risk_cap_on_anchored_early_return,
    test_graduated_recovery_conviction_owner_survives_equal_lifecycle_risk_cut,
    test_sector_guard_prefers_the_less_peak_damaged_equal_lifecycle,
    test_drifted_anchor_actual_gross_cannot_bypass_nominal_risk_cap,
    test_locked_recovery_cohort_scales_missing_members_to_remaining_budget,
    test_stale_single_recovery_anchor_graduates_on_confirmed_leader_cycle,
    test_fully_exited_recovery_anchors_cannot_hijack_a_later_leader_book,
    test_weak_secular_market_allows_early_recovery_cohort_graduation,
    test_graduation_day_retains_a_newly_promoted_recovery_book,
)

from _lifecycle_restoration_risk_cases import (
    test_persistent_single_name_v_repair_is_a_fallback_not_a_fast_path_shortcut,
    test_failed_restoration_triggers_capital_cooldown_and_retires_anchors,
    test_profitable_restore_drawdown_is_not_a_capital_failure,
    test_profitable_restore_with_confirmed_market_damage_uses_ordinary_repair,
    test_profitable_market_backed_relapse_preserves_restoration_ownership,
    test_failed_restoration_retires_strategic_restore_before_early_return,
    test_dynamic_risk_anchors_are_cross_industry_and_signature_confirmed,
    test_mature_recovery_cohort_breaks_on_persistent_market_backed_damage,
    test_confirmed_caution_freezes_new_risk_without_creating_a_sell_order,
    test_tactical_expiry_remains_executable_through_a_caution_freeze,
    test_unprofitable_tactical_time_expiry_waits_for_a_caution_freeze_to_clear,
    test_strategic_cohort_has_no_immunity_from_a_confirmed_severe_cap,
    test_narrow_market_two_of_three_anchor_damage_applies_graded_guard,
)
