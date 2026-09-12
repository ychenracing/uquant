"""Fixed effective rules grouped by their accountable domain owner.

These rules have no constructor or override inputs. Their values and semantic
names participate in the complete effective policy fingerprint.
"""

from typing import ClassVar


class PortfolioPolicy:
    """Fixed Portfolio rules."""

    __slots__ = ()

    min_trade_weight: ClassVar[float] = 0.05
    restoration_min_trade_weight: ClassVar[float] = 0.05
    protected_restore_min_trade_weight: ClassVar[float] = 0.04
    emerging_expiry_days: ClassVar[int] = 10
    replacement_edge: ClassVar[float] = 0.35
    replacement_confirm_days: ClassVar[int] = 3
    replacement_transfer_cap: ClassVar[float] = 0.30
    min_hold_days: ClassVar[int] = 10
    max_rotations_20d: ClassVar[int] = 2
    add1_min_mfe: ClassVar[float] = 0.04
    add2_min_mfe: ClassVar[float] = 0.10
    add_tranche_cooldown_sessions: ClassVar[int] = 5
    add_index_chase_ret5: ClassVar[float] = 0.06
    add1_weight: ClassVar[float] = 0.05
    add2_weight: ClassVar[float] = 0.05
    trend_entry_gross: ClassVar[float] = 0.80
    single_core_entry_cap: ClassVar[float] = 0.50
    core_admission_weight: ClassVar[float] = 0.20
    trend_target_gross: ClassVar[float] = 0.95
    choppy_target_gross: ClassVar[float] = 0.60
    max_satellites: ClassVar[int] = 2
    industry_weight_cap: ClassVar[float] = 0.75
    industry_duplicate_penalty: ClassVar[float] = 0.18
    strong_cluster_penalty: ClassVar[float] = 0.03
    strong_cluster_min_score: ClassVar[float] = 0.85
    strong_cluster_max_gap: ClassVar[float] = 0.06
    correlation_admission_penalty: ClassVar[float] = 0.10
    industry_rotation_enabled: ClassVar[bool] = True
    industry_rotation_edge: ClassVar[float] = 0.18
    industry_rotation_deterioration: ClassVar[float] = 0.48
    industry_rotation_breadth: ClassVar[float] = 0.50
    dynamic_k_confirm_days: ClassVar[int] = 3
    dynamic_k_expand_interval: ClassVar[int] = 5
    dynamic_k_change_interval: ClassVar[int] = 20
    unknown_industry_weight_cap: ClassVar[float] = 0.18
    weak_gross: ClassVar[float] = 0.25
    strong_trend_gross: ClassVar[float] = 1.0
    confidence_sizing_enabled: ClassVar[bool] = True
    high_confidence_entry_gross: ClassVar[float] = 0.90
    exceptional_entry_gross: ClassVar[float] = 0.95
    high_confidence_entry_score: ClassVar[float] = 0.84
    high_confidence_entry_breadth: ClassVar[float] = 0.60
    high_confidence_entry_vol20: ClassVar[float] = 0.045
    conviction_weighting_enabled: ClassVar[bool] = True
    challenger_scout_enabled: ClassVar[bool] = True
    challenger_scout_weight: ClassVar[float] = 0.06
    challenger_scout_confirm_days: ClassVar[int] = 7
    challenger_scout_score_edge: ClassVar[float] = 0.08
    challenger_scout_incumbent_hysteresis: ClassVar[float] = 0.08


class FeaturesPolicy:
    """Fixed Features rules."""

    __slots__ = ()

    min_history: ClassVar[int] = 120
    emerging_min_history: ClassVar[int] = 60
    trend_fast: ClassVar[int] = 20
    trend_medium: ClassVar[int] = 60
    trend_slow: ClassVar[int] = 120
    breakout_window: ClassVar[int] = 40
    atr_window: ClassVar[int] = 14
    correlation_window: ClassVar[int] = 40


class LeaderSelectionPolicy:
    """Fixed LeaderSelection rules."""

    __slots__ = ()

    leader_mature_score: ClassVar[float] = 0.72
    leader_emerging_score: ClassVar[float] = 0.76
    leader_min_confidence: ClassVar[float] = 0.70
    leader_tenure_days: ClassVar[int] = 5
    emerging_tenure_days: ClassVar[int] = 3
    industry_signal_min_members: ClassVar[int] = 2
    industry_rotation_min_score: ClassVar[float] = 0.62
    industry_rotation_min_confidence: ClassVar[float] = 0.50
    stable_reference_global_weight: ClassVar[float] = 0.70
    unknown_industry_confidence: ClassVar[float] = 0.55
    regime_factor_blend_enabled: ClassVar[bool] = True


class RecoveryPolicy:
    """Fixed Recovery rules."""

    __slots__ = ()

    recovery_cohort_graduation_days: ClassVar[int] = 360
    recovery_cohort_weak_graduation_days: ClassVar[int] = 20
    recovery_cohort_weak_market_ret120: ClassVar[float] = -0.10
    recovery_weak_market_min_index_ret60: ClassVar[float] = -0.005
    recovery_reserve_min_score: ClassVar[float] = 0.58
    recovery_reserve_min_ret60: ClassVar[float] = 0.20
    recovery_reserve_min_ret120: ClassVar[float] = 0.15
    # A recovery secondary may be replaced only after genuine structural
    # failure and the same material edge required by ordinary rotation.  This
    # is not the idle-cash scout and cannot sell a healthy incumbent to fund a
    # probe.
    recovery_substitution_edge: ClassVar[float] = 0.35
    recovery_substitution_max_ret20: ClassVar[float] = 0.30
    recovery_substitution_shock_window: ClassVar[int] = 20
    recovery_target_gross: ClassVar[float] = 0.92
    recovery_expansive_universe_gross: ClassVar[float] = 0.70
    recovery_conviction_weighting_enabled: ClassVar[bool] = True
    recovery_conviction_retention_bonus: ClassVar[float] = 0.30
    recovery_add_window_days: ClassVar[int] = 5
    recovery_member_confirm_days: ClassVar[int] = 3
    # This is a winner-only peak-giveback rule, not a universal Core stop.
    recovery_winner_mfe_arm: ClassVar[float] = 0.20
    recovery_winner_trail: ClassVar[float] = 0.10
    recovery_transition_weak_leg_ret120: ClassVar[float] = -0.08
    recovery_transition_strong_leg_max_ret120: ClassVar[float] = 0.08
    recovery_transition_min_divergence: ClassVar[float] = 0.10
    one_anchor_gross_cap: ClassVar[float] = 0.35
    two_anchor_gross_cap: ClassVar[float] = 0.55
    tactical_rebound_weight: ClassVar[float] = 0.60
    tactical_probe_weight: ClassVar[float] = 0.60
    tactical_rebound_max_ret20: ClassVar[float] = -0.20
    tactical_rebound_breadth_max_ret20: ClassVar[float] = -0.15
    tactical_rebound_min_industries: ClassVar[int] = 3
    tactical_rebound_oversold_max_ret5: ClassVar[float] = -0.06
    tactical_rebound_min_ret60: ClassVar[float] = 0.10
    tactical_rebound_oversold_min_ret60: ClassVar[float] = 0.20
    tactical_rebound_max_ret120: ClassVar[float] = 0.90
    tactical_overheat_cooldown_days: ClassVar[int] = 10
    tactical_rebound_take_profit: ClassVar[float] = 0.065
    tactical_frozen_take_profit: ClassVar[float] = 0.30
    tactical_rebound_cooldown_days: ClassVar[int] = 30


class StrategicPolicy:
    """Fixed Strategic rules."""

    __slots__ = ()

    strategic_dynamic_enabled: ClassVar[bool] = True
    strategic_cohort_size: ClassVar[int] = 3
    strategic_cohort_min_size: ClassVar[int] = 3
    strategic_two_name_gross: ClassVar[float] = 0.85
    strategic_one_name_gross: ClassVar[float] = 0.50
    strategic_two_name_min_score: ClassVar[float] = 0.70
    strategic_one_name_min_score: ClassVar[float] = 0.90
    strategic_one_name_min_secular_score: ClassVar[float] = 0.80
    strategic_two_name_confirm_days: ClassVar[int] = 3
    strategic_one_name_confirm_days: ClassVar[int] = 4
    # Strategic quality thresholds consumed by the current quorum routes.
    strategic_secular_min_score: ClassVar[float] = 0.58
    strategic_secular_min_confidence: ClassVar[float] = 0.65
    # Both routes below use reviewed causal thresholds and discover synchronized
    # industry groups from the requested universe at runtime.
    strategic_cohort_min_ret240: ClassVar[float] = 1.70
    strategic_persistent_max_ret120: ClassVar[float] = 1.50
    strategic_established_min_median_ret240: ClassVar[float] = 1.00
    strategic_reversal_max_ret240: ClassVar[float] = -0.15
    strategic_reversal_min_ret5: ClassVar[float] = 0.05
    strategic_reversal_min_median_ret20: ClassVar[float] = -0.05
    # A secular winner may consolidate normally, but a new cohort must not be
    # opened into a broad six-month blow-off.
    strategic_long_cycle_min_ret20: ClassVar[float] = -0.05
    strategic_long_cycle_min_ret60: ClassVar[float] = 0.0
    strategic_long_cycle_min_ret120: ClassVar[float] = 0.0
    strategic_current_factor_floor: ClassVar[float] = 0.50
    # A new leadership transition is confirmed from current, causal evidence;
    # it does not need an already-large 240-session return.
    strategic_transition_min_score: ClassVar[float] = 0.70
    strategic_transition_min_component: ClassVar[float] = 0.70
    strategic_transition_impulse_min_history: ClassVar[int] = 241
    strategic_transition_impulse_min_score: ClassVar[float] = 0.48
    strategic_transition_impulse_min_leader_score: ClassVar[float] = 0.35
    strategic_transition_impulse_min_secular_score: ClassVar[float] = 0.35
    strategic_transition_impulse_min_secular_confidence: ClassVar[float] = 0.65
    strategic_transition_impulse_min_ret20: ClassVar[float] = 0.05
    strategic_transition_impulse_min_ret60: ClassVar[float] = -0.12
    strategic_transition_impulse_min_ret120: ClassVar[float] = -0.20
    strategic_transition_impulse_max_ret120: ClassVar[float] = 0.10
    strategic_transition_impulse_min_market_ret20: ClassVar[float] = 0.0
    strategic_long_cycle_max_tech_ret120: ClassVar[float] = 0.20
    strategic_cohort_confirm_days: ClassVar[int] = 2
    strategic_cohort_profit_arm: ClassVar[float] = 0.10
    # A synchronized reversal remains diversified unless one member is
    # decisively stronger on two independent, causal evidence families.  That
    # exceptional owner may use the otherwise idle gross budget, then converts
    # to a cash-buffered position after one large-MFE profit lock.
    strategic_dominant_max_weight: ClassVar[float] = 0.95
    strategic_dominant_min_leader_gap: ClassVar[float] = 0.05
    strategic_dominant_profit_lock_mfe: ClassVar[float] = 2.20
    strategic_dominant_retained_gross: ClassVar[float] = 0.70
    strategic_cohort_trail_atr: ClassVar[float] = 3.55
    strategic_cohort_trail_spacing: ClassVar[float] = 0.05
    strategic_cohort_trail_bands: ClassVar[int] = 5
    strategic_cohort_exit_step: ClassVar[float] = 0.01
    strategic_gradual_post_guard_exit_step: ClassVar[float] = 0.17
    strategic_post_guard_exit_step: ClassVar[float] = 0.20
    strategic_cohort_disaster_stop: ClassVar[float] = -0.20
    strategic_cohort_tail_line: ClassVar[float] = 0.18
    strategic_cohort_tail_confirm_days: ClassVar[int] = 3
    strategic_cohort_guard_days: ClassVar[int] = 120
    strategic_damage_guard_dd: ClassVar[float] = 0.04
    strategic_damage_guard_transition: ClassVar[float] = 0.55
    strategic_damage_guard_gross: ClassVar[float] = 0.89
    strategic_guard_level2_cap: ClassVar[float] = 0.81


class OpportunityPolicy:
    """Fixed Opportunity rules."""

    __slots__ = ()

    recovery_crash_drawdown: ClassVar[float] = 0.15
    recovery_crash_lookback: ClassVar[int] = 20
    recovery_stabilize_days: ClassVar[int] = 8
    recovery_breadth_min: ClassVar[float] = 0.55
    recovery_confirm_days: ClassVar[int] = 2


class RiskPolicy:
    """Fixed Risk rules."""

    __slots__ = ()

    recovery_cohort_tail_guard_days: ClassVar[int] = 90
    recovery_cohort_tail_line: ClassVar[float] = 0.12
    caution_confirm_days: ClassVar[int] = 2
    risk_off_confirm_days: ClassVar[int] = 2
    crisis_confirm_days: ClassVar[int] = 1
    recovery_risk_confirm_days: ClassVar[int] = 3
    fast_v_recovery_confirm_days: ClassVar[int] = 2
    fast_v_recovery_return: ClassVar[float] = 0.02
    fast_v_recovery_index_return: ClassVar[float] = 0.03
    fast_v_recovery_breadth: ClassVar[float] = 0.40
    fast_v_recovery_below_ma20: ClassVar[float] = 0.80
    fast_v_recovery_gross: ClassVar[float] = 0.60
    risk_fast_return: ClassVar[float] = -0.045
    risk_breadth: ClassVar[float] = 0.65
    risk_below_ma20: ClassVar[float] = 0.65
    risk_correlation: ClassVar[float] = 0.75
    risk_volatility_ratio: ClassVar[float] = 1.80
    dynamic_risk_anchors_enabled: ClassVar[bool] = True
    risk_anchor_count: ClassVar[int] = 3
    risk_anchor_min_groups: ClassVar[int] = 2
    risk_anchor_confirm_days: ClassVar[int] = 5
    risk_anchor_min_secular_score: ClassVar[float] = 0.55
    risk_breadth_name_weight: ClassVar[float] = 0.50
    transition_damage_freeze: ClassVar[float] = 0.58
    transition_damage_repair: ClassVar[float] = 0.38
    chronic_overlay_enabled: ClassVar[bool] = True
    chronic_confirm_days: ClassVar[int] = 4
    chronic_repair_days: ClassVar[int] = 5
    chronic_moderate_cap: ClassVar[float] = 0.45
    chronic_severe_cap: ClassVar[float] = 0.30
    operating_dd_caution: ClassVar[float] = 0.08
    capital_dd_risk_off: ClassVar[float] = 0.14
    capital_dd_crisis: ClassVar[float] = 0.20
    capital_budget_ladder_enabled: ClassVar[bool] = True
    capital_budget_new_cohort_grace_days: ClassVar[int] = 160
    capital_budget_emerging_cohort_grace_days: ClassVar[int] = 40
    capital_budget_level2_dd: ClassVar[float] = 0.12
    capital_budget_level2_cap: ClassVar[float] = 0.82
    capital_budget_level3_dd: ClassVar[float] = 0.16
    capital_budget_level3_cap: ClassVar[float] = 0.50
    capital_budget_repair_days: ClassVar[int] = 5
    capital_guard_relapse_dd: ClassVar[float] = 0.04
    capital_guard_min_recovery_days: ClassVar[int] = 10
    capital_guard_cooldown_days: ClassVar[int] = 60
    concentrated_break_dd: ClassVar[float] = 0.08
    concentrated_break_ratio: ClassVar[float] = 0.67
    concentrated_break_confirm_days: ClassVar[int] = 2
    portfolio_break_dd: ClassVar[float] = 0.17
    portfolio_break_votes: ClassVar[int] = 0
    incomplete_universe_tail_dd: ClassVar[float] = 0.12
    unbacked_universe_tail_dd: ClassVar[float] = 0.05
    unbacked_recovery_anchor_min_days: ClassVar[int] = 60
    incomplete_universe_crisis_gross: ClassVar[float] = 0.50
    incomplete_universe_rearm_days: ClassVar[int] = 10
    concentrated_crisis_gross: ClassVar[float] = 0.255
    severe_crisis_gross: ClassVar[float] = 0.20
    market_crisis_gross: ClassVar[float] = 0.50
    concentrated_repair_days: ClassVar[int] = 2
    severe_shock_ret5: ClassVar[float] = -0.12
    severe_shock_wait_days: ClassVar[int] = 5
    persistent_v_recovery_wait_days: ClassVar[int] = 15
    severe_recovery_gross: ClassVar[float] = 0.25
    concentrated_recovery_gross: ClassVar[float] = 0.50
    shock_rearm_days: ClassVar[int] = 90
    risk_off_gross: ClassVar[float] = 0.66
    narrow_anchor_guard_gross: ClassVar[float] = 0.84
    narrow_anchor_divergence: ClassVar[float] = 0.50
    risk_sentinel_min_confidence: ClassVar[float] = 0.80
    risk_sentinel_confirm_days: ClassVar[int] = 2
    risk_sentinel_repair_days: ClassVar[int] = 3
    risk_sentinel_severe_direct_enabled: ClassVar[bool] = True
    risk_sentinel_causal_confirmation_enabled: ClassVar[bool] = False
    risk_overlay_enabled: ClassVar[bool] = True
    fail_closed: ClassVar[bool] = True


class SectorRiskPolicy:
    """Fixed SectorRisk rules."""

    __slots__ = ()

    sector_guard_enabled: ClassVar[bool] = True
    sector_guard_min_symbols: ClassVar[int] = 2
    sector_shock_return: ClassVar[float] = -0.045
    sector_shock_breadth: ClassVar[float] = 0.20
    sector_weighted_shock_return: ClassVar[float] = -0.024
    sector_weighted_negative_exposure: ClassVar[float] = 0.70
    sector_shock_window: ClassVar[int] = 4
    sector_shock_confirmations: ClassVar[int] = 2
    sector_guard_divergence: ClassVar[float] = 0.50
    # Repeated synchronized damage in the actually held sector is direct book
    # evidence, not a generic market label.  The guard cuts exposure to 40%; a
    # later independent CRISIS may reduce it further.
    sector_guard_gross: ClassVar[float] = 0.40
    sector_guard_min_sessions: ClassVar[int] = 8
    sector_recovery_ma: ClassVar[int] = 10
    sector_recovery_return: ClassVar[float] = 0.0
    sector_recovery_breadth: ClassVar[float] = 0.67
    sector_recovery_confirmations: ClassVar[int] = 3
