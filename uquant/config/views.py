"""Immutable owner-derived views of the authoritative flat configuration."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Literal, Self, cast

from .model import SystemConfig


class _ConfigView:
    __slots__ = ()

    @classmethod
    def from_config(cls, config: SystemConfig) -> Self:
        """Snapshot only the fields declared by this owner-derived view."""

        values = {
            field.name: getattr(config, field.name)
            for field in fields(cast(Any, cls))
        }
        constructor = cast(Any, cls)
        return cast(Self, constructor(**values))


@dataclass(frozen=True, slots=True)
class ExecutionConfigView(_ConfigView):
    """Read-only execution inputs assigned to governance owner EXECUTION."""

    commission_rate: float
    min_commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    max_volume_participation: float
    minimum_median_amount: float
    min_trade_value: float


@dataclass(frozen=True, slots=True)
class PortfolioConfigView(_ConfigView):
    """Read-only portfolio inputs assigned to governance owner PORTFOLIO."""

    max_gross: float
    max_symbol_weight: float
    max_positions: int
    min_trade_weight: float
    restoration_min_trade_weight: float
    protected_restore_min_trade_weight: float
    emerging_expiry_days: int
    replacement_edge: float
    replacement_confirm_days: int
    replacement_transfer_cap: float
    min_hold_days: int
    max_rotations_20d: int
    add1_min_mfe: float
    add2_min_mfe: float
    add_tranche_cooldown_sessions: int
    add_index_chase_ret5: float
    add1_weight: float
    add2_weight: float
    trend_entry_gross: float
    single_core_entry_cap: float
    core_admission_weight: float
    trend_target_gross: float
    choppy_target_gross: float
    max_satellites: int
    industry_weight_cap: float
    industry_duplicate_penalty: float
    strong_cluster_penalty: float
    strong_cluster_min_score: float
    strong_cluster_max_gap: float
    correlation_admission_penalty: float
    industry_rotation_enabled: bool
    industry_rotation_edge: float
    industry_rotation_deterioration: float
    industry_rotation_breadth: float
    dynamic_k_confirm_days: int
    dynamic_k_expand_interval: int
    dynamic_k_change_interval: int
    unknown_industry_weight_cap: float
    weak_gross: float
    strong_trend_gross: float
    confidence_sizing_enabled: bool
    high_confidence_entry_gross: float
    exceptional_entry_gross: float
    high_confidence_entry_score: float
    high_confidence_entry_breadth: float
    high_confidence_entry_vol20: float
    conviction_weighting_enabled: bool
    challenger_scout_enabled: bool
    challenger_scout_weight: float
    challenger_scout_confirm_days: int
    challenger_scout_score_edge: float
    challenger_scout_incumbent_hysteresis: float


@dataclass(frozen=True, slots=True)
class RiskConfigView(_ConfigView):
    """Read-only risk inputs assigned to governance owner RISK."""

    recovery_cohort_tail_guard_days: int
    recovery_cohort_tail_line: float
    caution_confirm_days: int
    risk_off_confirm_days: int
    crisis_confirm_days: int
    recovery_risk_confirm_days: int
    fast_v_recovery_confirm_days: int
    fast_v_recovery_return: float
    fast_v_recovery_index_return: float
    fast_v_recovery_breadth: float
    fast_v_recovery_below_ma20: float
    fast_v_recovery_gross: float
    risk_fast_return: float
    risk_breadth: float
    risk_below_ma20: float
    risk_correlation: float
    risk_volatility_ratio: float
    dynamic_risk_anchors_enabled: bool
    risk_anchor_count: int
    risk_anchor_min_groups: int
    risk_anchor_confirm_days: int
    risk_anchor_min_secular_score: float
    risk_breadth_name_weight: float
    transition_damage_freeze: float
    transition_damage_repair: float
    chronic_overlay_enabled: bool
    chronic_confirm_days: int
    chronic_repair_days: int
    chronic_moderate_cap: float
    chronic_severe_cap: float
    operating_dd_caution: float
    capital_dd_risk_off: float
    capital_dd_crisis: float
    capital_budget_ladder_enabled: bool
    capital_budget_new_cohort_grace_days: int
    capital_budget_emerging_cohort_grace_days: int
    capital_budget_level2_dd: float
    capital_budget_level2_cap: float
    capital_budget_level3_dd: float
    capital_budget_level3_cap: float
    capital_budget_repair_days: int
    capital_guard_relapse_dd: float
    capital_guard_min_recovery_days: int
    capital_guard_cooldown_days: int
    concentrated_break_dd: float
    concentrated_break_ratio: float
    concentrated_break_confirm_days: int
    portfolio_break_dd: float
    portfolio_break_votes: int
    incomplete_universe_tail_dd: float
    unbacked_universe_tail_dd: float
    unbacked_recovery_anchor_min_days: int
    incomplete_universe_crisis_gross: float
    incomplete_universe_rearm_days: int
    concentrated_crisis_gross: float
    severe_crisis_gross: float
    market_crisis_gross: float
    concentrated_repair_days: int
    severe_shock_ret5: float
    severe_shock_wait_days: int
    persistent_v_recovery_wait_days: int
    severe_recovery_gross: float
    concentrated_recovery_gross: float
    shock_rearm_days: int
    risk_off_gross: float
    narrow_anchor_guard_gross: float
    narrow_anchor_divergence: float
    risk_sentinel_mode: Literal["SHADOW", "FREEZE_ONLY"]
    risk_sentinel_min_confidence: float
    risk_sentinel_confirm_days: int
    risk_sentinel_repair_days: int
    risk_sentinel_severe_direct_enabled: bool
    risk_sentinel_causal_confirmation_enabled: bool
    risk_overlay_enabled: bool
    fail_closed: bool


__all__ = (
    "ExecutionConfigView",
    "PortfolioConfigView",
    "RiskConfigView",
)
