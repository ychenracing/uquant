"""Closed policy neighbors used only by explicit offline validation."""

from dataclasses import asdict

from uquant.config import DEFAULT_CONFIG, SystemConfig


def frozen_policy_config(profile: str, base: SystemConfig = DEFAULT_CONFIG) -> SystemConfig:
    """Apply one existing frozen policy neighbor for offline validation only.

    The caller selects a named contract case, never a rule name or value.
    The returned configuration uses the same production implementation and
    records the perturbed rule in its complete effective fingerprint.
    """
    class ReversalLower(SystemConfig):
        __slots__ = ()
        strategic_reversal_min_ret5 = 0.0475

    class ReversalUpper(SystemConfig):
        __slots__ = ()
        strategic_reversal_min_ret5 = 0.0525

    class ProfitLockLower(SystemConfig):
        __slots__ = ()
        strategic_dominant_profit_lock_mfe = 2.09

    class ProfitLockUpper(SystemConfig):
        __slots__ = ()
        strategic_dominant_profit_lock_mfe = 2.31

    class RetainedGrossLower(SystemConfig):
        __slots__ = ()
        strategic_dominant_retained_gross = 0.68

    class RetainedGrossUpper(SystemConfig):
        __slots__ = ()
        strategic_dominant_retained_gross = 0.72

    class ConfirmationLower(SystemConfig):
        __slots__ = ()
        leader_tenure_days = 4

    class ConfirmationUpper(SystemConfig):
        __slots__ = ()
        leader_tenure_days = 6

    class RecoveryBreadthLower(SystemConfig):
        __slots__ = ()
        recovery_breadth_min = DEFAULT_CONFIG.recovery_breadth_min * 0.90

    class RecoveryBreadthUpper(SystemConfig):
        __slots__ = ()
        recovery_breadth_min = DEFAULT_CONFIG.recovery_breadth_min * 1.10

    profiles = {
        "recovery_breadth_lower": RecoveryBreadthLower,
        "recovery_breadth_upper": RecoveryBreadthUpper,
        "p2_lower": ReversalLower, "p2_upper": ReversalUpper,
        "p7_lower": ProfitLockLower, "p7_upper": ProfitLockUpper,
        "p8_lower": RetainedGrossLower, "p8_upper": RetainedGrossUpper,
        "confirmation_lower": ConfirmationLower, "confirmation_upper": ConfirmationUpper,
    }
    if profile not in profiles:
        raise ValueError(f"unknown frozen policy profile: {profile}")
    return profiles[profile](**asdict(base))
