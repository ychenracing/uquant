"""Single source of truth for live decisions and historical replay settings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any


def canonical_control_float(value: float) -> float:
    """Serialize one control-plane float with the exact schema-v2 precision."""

    return round(float(value), 12)


@dataclass(frozen=True, slots=True)
class SystemConfig:
    """Immutable strategy, risk, execution, and portfolio configuration.

    The default instance is shared by daily decisions and historical replay so
    both paths use identical constraints. ``override`` returns a new instance;
    it never mutates the production defaults in place.
    """

    initial_cash: float = 2_000_000.0
    max_gross: float = 1.0
    max_symbol_weight: float = 0.60
    max_positions: int = 6
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee: float = 0.00001
    slippage: float = 0.001
    max_volume_participation: float = 0.005
    minimum_median_amount: float = 20_000_000.0
    min_trade_weight: float = 0.05
    restoration_min_trade_weight: float = 0.05
    protected_restore_min_trade_weight: float = 0.04
    min_trade_value: float = 20_000.0
    min_history: int = 120
    emerging_min_history: int = 60
    trend_fast: int = 20
    trend_medium: int = 60
    trend_slow: int = 120
    breakout_window: int = 40
    atr_window: int = 14
    correlation_window: int = 40
    leader_mature_score: float = 0.72
    leader_emerging_score: float = 0.76
    leader_min_confidence: float = 0.70
    leader_tenure_days: int = 5
    emerging_tenure_days: int = 3
    emerging_expiry_days: int = 10
    replacement_edge: float = 0.35
    replacement_confirm_days: int = 3
    replacement_transfer_cap: float = 0.30
    min_hold_days: int = 10
    max_rotations_20d: int = 2
    add1_min_mfe: float = 0.04
    add2_min_mfe: float = 0.10
    add_tranche_cooldown_sessions: int = 5
    add_index_chase_ret5: float = 0.06
    add1_weight: float = 0.05
    add2_weight: float = 0.05
    trend_entry_gross: float = 0.80
    single_core_entry_cap: float = 0.50
    core_admission_weight: float = 0.20
    trend_target_gross: float = 0.95
    choppy_target_gross: float = 0.60
    max_satellites: int = 2
    industry_weight_cap: float = 0.75
    industry_duplicate_penalty: float = 0.18
    strong_cluster_penalty: float = 0.03
    strong_cluster_min_score: float = 0.85
    strong_cluster_max_gap: float = 0.06
    correlation_admission_penalty: float = 0.10
    industry_rotation_enabled: bool = True
    industry_signal_min_members: int = 2
    hierarchical_industry_shrinkage_enabled: bool = False
    industry_rotation_min_score: float = 0.62
    industry_rotation_min_confidence: float = 0.50
    industry_rotation_edge: float = 0.18
    industry_rotation_deterioration: float = 0.48
    industry_rotation_breadth: float = 0.50
    dynamic_k_confirm_days: int = 3
    dynamic_k_expand_interval: int = 5
    dynamic_k_change_interval: int = 20
    recovery_cohort_graduation_days: int = 360
    recovery_cohort_weak_graduation_days: int = 20
    recovery_cohort_weak_market_ret120: float = -0.10
    recovery_weak_market_min_index_ret60: float = -0.005
    recovery_reserve_min_score: float = 0.58
    recovery_reserve_min_ret60: float = 0.20
    recovery_reserve_min_ret120: float = 0.15
    # A recovery secondary may be replaced only after genuine structural
    # failure and the same material edge required by ordinary rotation.  This
    # is not the idle-cash scout and cannot sell a healthy incumbent to fund a
    # probe.
    recovery_substitution_edge: float = 0.35
    recovery_substitution_max_ret20: float = 0.30
    recovery_substitution_shock_window: int = 20
    leader_cycle_confirm_days: int = 3
    leader_cycle_min_mature: int = 2
    leader_cycle_min_score: float = 0.82
    leader_cycle_impulse_return: float = 0.15
    leader_cycle_impulse_index_return: float = 0.15
    leader_cycle_impulse_breadth: float = 0.10
    leader_cycle_min_market_ret120: float = 0.01
    leader_cycle_impulse_min_market_ret120: float = -0.01
    # Strategic membership is evidence-derived. This empty compatibility field
    # accepts serialized input but never supplies a symbol-specific prior.
    strategic_cohort_symbols: tuple[str, ...] = ()
    strategic_dynamic_enabled: bool = True
    strategic_cohort_size: int = 3
    strategic_cohort_min_size: int = 3
    strategic_two_name_gross: float = 0.85
    strategic_one_name_gross: float = 0.50
    strategic_two_name_min_score: float = 0.70
    strategic_one_name_min_score: float = 0.90
    strategic_one_name_min_secular_score: float = 0.80
    strategic_two_name_confirm_days: int = 3
    strategic_one_name_confirm_days: int = 4
    # Serialized compatibility fields. Universe size is diagnostic only, and
    # none of these thresholds may select a production decision path.
    strategic_partial_universe_max_size: int = 8
    adaptive_broad_universe_min_size: int = 10
    adaptive_broad_universe_compatibility_enabled: bool = True
    strategic_secular_min_score: float = 0.58
    strategic_secular_min_confidence: float = 0.65
    # Both routes below use reviewed causal thresholds and discover synchronized
    # industry groups from the requested universe at runtime.
    strategic_cohort_min_ret240: float = 1.70
    strategic_persistent_max_ret120: float = 1.50
    strategic_established_min_median_ret240: float = 1.00
    strategic_expansive_universe_min_size: int = 20
    strategic_persistent_confirm_days: int = 3
    strategic_reversal_max_ret240: float = -0.15
    strategic_reversal_min_ret5: float = 0.05
    strategic_reversal_min_median_ret20: float = -0.05
    strategic_reversal_max_tech_ret120: float = -0.01
    strategic_reversal_confirm_days: int = 2
    strategic_epoch_cooldown_sessions: int = 30
    strategic_epoch_min_symbol_change: int = 1
    # A secular winner may consolidate normally, but a new cohort must not be
    # opened into a broad six-month blow-off.
    strategic_long_cycle_min_ret20: float = -0.05
    strategic_long_cycle_min_ret60: float = 0.0
    strategic_long_cycle_min_ret120: float = 0.0
    strategic_current_factor_floor: float = 0.50
    # A new leadership transition is confirmed from current, causal evidence;
    # it does not need an already-large 240-session return.
    strategic_transition_min_score: float = 0.70
    strategic_transition_min_component: float = 0.70
    strategic_transition_impulse_min_history: int = 241
    strategic_transition_impulse_min_score: float = 0.48
    strategic_transition_impulse_min_leader_score: float = 0.35
    strategic_transition_impulse_min_secular_score: float = 0.35
    strategic_transition_impulse_min_secular_confidence: float = 0.65
    strategic_transition_impulse_min_ret20: float = 0.05
    strategic_transition_impulse_min_ret60: float = -0.12
    strategic_transition_impulse_min_ret120: float = -0.20
    strategic_transition_impulse_max_ret120: float = 0.10
    strategic_transition_impulse_min_market_ret20: float = 0.0
    strategic_long_cycle_max_tech_ret120: float = 0.20
    strategic_cohort_confirm_days: int = 2
    strategic_cohort_profit_arm: float = 0.10
    # A synchronized reversal remains diversified unless one member is
    # decisively stronger on two independent, causal evidence families.  That
    # exceptional owner may use the otherwise idle gross budget, then converts
    # to a cash-buffered position after one large-MFE profit lock.
    strategic_dominant_max_weight: float = 0.95
    strategic_dominant_min_leader_gap: float = 0.05
    strategic_dominant_profit_lock_mfe: float = 2.20
    strategic_dominant_retained_gross: float = 0.70
    strategic_cohort_trail_atr: float = 3.55
    strategic_cohort_trail_spacing: float = 0.05
    strategic_cohort_trail_bands: int = 5
    strategic_cohort_exit_step: float = 0.01
    strategic_gradual_post_guard_exit_step: float = 0.17
    strategic_post_guard_exit_step: float = 0.20
    strategic_cohort_disaster_stop: float = -0.20
    strategic_cohort_tail_line: float = 0.18
    strategic_cohort_tail_confirm_days: int = 3
    strategic_cohort_guard_days: int = 120
    strategic_damage_guard_dd: float = 0.04
    strategic_damage_guard_transition: float = 0.55
    strategic_damage_guard_gross: float = 0.89
    strategic_guard_level2_cap: float = 0.81
    recovery_target_gross: float = 0.92
    recovery_expansive_universe_gross: float = 0.70
    recovery_conviction_weighting_enabled: bool = True
    recovery_conviction_retention_bonus: float = 0.30
    recovery_crash_drawdown: float = 0.15
    recovery_crash_lookback: int = 20
    recovery_stabilize_days: int = 8
    recovery_breadth_min: float = 0.55
    recovery_add_window_days: int = 5
    recovery_member_confirm_days: int = 3
    # This is a winner-only peak-giveback rule, not a universal Core stop.
    recovery_winner_mfe_arm: float = 0.20
    recovery_winner_trail: float = 0.10
    recovery_cohort_tail_guard_days: int = 90
    recovery_cohort_tail_line: float = 0.12
    recovery_transition_weak_leg_ret120: float = -0.08
    recovery_transition_strong_leg_max_ret120: float = 0.08
    recovery_transition_min_divergence: float = 0.10
    one_anchor_gross_cap: float = 0.35
    two_anchor_gross_cap: float = 0.55
    tactical_rebound_weight: float = 0.60
    tactical_probe_weight: float = 0.60
    tactical_rebound_max_ret20: float = -0.20
    tactical_rebound_breadth_max_ret20: float = -0.15
    tactical_rebound_min_industries: int = 3
    tactical_rebound_oversold_max_ret5: float = -0.06
    tactical_rebound_min_ret60: float = 0.10
    tactical_rebound_oversold_min_ret60: float = 0.20
    tactical_rebound_max_ret120: float = 0.90
    tactical_overheat_cooldown_days: int = 10
    tactical_rebound_take_profit: float = 0.065
    tactical_frozen_take_profit: float = 0.30
    tactical_rebound_cooldown_days: int = 30
    recovery_confirm_days: int = 2
    caution_confirm_days: int = 2
    risk_off_confirm_days: int = 2
    crisis_confirm_days: int = 1
    recovery_risk_confirm_days: int = 3
    fast_v_recovery_confirm_days: int = 2
    fast_v_recovery_return: float = 0.02
    fast_v_recovery_index_return: float = 0.03
    fast_v_recovery_breadth: float = 0.40
    fast_v_recovery_below_ma20: float = 0.80
    fast_v_recovery_gross: float = 0.60
    risk_fast_return: float = -0.045
    risk_breadth: float = 0.65
    risk_below_ma20: float = 0.65
    risk_correlation: float = 0.75
    risk_volatility_ratio: float = 1.80
    dynamic_risk_anchors_enabled: bool = True
    risk_anchor_count: int = 3
    risk_anchor_min_groups: int = 2
    risk_anchor_confirm_days: int = 5
    risk_anchor_min_secular_score: float = 0.55
    risk_breadth_name_weight: float = 0.50
    group_balanced_reference_enabled: bool = False
    stable_reference_global_weight: float = 0.70
    unknown_industry_confidence: float = 0.55
    unknown_industry_weight_cap: float = 0.18
    transition_overlay_enabled: bool = True
    transition_damage_freeze: float = 0.58
    transition_damage_repair: float = 0.38
    transition_confirm_days: int = 3
    transition_repair_days: int = 4
    chronic_overlay_enabled: bool = True
    chronic_confirm_days: int = 4
    chronic_repair_days: int = 5
    chronic_moderate_cap: float = 0.45
    chronic_severe_cap: float = 0.30
    sector_guard_enabled: bool = True
    sector_guard_min_symbols: int = 2
    sector_shock_return: float = -0.045
    sector_shock_breadth: float = 0.20
    sector_weighted_shock_return: float = -0.024
    sector_weighted_negative_exposure: float = 0.70
    sector_shock_window: int = 4
    sector_shock_confirmations: int = 2
    sector_guard_divergence: float = 0.50
    # Repeated synchronized damage in the actually held sector is direct book
    # evidence, not a generic market label.  The guard cuts exposure to 40%; a
    # later independent CRISIS may reduce it further.
    sector_guard_gross: float = 0.40
    sector_guard_min_sessions: int = 8
    sector_recovery_ma: int = 10
    sector_recovery_return: float = 0.0
    sector_recovery_breadth: float = 0.67
    sector_recovery_confirmations: int = 3
    operating_dd_caution: float = 0.08
    capital_dd_risk_off: float = 0.14
    capital_dd_crisis: float = 0.20
    capital_budget_ladder_enabled: bool = True
    capital_budget_new_cohort_grace_days: int = 160
    capital_budget_emerging_cohort_grace_days: int = 40
    capital_budget_level2_dd: float = 0.12
    capital_budget_level2_cap: float = 0.82
    capital_budget_level3_dd: float = 0.16
    capital_budget_level3_cap: float = 0.50
    capital_budget_repair_days: int = 5
    capital_guard_relapse_dd: float = 0.04
    capital_guard_min_recovery_days: int = 10
    capital_guard_cooldown_days: int = 60
    concentrated_break_dd: float = 0.08
    concentrated_break_ratio: float = 0.67
    concentrated_break_confirm_days: int = 2
    portfolio_break_dd: float = 0.17
    portfolio_break_votes: int = 0
    incomplete_universe_tail_dd: float = 0.12
    unbacked_universe_tail_dd: float = 0.05
    unbacked_recovery_anchor_min_days: int = 60
    incomplete_universe_crisis_gross: float = 0.50
    incomplete_universe_rearm_days: int = 10
    concentrated_crisis_gross: float = 0.255
    severe_crisis_gross: float = 0.20
    market_crisis_gross: float = 0.50
    concentrated_repair_days: int = 2
    severe_shock_ret5: float = -0.12
    severe_shock_wait_days: int = 5
    persistent_v_recovery_wait_days: int = 15
    severe_recovery_gross: float = 0.25
    concentrated_recovery_gross: float = 0.50
    shock_rearm_days: int = 90
    risk_off_gross: float = 0.66
    narrow_anchor_guard_gross: float = 0.84
    narrow_anchor_divergence: float = 0.50
    weak_gross: float = 0.25
    strong_trend_gross: float = 1.0
    regime_factor_blend_enabled: bool = True
    same_day_leader_pipeline_enabled: bool = False
    confidence_sizing_enabled: bool = True
    high_confidence_entry_gross: float = 0.90
    exceptional_entry_gross: float = 0.95
    high_confidence_entry_score: float = 0.84
    high_confidence_entry_breadth: float = 0.60
    high_confidence_entry_vol20: float = 0.045
    conviction_weighting_enabled: bool = True
    challenger_scout_enabled: bool = True
    challenger_scout_weight: float = 0.06
    challenger_scout_confirm_days: int = 7
    challenger_scout_score_edge: float = 0.08
    challenger_scout_incumbent_hysteresis: float = 0.08
    risk_overlay_enabled: bool = True
    evidence_family_voting_enabled: bool = False
    fail_closed: bool = True

    def __post_init__(self) -> None:
        """Reject unsafe values and inconsistent relationships between parameters."""

        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 < self.max_gross <= 1:
            raise ValueError("max_gross must be in (0, 1]")
        if not 0 < self.max_symbol_weight <= 0.60:
            raise ValueError("max_symbol_weight must be in (0, 0.60]")
        if not 1 <= self.max_positions <= 6:
            raise ValueError("max_positions must be in [1, 6]")
        if not 20 <= self.emerging_min_history <= self.min_history:
            raise ValueError("emerging_min_history must be in [20, min_history]")
        for name in (
            "leader_mature_score",
            "leader_emerging_score",
            "leader_min_confidence",
            "industry_weight_cap",
            "concentrated_break_ratio",
            "recovery_breadth_min",
            "recovery_conviction_retention_bonus",
            "industry_rotation_min_score",
            "industry_rotation_min_confidence",
            "industry_rotation_deterioration",
            "industry_rotation_breadth",
        ):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        for name in (
            "commission_rate",
            "min_commission",
            "stamp_duty",
            "transfer_fee",
            "slippage",
            "max_volume_participation",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if not 0 <= self.max_volume_participation <= 1:
            raise ValueError("max_volume_participation must be in [0, 1]")
        if not (
            0
            < self.protected_restore_min_trade_weight
            <= self.restoration_min_trade_weight
            <= self.min_trade_weight
            <= 1
        ):
            raise ValueError(
                "protected_restore_min_trade_weight/restoration_min_trade_weight must be "
                "positive, ordered, and no greater than min_trade_weight"
            )
        if self.min_trade_value < 0:
            raise ValueError("min_trade_value cannot be negative")
        if not 0 <= self.concentrated_crisis_gross <= self.recovery_target_gross <= 1:
            raise ValueError("invalid crisis/recovery gross targets")
        if not 0.50 <= self.recovery_expansive_universe_gross <= self.recovery_target_gross:
            raise ValueError("expansive recovery gross must be in [0.50, recovery_target_gross]")
        if (
            not 0
            <= self.severe_recovery_gross
            <= self.concentrated_recovery_gross
            <= self.recovery_target_gross
        ):
            raise ValueError("invalid differentiated recovery gross targets")
        if self.sector_guard_min_symbols < 2:
            raise ValueError("sector_guard_min_symbols must be at least two")
        if not -1 < self.sector_shock_return < 0:
            raise ValueError("sector_shock_return must be in (-1, 0)")
        if not 0 <= self.sector_shock_breadth <= 1:
            raise ValueError("sector_shock_breadth must be in [0, 1]")
        if not -1 < self.sector_weighted_shock_return < 0:
            raise ValueError("sector_weighted_shock_return must be in (-1, 0)")
        if not 0 <= self.sector_weighted_negative_exposure <= 1:
            raise ValueError("sector_weighted_negative_exposure must be in [0, 1]")
        if not 1 <= self.sector_shock_confirmations <= self.sector_shock_window:
            raise ValueError("invalid sector shock confirmation window")
        if not 0 <= self.sector_guard_divergence <= 1:
            raise ValueError("sector_guard_divergence must be in [0, 1]")
        if not 0 <= self.sector_guard_gross <= self.max_gross:
            raise ValueError("sector_guard_gross must be in [0, max_gross]")
        if self.sector_guard_min_sessions < 1 or self.sector_recovery_ma < 2:
            raise ValueError("sector guard recovery windows must be positive")
        if not 0 <= self.sector_recovery_breadth <= 1:
            raise ValueError("sector_recovery_breadth must be in [0, 1]")
        if self.sector_recovery_confirmations < 1:
            raise ValueError("sector_recovery_confirmations must be positive")
        if not 0 < self.trend_entry_gross <= self.trend_target_gross <= 1:
            raise ValueError("invalid trend gross targets")
        if not 0 < self.add1_weight <= self.add2_weight <= self.max_symbol_weight:
            raise ValueError("invalid add tranche weights")
        if not 0 <= self.add1_min_mfe <= self.add2_min_mfe:
            raise ValueError("invalid add-tranche MFE thresholds")
        if self.add_tranche_cooldown_sessions < 1:
            raise ValueError("add_tranche_cooldown_sessions must be positive")
        if not 0 < self.add_index_chase_ret5 < 1:
            raise ValueError("add_index_chase_ret5 must be in (0, 1)")
        if not 0 <= self.replacement_edge <= 1:
            raise ValueError("replacement_edge must be in [0, 1]")
        if self.industry_signal_min_members < 1:
            raise ValueError("industry_signal_min_members must be positive")
        if not 0 <= self.industry_rotation_edge <= 1:
            raise ValueError("industry_rotation_edge must be in [0, 1]")
        if self.replacement_confirm_days < 1 or self.min_hold_days < 1:
            raise ValueError("replacement confirmation and minimum hold must be positive")
        if not 0 < self.replacement_transfer_cap <= self.max_symbol_weight:
            raise ValueError("invalid replacement transfer cap")
        if not 0 <= self.max_satellites <= 2:
            raise ValueError("max_satellites must be in [0, 2]")
        if not 1 <= self.dynamic_k_expand_interval <= self.dynamic_k_change_interval:
            raise ValueError("invalid dynamic K change intervals")
        if self.dynamic_k_confirm_days < 1:
            raise ValueError("dynamic_k_confirm_days must be positive")
        if not (
            self.dynamic_k_change_interval
            <= self.recovery_cohort_weak_graduation_days
            <= self.recovery_cohort_graduation_days
        ):
            raise ValueError(
                "recovery cohort graduation days must preserve dynamic-K, weak, and mature ordering"
            )
        if not -1 < self.recovery_cohort_weak_market_ret120 < 0:
            raise ValueError("recovery_cohort_weak_market_ret120 must be in (-1, 0)")
        if not -1 < self.recovery_weak_market_min_index_ret60 < 1:
            raise ValueError("recovery_weak_market_min_index_ret60 must be in (-1, 1)")
        if self.recovery_add_window_days < 1:
            raise ValueError("recovery_add_window_days must be positive")
        if self.recovery_member_confirm_days < 2:
            raise ValueError("recovery_member_confirm_days must be at least 2")
        if not 0 < self.recovery_winner_mfe_arm < 2:
            raise ValueError("recovery_winner_mfe_arm must be in (0, 2)")
        if not 0 < self.recovery_winner_trail < 1:
            raise ValueError("recovery_winner_trail must be in (0, 1)")
        if self.recovery_cohort_tail_guard_days < 60:
            raise ValueError("recovery cohort tail guard must be at least 60 sessions")
        if not self.operating_dd_caution < self.recovery_cohort_tail_line < self.capital_dd_crisis:
            raise ValueError("recovery cohort tail line must sit between caution and crisis")
        if not -1 < self.recovery_transition_weak_leg_ret120 < 0:
            raise ValueError("recovery transition weak-leg return must be in (-1, 0)")
        if not 0 < self.recovery_transition_strong_leg_max_ret120 < 1:
            raise ValueError("recovery transition strong-leg return must be in (0, 1)")
        if not 0 < self.recovery_transition_min_divergence < 1:
            raise ValueError("recovery transition divergence must be in (0, 1)")
        if not 0 < self.tactical_probe_weight <= self.tactical_rebound_weight <= self.max_symbol_weight:
            raise ValueError(
                "tactical probe/rebound weights must be positive, ordered, and within max_symbol_weight"
            )
        if not (
            -1
            < self.tactical_rebound_max_ret20
            <= self.tactical_rebound_breadth_max_ret20
            < 0
        ):
            raise ValueError(
                "tactical rebound return thresholds must be ordered in (-1, 0)"
            )
        if self.tactical_rebound_min_industries < 2:
            raise ValueError("tactical_rebound_min_industries must be at least 2")
        if not -1 < self.tactical_rebound_oversold_max_ret5 < 0:
            raise ValueError("tactical_rebound_oversold_max_ret5 must be in (-1, 0)")
        if not (
            0
            < self.tactical_rebound_min_ret60
            <= self.tactical_rebound_oversold_min_ret60
            < 1
        ):
            raise ValueError(
                "tactical rebound ret60 thresholds must be ordered in (0, 1)"
            )
        if self.tactical_rebound_max_ret120 <= 0:
            raise ValueError("tactical_rebound_max_ret120 must be positive")
        if self.tactical_overheat_cooldown_days < 1:
            raise ValueError("tactical_overheat_cooldown_days must be positive")
        if not (
            0
            < self.tactical_rebound_take_profit
            < self.tactical_frozen_take_profit
            < 1
        ):
            raise ValueError("tactical take-profit thresholds must be ordered in (0, 1)")
        if self.leader_cycle_confirm_days < 1:
            raise ValueError("leader_cycle_confirm_days must be positive")
        if not 1 <= self.leader_cycle_min_mature <= self.max_positions:
            raise ValueError("leader_cycle_min_mature must be in [1, max_positions]")
        if not 0 <= self.leader_cycle_min_score <= 1:
            raise ValueError("leader_cycle_min_score must be in [0, 1]")
        if not 0 <= self.leader_cycle_impulse_breadth <= 1:
            raise ValueError("leader_cycle_impulse_breadth must be in [0, 1]")
        if not -1 < self.leader_cycle_min_market_ret120 < 1:
            raise ValueError("leader_cycle_min_market_ret120 must be in (-1, 1)")
        if not (
            -1
            < self.leader_cycle_impulse_min_market_ret120
            <= self.leader_cycle_min_market_ret120
        ):
            raise ValueError(
                "leader_cycle_impulse_min_market_ret120 must not exceed the ordinary market floor"
            )
        if self.strategic_cohort_symbols:
            raise ValueError(
                "strategic_cohort_symbols must remain empty; membership is discovered dynamically"
            )
        if not 1 <= self.strategic_cohort_size <= min(3, self.max_positions):
            raise ValueError("strategic_cohort_size must be in [1, min(3, max_positions)]")
        if not 1 <= self.strategic_cohort_min_size <= self.strategic_cohort_size:
            raise ValueError("strategic_cohort_min_size must be in [1, strategic_cohort_size]")
        if not 0.80 <= self.strategic_two_name_gross <= 0.90:
            raise ValueError("strategic_two_name_gross must be in [0.80, 0.90]")
        if not 0.45 <= self.strategic_one_name_gross <= 0.55:
            raise ValueError("strategic_one_name_gross must be in [0.45, 0.55]")
        if not 0 <= self.strategic_two_name_min_score <= 1:
            raise ValueError("strategic_two_name_min_score must be in [0, 1]")
        if not 0 <= self.strategic_one_name_min_score <= 1:
            raise ValueError("strategic_one_name_min_score must be in [0, 1]")
        if not 0 <= self.strategic_one_name_min_secular_score <= 1:
            raise ValueError("strategic_one_name_min_secular_score must be in [0, 1]")
        if not (
            self.strategic_cohort_confirm_days
            < self.strategic_two_name_confirm_days
            < self.strategic_one_name_confirm_days
        ):
            raise ValueError("smaller strategic cohorts require progressively longer confirmation")
        if not 0 <= self.strategic_secular_min_score <= 1:
            raise ValueError("strategic_secular_min_score must be in [0, 1]")
        if not 0 <= self.strategic_secular_min_confidence <= 1:
            raise ValueError("strategic_secular_min_confidence must be in [0, 1]")
        if self.strategic_cohort_min_ret240 < 0:
            raise ValueError("strategic_cohort_min_ret240 cannot be negative")
        if self.strategic_established_min_median_ret240 < 0:
            raise ValueError("strategic_established_min_median_ret240 cannot be negative")
        if self.strategic_persistent_confirm_days < 1:
            raise ValueError("strategic_persistent_confirm_days must be positive")
        if not -1 < self.strategic_reversal_max_ret240 < 0:
            raise ValueError("strategic_reversal_max_ret240 must be in (-1, 0)")
        if not 0 < self.strategic_reversal_min_ret5 < 1:
            raise ValueError("strategic_reversal_min_ret5 must be in (0, 1)")
        if not -1 < self.strategic_reversal_min_median_ret20 <= 0:
            raise ValueError("strategic_reversal_min_median_ret20 must be in (-1, 0]")
        if not -1 < self.strategic_reversal_max_tech_ret120 <= 0:
            raise ValueError("strategic_reversal_max_tech_ret120 must be in (-1, 0]")
        if self.strategic_reversal_confirm_days < 1:
            raise ValueError("strategic_reversal_confirm_days must be positive")
        if not 20 <= self.strategic_epoch_cooldown_sessions <= 40:
            raise ValueError("strategic epoch cooldown must be in [20, 40]")
        if not 1 <= self.strategic_epoch_min_symbol_change <= 3:
            raise ValueError("strategic epoch symbol change must be in [1, 3]")
        if not -1 < self.strategic_long_cycle_min_ret20 < 1:
            raise ValueError("strategic_long_cycle_min_ret20 must be in (-1, 1)")
        if not -1 < self.strategic_long_cycle_min_ret60 < 1:
            raise ValueError("strategic_long_cycle_min_ret60 must be in (-1, 1)")
        if not -1 < self.strategic_long_cycle_min_ret120 < 1:
            raise ValueError("strategic_long_cycle_min_ret120 must be in (-1, 1)")
        if not 0 <= self.strategic_current_factor_floor <= 1:
            raise ValueError("strategic_current_factor_floor must be in [0, 1]")
        if not 0 <= self.strategic_transition_min_score <= 1:
            raise ValueError("strategic_transition_min_score must be in [0, 1]")
        if not 0 <= self.strategic_transition_min_component <= 1:
            raise ValueError("strategic_transition_min_component must be in [0, 1]")
        if self.strategic_transition_impulse_min_history < 241:
            raise ValueError("strategic_transition_impulse_min_history must be at least 241")
        if not 0 <= self.strategic_transition_impulse_min_score <= 1:
            raise ValueError("strategic_transition_impulse_min_score must be in [0, 1]")
        if not 0 <= self.strategic_transition_impulse_min_leader_score <= 1:
            raise ValueError("strategic_transition_impulse_min_leader_score must be in [0, 1]")
        if not 0 <= self.strategic_transition_impulse_min_secular_score <= 1:
            raise ValueError("strategic_transition_impulse_min_secular_score must be in [0, 1]")
        if not 0 <= self.strategic_transition_impulse_min_secular_confidence <= 1:
            raise ValueError("strategic_transition_impulse_min_secular_confidence must be in [0, 1]")
        if not -1 < self.strategic_transition_impulse_min_ret20 < 1:
            raise ValueError("strategic_transition_impulse_min_ret20 must be in (-1, 1)")
        if not -1 < self.strategic_transition_impulse_min_ret60 < 1:
            raise ValueError("strategic_transition_impulse_min_ret60 must be in (-1, 1)")
        if not -1 < self.strategic_transition_impulse_min_ret120 < 1:
            raise ValueError("strategic_transition_impulse_min_ret120 must be in (-1, 1)")
        if not -1 < self.strategic_transition_impulse_max_ret120 < 1:
            raise ValueError("strategic_transition_impulse_max_ret120 must be in (-1, 1)")
        if self.strategic_transition_impulse_min_ret120 >= self.strategic_transition_impulse_max_ret120:
            raise ValueError("strategic transition impulse ret120 bounds are inverted")
        if not -1 < self.strategic_transition_impulse_min_market_ret20 < 1:
            raise ValueError("strategic_transition_impulse_min_market_ret20 must be in (-1, 1)")
        if not 0 < self.strategic_long_cycle_max_tech_ret120 < 1:
            raise ValueError("strategic_long_cycle_max_tech_ret120 must be in (0, 1)")
        if self.strategic_persistent_max_ret120 <= 0:
            raise ValueError("strategic_persistent_max_ret120 must be positive")
        if self.strategic_cohort_confirm_days < 1:
            raise ValueError("strategic_cohort_confirm_days must be positive")
        if not 0 <= self.strategic_cohort_profit_arm <= 1:
            raise ValueError("strategic_cohort_profit_arm must be in [0, 1]")
        if not self.max_symbol_weight < self.strategic_dominant_max_weight <= self.max_gross:
            raise ValueError("invalid strategic dominant max weight")
        if not 0 < self.strategic_dominant_min_leader_gap <= 1:
            raise ValueError("invalid strategic dominant leader gap")
        if self.strategic_dominant_profit_lock_mfe <= self.strategic_cohort_profit_arm:
            raise ValueError("invalid strategic dominant profit lock")
        if not (
            self.max_symbol_weight
            < self.strategic_dominant_retained_gross
            < self.strategic_dominant_max_weight
        ):
            raise ValueError("invalid strategic dominant retained gross")
        if self.strategic_cohort_trail_atr <= 0 or self.strategic_cohort_trail_spacing < 0:
            raise ValueError("invalid strategic cohort trailing distances")
        if self.strategic_cohort_trail_bands < 3 or self.strategic_cohort_trail_bands % 2 == 0:
            raise ValueError("strategic_cohort_trail_bands must be an odd integer >=3")
        if not 0 < self.strategic_cohort_exit_step <= self.max_symbol_weight:
            raise ValueError("invalid strategic cohort exit step")
        if not (
            self.strategic_cohort_exit_step
            <= self.strategic_gradual_post_guard_exit_step
            <= self.strategic_post_guard_exit_step
            <= self.max_symbol_weight
        ):
            raise ValueError("invalid strategic post-guard exit step")
        if not -1 < self.strategic_cohort_disaster_stop < 0:
            raise ValueError("strategic cohort disaster stop must be in (-1, 0)")
        if not (self.operating_dd_caution < self.strategic_cohort_tail_line <= self.capital_dd_crisis):
            raise ValueError("invalid strategic cohort tail line")
        if self.strategic_cohort_guard_days < 1:
            raise ValueError("strategic_cohort_guard_days must be positive")
        if not 0 < self.strategic_damage_guard_dd < self.operating_dd_caution:
            raise ValueError("invalid strategic damage guard drawdown")
        if not (
            self.transition_damage_repair
            < self.strategic_damage_guard_transition
            <= self.transition_damage_freeze
        ):
            raise ValueError("invalid strategic damage guard transition")
        if not self.capital_budget_level3_cap <= self.strategic_damage_guard_gross < self.max_gross:
            raise ValueError("invalid strategic damage guard gross")
        if not (
            self.capital_budget_level3_cap
            <= self.strategic_guard_level2_cap
            <= self.capital_budget_level2_cap
        ):
            raise ValueError("invalid strategic guard level-2 cap")
        if self.capital_guard_cooldown_days < 1:
            raise ValueError("capital_guard_cooldown_days must be positive")
        if self.capital_guard_min_recovery_days < 1:
            raise ValueError("capital_guard_min_recovery_days must be positive")
        if not 0 < self.capital_guard_relapse_dd <= self.operating_dd_caution:
            raise ValueError("capital_guard_relapse_dd is outside its safety range")
        if self.strategic_cohort_tail_confirm_days < 1:
            raise ValueError("strategic_cohort_tail_confirm_days must be positive")
        if self.fast_v_recovery_confirm_days < 1:
            raise ValueError("fast_v_recovery_confirm_days must be positive")
        if self.persistent_v_recovery_wait_days < self.severe_shock_wait_days:
            raise ValueError("persistent V-recovery wait cannot precede severe wait")
        if not (0 <= self.fast_v_recovery_breadth <= 1 and 0 <= self.fast_v_recovery_below_ma20 <= 1):
            raise ValueError("fast V-recovery breadth thresholds must be in [0, 1]")
        if not 0 <= self.fast_v_recovery_gross <= self.recovery_target_gross:
            raise ValueError("invalid fast V-recovery gross")
        if not self.risk_off_gross <= self.narrow_anchor_guard_gross <= self.max_gross:
            raise ValueError("invalid narrow anchor guard gross")
        if not 0 <= self.narrow_anchor_divergence <= 1:
            raise ValueError("narrow_anchor_divergence must be in [0, 1]")
        if not (
            0
            < self.operating_dd_caution
            < self.incomplete_universe_tail_dd
            < self.capital_dd_risk_off
            < self.capital_dd_crisis
            < 1
        ):
            raise ValueError("invalid ordered portfolio drawdown thresholds")
        if not (
            self.concentrated_crisis_gross <= self.incomplete_universe_crisis_gross <= self.risk_off_gross
        ):
            raise ValueError("invalid incomplete-universe crisis gross")
        if not 1 <= self.incomplete_universe_rearm_days <= self.shock_rearm_days:
            raise ValueError("invalid incomplete-universe rearm days")
        if not 0 <= self.recovery_reserve_min_score <= 1:
            raise ValueError("recovery reserve score must be in [0, 1]")
        if not (-1 < self.recovery_reserve_min_ret120 <= self.recovery_reserve_min_ret60 < 1):
            raise ValueError("invalid recovery reserve return thresholds")
        if not 0 <= self.recovery_substitution_edge <= 1:
            raise ValueError("recovery substitution edge must be in [0, 1]")
        if not 0 < self.recovery_substitution_max_ret20 < 1:
            raise ValueError("recovery substitution max ret20 must be in (0, 1)")
        if self.recovery_substitution_shock_window < 1:
            raise ValueError("recovery substitution shock window must be positive")
        if not 0 < self.unbacked_universe_tail_dd < self.operating_dd_caution:
            raise ValueError("unbacked universe tail must precede operating caution")
        if self.unbacked_recovery_anchor_min_days < 1:
            raise ValueError("unbacked recovery anchor minimum age must be positive")
        if not 1 <= self.risk_anchor_count <= 3:
            raise ValueError("risk_anchor_count must be in [1, 3]")
        if not 1 <= self.risk_anchor_min_groups <= self.risk_anchor_count:
            raise ValueError("risk_anchor_min_groups must be in [1, risk_anchor_count]")
        if not 1 <= self.risk_anchor_confirm_days <= 10:
            raise ValueError("risk_anchor_confirm_days must be in [1, 10]")
        for name in (
            "risk_anchor_min_secular_score",
            "risk_breadth_name_weight",
            "stable_reference_global_weight",
            "unknown_industry_confidence",
            "unknown_industry_weight_cap",
            "transition_damage_freeze",
            "transition_damage_repair",
            "high_confidence_entry_score",
            "high_confidence_entry_breadth",
        ):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.transition_damage_repair >= self.transition_damage_freeze:
            raise ValueError("transition repair must be below freeze threshold")
        if self.transition_confirm_days < 1 or self.transition_repair_days < 1:
            raise ValueError("transition confirmation windows must be positive")
        if self.chronic_confirm_days < 3 or self.chronic_repair_days < 3:
            raise ValueError("chronic confirmation windows must be at least three")
        if not 0 <= self.chronic_severe_cap <= self.chronic_moderate_cap <= 0.60:
            raise ValueError("invalid chronic deterioration caps")
        if not (
            self.operating_dd_caution
            < self.capital_budget_level2_dd
            < self.capital_dd_risk_off
            < self.capital_budget_level3_dd
            < self.capital_dd_crisis
        ):
            raise ValueError("invalid capital budget drawdown ladder")
        if not (
            self.severe_crisis_gross
            <= self.concentrated_crisis_gross
            <= self.market_crisis_gross
            <= self.capital_budget_level3_cap
            <= self.capital_budget_level2_cap
            <= self.max_gross
        ):
            raise ValueError("invalid severity and capital-budget caps")
        if self.capital_budget_repair_days < 1:
            raise ValueError("capital budget repair days must be positive")
        if not (
            self.trend_entry_gross
            <= self.high_confidence_entry_gross
            <= self.exceptional_entry_gross
            <= self.max_gross
        ):
            raise ValueError("invalid confidence-conditioned entry gross")
        if not 0 < self.high_confidence_entry_vol20 < 1:
            raise ValueError("high_confidence_entry_vol20 must be in (0, 1)")
        if not 0 < self.challenger_scout_weight <= 0.08:
            raise ValueError("challenger_scout_weight must be in (0, 0.08]")
        if not 5 <= self.challenger_scout_confirm_days <= 10:
            raise ValueError("challenger_scout_confirm_days must be in [5, 10]")
        if not 0 <= self.challenger_scout_score_edge <= 1:
            raise ValueError("challenger_scout_score_edge must be in [0, 1]")
        if not 0 <= self.challenger_scout_incumbent_hysteresis <= 0.20:
            raise ValueError("challenger scout incumbent hysteresis must be in [0, 0.20]")

    def override(self, **changes: Any) -> SystemConfig:
        """Return a validated immutable configuration with selected fields replaced."""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Return all configuration fields as a serialization-safe mapping."""

        return asdict(self)


DEFAULT_CONFIG = SystemConfig()


def config_fingerprint(cfg: SystemConfig = DEFAULT_CONFIG) -> str:
    """Return a canonical digest of every effective production setting."""

    encoded = json.dumps(
        cfg.to_dict(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
