"""Ordered recovery configuration validation."""

from __future__ import annotations

from typing import Any


def validate_recovery(config: Any) -> None:
    """Validate recovery, substitution, and rebound controls."""

    if not (
        config.dynamic_k_change_interval
        <= config.recovery_cohort_weak_graduation_days
        <= config.recovery_cohort_graduation_days
    ):
        raise ValueError("recovery cohort graduation days must preserve dynamic-K, weak, and mature ordering")
    if not -1 < config.recovery_cohort_weak_market_ret120 < 0:
        raise ValueError("recovery_cohort_weak_market_ret120 must be in (-1, 0)")
    if not -1 < config.recovery_weak_market_min_index_ret60 < 1:
        raise ValueError("recovery_weak_market_min_index_ret60 must be in (-1, 1)")
    if config.recovery_add_window_days < 1:
        raise ValueError("recovery_add_window_days must be positive")
    if config.recovery_member_confirm_days < 2:
        raise ValueError("recovery_member_confirm_days must be at least 2")
    if not 0 < config.recovery_winner_mfe_arm < 2:
        raise ValueError("recovery_winner_mfe_arm must be in (0, 2)")
    if not 0 < config.recovery_winner_trail < 1:
        raise ValueError("recovery_winner_trail must be in (0, 1)")
    if config.recovery_cohort_tail_guard_days < 60:
        raise ValueError("recovery cohort tail guard must be at least 60 sessions")
    if not config.operating_dd_caution < config.recovery_cohort_tail_line < config.capital_dd_crisis:
        raise ValueError("recovery cohort tail line must sit between caution and crisis")
    if not -1 < config.recovery_transition_weak_leg_ret120 < 0:
        raise ValueError("recovery transition weak-leg return must be in (-1, 0)")
    if not 0 < config.recovery_transition_strong_leg_max_ret120 < 1:
        raise ValueError("recovery transition strong-leg return must be in (0, 1)")
    if not 0 < config.recovery_transition_min_divergence < 1:
        raise ValueError("recovery transition divergence must be in (0, 1)")
    if not 0 < config.tactical_probe_weight <= config.tactical_rebound_weight <= config.max_symbol_weight:
        raise ValueError(
            "tactical probe/rebound weights must be positive, ordered, and within max_symbol_weight"
        )
    if not (-1 < config.tactical_rebound_max_ret20 <= config.tactical_rebound_breadth_max_ret20 < 0):
        raise ValueError("tactical rebound return thresholds must be ordered in (-1, 0)")
    if config.tactical_rebound_min_industries < 2:
        raise ValueError("tactical_rebound_min_industries must be at least 2")
    if not -1 < config.tactical_rebound_oversold_max_ret5 < 0:
        raise ValueError("tactical_rebound_oversold_max_ret5 must be in (-1, 0)")
    if not (0 < config.tactical_rebound_min_ret60 <= config.tactical_rebound_oversold_min_ret60 < 1):
        raise ValueError("tactical rebound ret60 thresholds must be ordered in (0, 1)")
    if config.tactical_rebound_max_ret120 <= 0:
        raise ValueError("tactical_rebound_max_ret120 must be positive")
    if config.tactical_overheat_cooldown_days < 1:
        raise ValueError("tactical_overheat_cooldown_days must be positive")
    if not (0 < config.tactical_rebound_take_profit < config.tactical_frozen_take_profit < 1):
        raise ValueError("tactical take-profit thresholds must be ordered in (0, 1)")


__all__ = ("validate_recovery",)
