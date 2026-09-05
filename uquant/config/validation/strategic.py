"""Ordered strategic configuration validation."""

from __future__ import annotations

from typing import Any


def _validate_strategic_admission(config: Any) -> None:
    """Validate cycle evidence, cohort sizes, and admission scores."""

    if config.leader_cycle_confirm_days < 1:
        raise ValueError("leader_cycle_confirm_days must be positive")
    if not 1 <= config.leader_cycle_min_mature <= config.max_positions:
        raise ValueError("leader_cycle_min_mature must be in [1, max_positions]")
    if not 0 <= config.leader_cycle_min_score <= 1:
        raise ValueError("leader_cycle_min_score must be in [0, 1]")
    if not 0 <= config.leader_cycle_impulse_breadth <= 1:
        raise ValueError("leader_cycle_impulse_breadth must be in [0, 1]")
    if not -1 < config.leader_cycle_min_market_ret120 < 1:
        raise ValueError("leader_cycle_min_market_ret120 must be in (-1, 1)")
    if not (-1 < config.leader_cycle_impulse_min_market_ret120 <= config.leader_cycle_min_market_ret120):
        raise ValueError("leader_cycle_impulse_min_market_ret120 must not exceed the ordinary market floor")
    if not 1 <= config.strategic_cohort_size <= min(3, config.max_positions):
        raise ValueError("strategic_cohort_size must be in [1, min(3, max_positions)]")
    if not 1 <= config.strategic_cohort_min_size <= config.strategic_cohort_size:
        raise ValueError("strategic_cohort_min_size must be in [1, strategic_cohort_size]")
    if not 0.80 <= config.strategic_two_name_gross <= 0.90:
        raise ValueError("strategic_two_name_gross must be in [0.80, 0.90]")
    if not 0.45 <= config.strategic_one_name_gross <= 0.55:
        raise ValueError("strategic_one_name_gross must be in [0.45, 0.55]")
    if not 0 <= config.strategic_two_name_min_score <= 1:
        raise ValueError("strategic_two_name_min_score must be in [0, 1]")
    if not 0 <= config.strategic_one_name_min_score <= 1:
        raise ValueError("strategic_one_name_min_score must be in [0, 1]")
    if not 0 <= config.strategic_one_name_min_secular_score <= 1:
        raise ValueError("strategic_one_name_min_secular_score must be in [0, 1]")


def _validate_strategic_routes(config: Any) -> None:
    """Validate confirmation, secular, reversal, and epoch controls."""

    if not (
        config.strategic_cohort_confirm_days
        < config.strategic_two_name_confirm_days
        < config.strategic_one_name_confirm_days
    ):
        raise ValueError("smaller strategic cohorts require progressively longer confirmation")
    if not 0 <= config.strategic_secular_min_score <= 1:
        raise ValueError("strategic_secular_min_score must be in [0, 1]")
    if not 0 <= config.strategic_secular_min_confidence <= 1:
        raise ValueError("strategic_secular_min_confidence must be in [0, 1]")
    if config.strategic_cohort_min_ret240 < 0:
        raise ValueError("strategic_cohort_min_ret240 cannot be negative")
    if config.strategic_established_min_median_ret240 < 0:
        raise ValueError("strategic_established_min_median_ret240 cannot be negative")
    if not -1 < config.strategic_reversal_max_ret240 < 0:
        raise ValueError("strategic_reversal_max_ret240 must be in (-1, 0)")
    if not 0 < config.strategic_reversal_min_ret5 < 1:
        raise ValueError("strategic_reversal_min_ret5 must be in (0, 1)")
    if not -1 < config.strategic_reversal_min_median_ret20 <= 0:
        raise ValueError("strategic_reversal_min_median_ret20 must be in (-1, 0]")
    if not -1 < config.strategic_reversal_max_tech_ret120 <= 0:
        raise ValueError("strategic_reversal_max_tech_ret120 must be in (-1, 0]")


def validate_strategic_discovery(config: Any) -> None:
    """Validate strategic discovery in its exact historical order."""

    _validate_strategic_admission(config)
    _validate_strategic_routes(config)


def _validate_strategic_transition_inputs(config: Any) -> None:
    """Validate long-cycle and transition evidence inputs."""

    if not -1 < config.strategic_long_cycle_min_ret20 < 1:
        raise ValueError("strategic_long_cycle_min_ret20 must be in (-1, 1)")
    if not -1 < config.strategic_long_cycle_min_ret60 < 1:
        raise ValueError("strategic_long_cycle_min_ret60 must be in (-1, 1)")
    if not -1 < config.strategic_long_cycle_min_ret120 < 1:
        raise ValueError("strategic_long_cycle_min_ret120 must be in (-1, 1)")
    if not 0 <= config.strategic_current_factor_floor <= 1:
        raise ValueError("strategic_current_factor_floor must be in [0, 1]")
    if not 0 <= config.strategic_transition_min_score <= 1:
        raise ValueError("strategic_transition_min_score must be in [0, 1]")
    if not 0 <= config.strategic_transition_min_component <= 1:
        raise ValueError("strategic_transition_min_component must be in [0, 1]")
    if config.strategic_transition_impulse_min_history < 241:
        raise ValueError("strategic_transition_impulse_min_history must be at least 241")
    if not 0 <= config.strategic_transition_impulse_min_score <= 1:
        raise ValueError("strategic_transition_impulse_min_score must be in [0, 1]")
    if not 0 <= config.strategic_transition_impulse_min_leader_score <= 1:
        raise ValueError("strategic_transition_impulse_min_leader_score must be in [0, 1]")
    if not 0 <= config.strategic_transition_impulse_min_secular_score <= 1:
        raise ValueError("strategic_transition_impulse_min_secular_score must be in [0, 1]")
    if not 0 <= config.strategic_transition_impulse_min_secular_confidence <= 1:
        raise ValueError("strategic_transition_impulse_min_secular_confidence must be in [0, 1]")


def _validate_strategic_transition_bounds(config: Any) -> None:
    """Validate impulse bounds and strategic profit-arm controls."""

    if not -1 < config.strategic_transition_impulse_min_ret20 < 1:
        raise ValueError("strategic_transition_impulse_min_ret20 must be in (-1, 1)")
    if not -1 < config.strategic_transition_impulse_min_ret60 < 1:
        raise ValueError("strategic_transition_impulse_min_ret60 must be in (-1, 1)")
    if not -1 < config.strategic_transition_impulse_min_ret120 < 1:
        raise ValueError("strategic_transition_impulse_min_ret120 must be in (-1, 1)")
    if not -1 < config.strategic_transition_impulse_max_ret120 < 1:
        raise ValueError("strategic_transition_impulse_max_ret120 must be in (-1, 1)")
    if config.strategic_transition_impulse_min_ret120 >= config.strategic_transition_impulse_max_ret120:
        raise ValueError("strategic transition impulse ret120 bounds are inverted")
    if not -1 < config.strategic_transition_impulse_min_market_ret20 < 1:
        raise ValueError("strategic_transition_impulse_min_market_ret20 must be in (-1, 1)")
    if not 0 < config.strategic_long_cycle_max_tech_ret120 < 1:
        raise ValueError("strategic_long_cycle_max_tech_ret120 must be in (0, 1)")
    if config.strategic_persistent_max_ret120 <= 0:
        raise ValueError("strategic_persistent_max_ret120 must be positive")
    if config.strategic_cohort_confirm_days < 1:
        raise ValueError("strategic_cohort_confirm_days must be positive")
    if not 0 <= config.strategic_cohort_profit_arm <= 1:
        raise ValueError("strategic_cohort_profit_arm must be in [0, 1]")


def validate_strategic_transition(config: Any) -> None:
    """Validate strategic transitions in their exact historical order."""

    _validate_strategic_transition_inputs(config)
    _validate_strategic_transition_bounds(config)


def validate_strategic_lifecycle(config: Any) -> None:
    """Validate strategic dominance, exits, and damage guards."""

    if not config.max_symbol_weight < config.strategic_dominant_max_weight <= config.max_gross:
        raise ValueError("invalid strategic dominant max weight")
    if not 0 < config.strategic_dominant_min_leader_gap <= 1:
        raise ValueError("invalid strategic dominant leader gap")
    if config.strategic_dominant_profit_lock_mfe <= config.strategic_cohort_profit_arm:
        raise ValueError("invalid strategic dominant profit lock")
    if not (
        config.max_symbol_weight
        < config.strategic_dominant_retained_gross
        < config.strategic_dominant_max_weight
    ):
        raise ValueError("invalid strategic dominant retained gross")
    if config.strategic_cohort_trail_atr <= 0 or config.strategic_cohort_trail_spacing < 0:
        raise ValueError("invalid strategic cohort trailing distances")
    if config.strategic_cohort_trail_bands < 3 or config.strategic_cohort_trail_bands % 2 == 0:
        raise ValueError("strategic_cohort_trail_bands must be an odd integer >=3")
    if not 0 < config.strategic_cohort_exit_step <= config.max_symbol_weight:
        raise ValueError("invalid strategic cohort exit step")
    if not (
        config.strategic_cohort_exit_step
        <= config.strategic_gradual_post_guard_exit_step
        <= config.strategic_post_guard_exit_step
        <= config.max_symbol_weight
    ):
        raise ValueError("invalid strategic post-guard exit step")
    if not -1 < config.strategic_cohort_disaster_stop < 0:
        raise ValueError("strategic cohort disaster stop must be in (-1, 0)")
    if not (config.operating_dd_caution < config.strategic_cohort_tail_line <= config.capital_dd_crisis):
        raise ValueError("invalid strategic cohort tail line")
    if config.strategic_cohort_guard_days < 1:
        raise ValueError("strategic_cohort_guard_days must be positive")
    if not 0 < config.strategic_damage_guard_dd < config.operating_dd_caution:
        raise ValueError("invalid strategic damage guard drawdown")
    if not (
        config.transition_damage_repair
        < config.strategic_damage_guard_transition
        <= config.transition_damage_freeze
    ):
        raise ValueError("invalid strategic damage guard transition")
    if not config.capital_budget_level3_cap <= config.strategic_damage_guard_gross < config.max_gross:
        raise ValueError("invalid strategic damage guard gross")
    if not (
        config.capital_budget_level3_cap
        <= config.strategic_guard_level2_cap
        <= config.capital_budget_level2_cap
    ):
        raise ValueError("invalid strategic guard level-2 cap")


__all__ = (
    "validate_strategic_discovery",
    "validate_strategic_lifecycle",
    "validate_strategic_transition",
)
