"""Ordered risk configuration validation."""

from __future__ import annotations

from typing import Any


def validate_crisis_and_sector(config: Any) -> None:
    """Validate crisis targets and held-sector guard controls."""

    if not 0 <= config.concentrated_crisis_gross <= config.recovery_target_gross <= 1:
        raise ValueError("invalid crisis/recovery gross targets")
    if not 0.50 <= config.recovery_expansive_universe_gross <= config.recovery_target_gross:
        raise ValueError("expansive recovery gross must be in [0.50, recovery_target_gross]")
    if (
        not 0
        <= config.severe_recovery_gross
        <= config.concentrated_recovery_gross
        <= config.recovery_target_gross
    ):
        raise ValueError("invalid differentiated recovery gross targets")
    if config.sector_guard_min_symbols < 2:
        raise ValueError("sector_guard_min_symbols must be at least two")
    if not -1 < config.sector_shock_return < 0:
        raise ValueError("sector_shock_return must be in (-1, 0)")
    if not 0 <= config.sector_shock_breadth <= 1:
        raise ValueError("sector_shock_breadth must be in [0, 1]")
    if not -1 < config.sector_weighted_shock_return < 0:
        raise ValueError("sector_weighted_shock_return must be in (-1, 0)")
    if not 0 <= config.sector_weighted_negative_exposure <= 1:
        raise ValueError("sector_weighted_negative_exposure must be in [0, 1]")
    if not 1 <= config.sector_shock_confirmations <= config.sector_shock_window:
        raise ValueError("invalid sector shock confirmation window")
    if not 0 <= config.sector_guard_divergence <= 1:
        raise ValueError("sector_guard_divergence must be in [0, 1]")
    if not 0 <= config.sector_guard_gross <= config.max_gross:
        raise ValueError("sector_guard_gross must be in [0, max_gross]")
    if config.sector_guard_min_sessions < 1 or config.sector_recovery_ma < 2:
        raise ValueError("sector guard recovery windows must be positive")
    if not 0 <= config.sector_recovery_breadth <= 1:
        raise ValueError("sector_recovery_breadth must be in [0, 1]")
    if config.sector_recovery_confirmations < 1:
        raise ValueError("sector_recovery_confirmations must be positive")


def _validate_recovery_and_drawdown_risk(config: Any) -> None:
    """Validate recovery timing, drawdown order, and reserve controls."""

    if config.capital_guard_cooldown_days < 1:
        raise ValueError("capital_guard_cooldown_days must be positive")
    if config.capital_guard_min_recovery_days < 1:
        raise ValueError("capital_guard_min_recovery_days must be positive")
    if not 0 < config.capital_guard_relapse_dd <= config.operating_dd_caution:
        raise ValueError("capital_guard_relapse_dd is outside its safety range")
    if config.strategic_cohort_tail_confirm_days < 1:
        raise ValueError("strategic_cohort_tail_confirm_days must be positive")
    if config.fast_v_recovery_confirm_days < 1:
        raise ValueError("fast_v_recovery_confirm_days must be positive")
    if config.persistent_v_recovery_wait_days < config.severe_shock_wait_days:
        raise ValueError("persistent V-recovery wait cannot precede severe wait")
    if not (0 <= config.fast_v_recovery_breadth <= 1 and 0 <= config.fast_v_recovery_below_ma20 <= 1):
        raise ValueError("fast V-recovery breadth thresholds must be in [0, 1]")
    if not 0 <= config.fast_v_recovery_gross <= config.recovery_target_gross:
        raise ValueError("invalid fast V-recovery gross")
    if not config.risk_off_gross <= config.narrow_anchor_guard_gross <= config.max_gross:
        raise ValueError("invalid narrow anchor guard gross")
    if not 0 <= config.narrow_anchor_divergence <= 1:
        raise ValueError("narrow_anchor_divergence must be in [0, 1]")
    if not (
        0
        < config.operating_dd_caution
        < config.incomplete_universe_tail_dd
        < config.capital_dd_risk_off
        < config.capital_dd_crisis
        < 1
    ):
        raise ValueError("invalid ordered portfolio drawdown thresholds")
    if not (
        config.concentrated_crisis_gross <= config.incomplete_universe_crisis_gross <= config.risk_off_gross
    ):
        raise ValueError("invalid incomplete-universe crisis gross")
    if not 1 <= config.incomplete_universe_rearm_days <= config.shock_rearm_days:
        raise ValueError("invalid incomplete-universe rearm days")


def _validate_recovery_reserve_and_unbacked(config: Any) -> None:
    """Validate reserve substitution and unbacked-universe controls."""

    if not 0 <= config.recovery_reserve_min_score <= 1:
        raise ValueError("recovery reserve score must be in [0, 1]")
    if not (-1 < config.recovery_reserve_min_ret120 <= config.recovery_reserve_min_ret60 < 1):
        raise ValueError("invalid recovery reserve return thresholds")
    if not 0 <= config.recovery_substitution_edge <= 1:
        raise ValueError("recovery substitution edge must be in [0, 1]")
    if not 0 < config.recovery_substitution_max_ret20 < 1:
        raise ValueError("recovery substitution max ret20 must be in (0, 1)")
    if config.recovery_substitution_shock_window < 1:
        raise ValueError("recovery substitution shock window must be positive")
    if not 0 < config.unbacked_universe_tail_dd < config.operating_dd_caution:
        raise ValueError("unbacked universe tail must precede operating caution")
    if config.unbacked_recovery_anchor_min_days < 1:
        raise ValueError("unbacked recovery anchor minimum age must be positive")


def _validate_risk_anchors_and_capital(config: Any) -> None:
    """Validate risk anchors, chronic state, and capital ladders."""

    if not 1 <= config.risk_anchor_count <= 3:
        raise ValueError("risk_anchor_count must be in [1, 3]")
    if not 1 <= config.risk_anchor_min_groups <= config.risk_anchor_count:
        raise ValueError("risk_anchor_min_groups must be in [1, risk_anchor_count]")
    if not 1 <= config.risk_anchor_confirm_days <= 10:
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
        if not 0 <= getattr(config, name) <= 1:
            raise ValueError(f"{name} must be in [0, 1]")
    if config.transition_damage_repair >= config.transition_damage_freeze:
        raise ValueError("transition repair must be below freeze threshold")
    if config.chronic_confirm_days < 3 or config.chronic_repair_days < 3:
        raise ValueError("chronic confirmation windows must be at least three")
    if not 0 <= config.chronic_severe_cap <= config.chronic_moderate_cap <= 0.60:
        raise ValueError("invalid chronic deterioration caps")
    if not (
        config.operating_dd_caution
        < config.capital_budget_level2_dd
        < config.capital_dd_risk_off
        < config.capital_budget_level3_dd
        < config.capital_dd_crisis
    ):
        raise ValueError("invalid capital budget drawdown ladder")
    if not (
        config.severe_crisis_gross
        <= config.concentrated_crisis_gross
        <= config.market_crisis_gross
        <= config.capital_budget_level3_cap
        <= config.capital_budget_level2_cap
        <= config.max_gross
    ):
        raise ValueError("invalid severity and capital-budget caps")
    if config.capital_budget_repair_days < 1:
        raise ValueError("capital budget repair days must be positive")


def _validate_confidence_and_scout(config: Any) -> None:
    """Validate confidence sizing and challenger scout limits."""

    if not (
        config.trend_entry_gross
        <= config.high_confidence_entry_gross
        <= config.exceptional_entry_gross
        <= config.max_gross
    ):
        raise ValueError("invalid confidence-conditioned entry gross")
    if not 0 < config.high_confidence_entry_vol20 < 1:
        raise ValueError("high_confidence_entry_vol20 must be in (0, 1)")
    if not 0 < config.challenger_scout_weight <= 0.08:
        raise ValueError("challenger_scout_weight must be in (0, 0.08]")
    if not 5 <= config.challenger_scout_confirm_days <= 10:
        raise ValueError("challenger_scout_confirm_days must be in [5, 10]")
    if not 0 <= config.challenger_scout_score_edge <= 1:
        raise ValueError("challenger_scout_score_edge must be in [0, 1]")
    if not 0 <= config.challenger_scout_incumbent_hysteresis <= 0.20:
        raise ValueError("challenger scout incumbent hysteresis must be in [0, 0.20]")


def validate_risk(config: Any) -> None:
    """Validate risk controls in their exact historical order."""

    _validate_recovery_and_drawdown_risk(config)
    _validate_recovery_reserve_and_unbacked(config)
    _validate_risk_anchors_and_capital(config)
    _validate_confidence_and_scout(config)


__all__ = ("validate_crisis_and_sector", "validate_risk")
