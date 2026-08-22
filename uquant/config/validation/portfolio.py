"""Ordered portfolio configuration validation."""

from __future__ import annotations

from typing import Any


def validate_portfolio(config: Any) -> None:
    """Validate allocation, rotation, and dynamic-K controls."""

    if not 0 < config.trend_entry_gross <= config.trend_target_gross <= 1:
        raise ValueError("invalid trend gross targets")
    if not 0 < config.add1_weight <= config.add2_weight <= config.max_symbol_weight:
        raise ValueError("invalid add tranche weights")
    if not 0 <= config.add1_min_mfe <= config.add2_min_mfe:
        raise ValueError("invalid add-tranche MFE thresholds")
    if config.add_tranche_cooldown_sessions < 1:
        raise ValueError("add_tranche_cooldown_sessions must be positive")
    if not 0 < config.add_index_chase_ret5 < 1:
        raise ValueError("add_index_chase_ret5 must be in (0, 1)")
    if not 0 <= config.replacement_edge <= 1:
        raise ValueError("replacement_edge must be in [0, 1]")
    if config.industry_signal_min_members < 1:
        raise ValueError("industry_signal_min_members must be positive")
    if not 0 <= config.industry_rotation_edge <= 1:
        raise ValueError("industry_rotation_edge must be in [0, 1]")
    if config.replacement_confirm_days < 1 or config.min_hold_days < 1:
        raise ValueError("replacement confirmation and minimum hold must be positive")
    if not 0 < config.replacement_transfer_cap <= config.max_symbol_weight:
        raise ValueError("invalid replacement transfer cap")
    if not 0 <= config.max_satellites <= 2:
        raise ValueError("max_satellites must be in [0, 2]")
    if not 1 <= config.dynamic_k_expand_interval <= config.dynamic_k_change_interval:
        raise ValueError("invalid dynamic K change intervals")
    if config.dynamic_k_confirm_days < 1:
        raise ValueError("dynamic_k_confirm_days must be positive")


__all__ = ("validate_portfolio",)
