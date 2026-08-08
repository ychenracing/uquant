"""Single source of truth for production and validation configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class SystemConfig:
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
    target_hysteresis: float = 0.08
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
    replacement_edge: float = 0.10
    replacement_confirm_days: int = 3
    min_hold_days: int = 10
    max_rotations_20d: int = 2
    add1_min_mfe: float = 0.04
    add2_min_mfe: float = 0.10
    recovery_probe_gross: float = 0.30
    recovery_target_gross: float = 0.895
    recovery_crash_drawdown: float = 0.15
    recovery_crash_lookback: int = 20
    recovery_stabilize_days: int = 8
    recovery_breadth_min: float = 0.55
    recovery_add_window_days: int = 5
    one_anchor_gross_cap: float = 0.35
    two_anchor_gross_cap: float = 0.55
    tactical_rebound_weight: float = 0.60
    tactical_rebound_take_profit: float = 0.065
    tactical_rebound_cooldown_days: int = 30
    recovery_confirm_days: int = 2
    recovery_cooldown_days: int = 10
    caution_confirm_days: int = 2
    risk_off_confirm_days: int = 2
    crisis_confirm_days: int = 1
    recovery_risk_confirm_days: int = 3
    risk_fast_return: float = -0.045
    risk_breadth: float = 0.65
    risk_below_ma20: float = 0.65
    risk_correlation: float = 0.75
    risk_volatility_ratio: float = 1.80
    operating_dd_caution: float = 0.08
    capital_dd_risk_off: float = 0.14
    capital_dd_crisis: float = 0.20
    concentrated_break_dd: float = 0.08
    concentrated_break_ratio: float = 0.67
    concentrated_break_confirm_days: int = 2
    portfolio_break_dd: float = 0.18
    portfolio_break_votes: int = 2
    concentrated_crisis_gross: float = 0.0
    concentrated_repair_days: int = 2
    severe_shock_ret5: float = -0.12
    severe_shock_wait_days: int = 10
    severe_recovery_gross: float = 0.25
    shock_rearm_days: int = 90
    risk_off_gross: float = 0.80
    crisis_gross: float = 0.50
    weak_gross: float = 0.25
    strong_trend_gross: float = 1.0
    production_stage: str = "PRODUCTION"
    fail_closed: bool = True
    deterministic_seed: int = 20260808

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 0 < self.max_gross <= 1:
            raise ValueError("max_gross must be in (0, 1]")
        if not 0 < self.max_symbol_weight <= 0.60:
            raise ValueError("max_symbol_weight must be in (0, 0.60]")
        if not 1 <= self.max_positions <= 6:
            raise ValueError("max_positions must be in [1, 6]")
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
        if self.production_stage not in {"OFF", "SHADOW", "CANDIDATE", "PRODUCTION"}:
            raise ValueError("invalid production_stage")
        if not 0 <= self.concentrated_crisis_gross <= self.recovery_target_gross <= 1:
            raise ValueError("invalid crisis/recovery gross targets")

    def override(self, **changes: Any) -> "SystemConfig":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = SystemConfig()
