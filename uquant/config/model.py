"""Authoritative flat configuration model and canonical identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from .validation.execution import validate_execution
from .validation.market import validate_market
from .validation.portfolio import validate_portfolio
from .validation.recovery import validate_recovery
from .validation.risk import validate_crisis_and_sector, validate_risk
from .validation.sentinel import validate_sentinel
from .validation.strategic import (
    validate_strategic_discovery,
    validate_strategic_lifecycle,
    validate_strategic_transition,
)


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
    # Strategic quality thresholds consumed by the current quorum routes.
    strategic_secular_min_score: float = 0.58
    strategic_secular_min_confidence: float = 0.65
    # Both routes below use reviewed causal thresholds and discover synchronized
    # industry groups from the requested universe at runtime.
    strategic_cohort_min_ret240: float = 1.70
    strategic_persistent_max_ret120: float = 1.50
    strategic_established_min_median_ret240: float = 1.00
    strategic_reversal_max_ret240: float = -0.15
    strategic_reversal_min_ret5: float = 0.05
    strategic_reversal_min_median_ret20: float = -0.05
    strategic_reversal_max_tech_ret120: float = -0.01
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
    stable_reference_global_weight: float = 0.70
    unknown_industry_confidence: float = 0.55
    unknown_industry_weight_cap: float = 0.18
    transition_damage_freeze: float = 0.58
    transition_damage_repair: float = 0.38
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
    risk_sentinel_mode: Literal[
        "SHADOW",
        "FREEZE_ONLY",
    ] = "FREEZE_ONLY"
    risk_sentinel_min_confidence: float = 0.80
    risk_sentinel_confirm_days: int = 2
    risk_sentinel_repair_days: int = 3
    risk_sentinel_severe_direct_enabled: bool = True
    risk_sentinel_causal_confirmation_enabled: bool = False
    risk_overlay_enabled: bool = True
    fail_closed: bool = True

    def __post_init__(self) -> None:
        """Reject unsafe values in the exact historical validation order."""

        validate_market(self)
        validate_execution(self)
        validate_crisis_and_sector(self)
        validate_portfolio(self)
        validate_recovery(self)
        validate_strategic_discovery(self)
        validate_strategic_transition(self)
        validate_strategic_lifecycle(self)
        validate_risk(self)
        validate_sentinel(self)

    def override(self, **changes: Any) -> SystemConfig:
        """Return a validated immutable configuration with selected fields replaced."""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Return all configuration fields as a serialization-safe mapping."""

        return asdict(self)


DEFAULT_CONFIG = SystemConfig()


def config_fingerprint(cfg: SystemConfig = DEFAULT_CONFIG) -> str:
    """Return a canonical digest of every effective production setting."""

    payload = cfg.to_dict()
    if payload["risk_sentinel_causal_confirmation_enabled"] is False:
        # The disabled authority switch is excluded from the canonical payload
        # to preserve the reviewed economic identity; enabling it creates a
        # distinct configuration identity.
        payload.pop("risk_sentinel_causal_confirmation_enabled")
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
