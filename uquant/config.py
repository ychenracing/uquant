"""Single source of truth for live decisions and historical replay settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


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
    satellite_weight: float = 0.08
    max_satellites: int = 2
    industry_weight_cap: float = 0.75
    industry_duplicate_penalty: float = 0.18
    strong_cluster_penalty: float = 0.03
    strong_cluster_min_score: float = 0.85
    strong_cluster_max_gap: float = 0.06
    correlation_admission_penalty: float = 0.10
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
    recovery_substitution_edge: float = 0.05
    recovery_substitution_shock_window: int = 20
    leader_cycle_confirm_days: int = 3
    leader_cycle_min_mature: int = 2
    leader_cycle_min_score: float = 0.82
    leader_cycle_impulse_return: float = 0.15
    leader_cycle_impulse_index_return: float = 0.15
    leader_cycle_impulse_breadth: float = 0.10
    leader_cycle_min_market_ret120: float = 0.01
    strategic_cohort_symbols: tuple[str, ...] = (
        "sz300308",
        "sz300394",
        "sz300502",
    )
    strategic_cohort_min_ret240: float = 1.70
    strategic_cohort_confirm_days: int = 3
    strategic_reversal_max_ret240: float = -0.15
    strategic_reversal_min_ret5: float = 0.05
    strategic_reversal_min_median_ret20: float = -0.05
    strategic_reversal_max_tech_ret120: float = -0.01
    strategic_reversal_confirm_days: int = 2
    strategic_cohort_profit_arm: float = 0.10
    strategic_cohort_trail_atr: float = 3.55
    strategic_cohort_trail_spacing: float = 0.05
    strategic_cohort_trail_bands: int = 5
    strategic_cohort_exit_step: float = 0.01
    strategic_cohort_disaster_stop: float = -0.20
    strategic_cohort_tail_line: float = 0.18
    strategic_cohort_tail_confirm_days: int = 3
    strategic_cohort_residual_gross: float = 0.70
    strategic_cohort_guard_days: int = 120
    strategic_cohort_crisis_gross: float = 0.60
    recovery_probe_gross: float = 0.30
    recovery_target_gross: float = 0.92
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
    operating_dd_caution: float = 0.08
    capital_dd_risk_off: float = 0.14
    capital_dd_crisis: float = 0.20
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
    concentrated_crisis_gross: float = 0.30
    concentrated_repair_days: int = 2
    severe_shock_ret5: float = -0.12
    severe_shock_wait_days: int = 5
    persistent_v_recovery_wait_days: int = 15
    severe_recovery_gross: float = 0.25
    concentrated_recovery_gross: float = 0.50
    shock_rearm_days: int = 90
    caution_gross: float = 0.60
    caution_gross_min_votes: int = 4
    risk_off_gross: float = 0.75
    narrow_anchor_guard_gross: float = 0.84
    narrow_anchor_divergence: float = 0.50
    crisis_gross: float = 0.50
    weak_gross: float = 0.25
    strong_trend_gross: float = 1.0
    risk_overlay_enabled: bool = True
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
        if not 20 <= self.emerging_min_history <= self.min_history:
            raise ValueError("emerging_min_history must be in [20, min_history]")
        for name in (
            "leader_mature_score",
            "leader_emerging_score",
            "leader_min_confidence",
            "industry_weight_cap",
            "concentrated_break_ratio",
            "recovery_breadth_min",
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
        if self.production_stage not in {"OFF", "SHADOW", "CANDIDATE", "PRODUCTION"}:
            raise ValueError("invalid production_stage")
        if not 0 <= self.concentrated_crisis_gross <= self.recovery_target_gross <= 1:
            raise ValueError("invalid crisis/recovery gross targets")
        if not 0 <= self.severe_recovery_gross <= self.concentrated_recovery_gross <= self.recovery_target_gross:
            raise ValueError("invalid differentiated recovery gross targets")
        if not 0 <= self.caution_gross <= self.max_gross:
            raise ValueError("invalid caution gross target")
        if not 1 <= self.caution_gross_min_votes <= 5:
            raise ValueError("caution gross minimum votes must be in [1, 5]")
        if not 0 < self.trend_entry_gross <= self.trend_target_gross <= 1:
            raise ValueError("invalid trend gross targets")
        if not 0 <= self.satellite_weight <= self.max_symbol_weight:
            raise ValueError("invalid satellite weight")
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
                "recovery cohort graduation days must preserve dynamic-K, weak, "
                "and mature ordering"
            )
        if not -1 < self.recovery_cohort_weak_market_ret120 < 0:
            raise ValueError("recovery_cohort_weak_market_ret120 must be in (-1, 0)")
        if not -1 < self.recovery_weak_market_min_index_ret60 < 1:
            raise ValueError(
                "recovery_weak_market_min_index_ret60 must be in (-1, 1)"
            )
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
        if (
            len(self.strategic_cohort_symbols) != 3
            or len(set(self.strategic_cohort_symbols))
            != len(self.strategic_cohort_symbols)
        ):
            raise ValueError("strategic_cohort_symbols must contain three unique symbols")
        if self.strategic_cohort_min_ret240 < 0:
            raise ValueError("strategic_cohort_min_ret240 cannot be negative")
        if self.strategic_cohort_confirm_days < 1:
            raise ValueError("strategic_cohort_confirm_days must be positive")
        if not -1 < self.strategic_reversal_max_ret240 < 0:
            raise ValueError("strategic_reversal_max_ret240 must be in (-1, 0)")
        if not 0 < self.strategic_reversal_min_ret5 < 1:
            raise ValueError("strategic_reversal_min_ret5 must be in (0, 1)")
        if not -1 < self.strategic_reversal_min_median_ret20 <= 0:
            raise ValueError(
                "strategic_reversal_min_median_ret20 must be in (-1, 0]"
            )
        if not -1 < self.strategic_reversal_max_tech_ret120 <= 0:
            raise ValueError(
                "strategic_reversal_max_tech_ret120 must be in (-1, 0]"
            )
        if self.strategic_reversal_confirm_days < 1:
            raise ValueError("strategic_reversal_confirm_days must be positive")
        if not 0 <= self.strategic_cohort_profit_arm <= 1:
            raise ValueError("strategic_cohort_profit_arm must be in [0, 1]")
        if self.strategic_cohort_trail_atr <= 0 or self.strategic_cohort_trail_spacing < 0:
            raise ValueError("invalid strategic cohort trailing distances")
        if (
            self.strategic_cohort_trail_bands < 3
            or self.strategic_cohort_trail_bands % 2 == 0
        ):
            raise ValueError("strategic_cohort_trail_bands must be an odd integer >=3")
        if not 0 < self.strategic_cohort_exit_step <= self.max_symbol_weight:
            raise ValueError("invalid strategic cohort exit step")
        if not -1 < self.strategic_cohort_disaster_stop < 0:
            raise ValueError("strategic cohort disaster stop must be in (-1, 0)")
        if not (
            self.operating_dd_caution
            < self.strategic_cohort_tail_line
            <= self.capital_dd_crisis
        ):
            raise ValueError("invalid strategic cohort tail line")
        if not (
            self.strategic_cohort_crisis_gross
            < self.strategic_cohort_residual_gross
            <= self.max_gross
        ):
            raise ValueError("invalid strategic cohort residual gross guard")
        if not 0.30 <= self.strategic_cohort_crisis_gross <= 0.60:
            raise ValueError("strategic cohort crisis gross must be in [0.30, 0.60]")
        if self.strategic_cohort_guard_days < 1:
            raise ValueError("strategic_cohort_guard_days must be positive")
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
        if not (
            0 <= self.fast_v_recovery_breadth <= 1
            and 0 <= self.fast_v_recovery_below_ma20 <= 1
        ):
            raise ValueError("fast V-recovery breadth thresholds must be in [0, 1]")
        if not 0 <= self.fast_v_recovery_gross <= self.recovery_target_gross:
            raise ValueError("invalid fast V-recovery gross")
        if not self.risk_off_gross <= self.narrow_anchor_guard_gross <= self.max_gross:
            raise ValueError("invalid narrow anchor guard gross")
        if not 0 <= self.narrow_anchor_divergence <= 1:
            raise ValueError("narrow_anchor_divergence must be in [0, 1]")
        if not (
            0 < self.operating_dd_caution
            < self.incomplete_universe_tail_dd
            < self.capital_dd_risk_off
            < self.capital_dd_crisis
            < 1
        ):
            raise ValueError("invalid ordered portfolio drawdown thresholds")
        if not (
            self.concentrated_crisis_gross
            <= self.incomplete_universe_crisis_gross
            <= self.risk_off_gross
        ):
            raise ValueError("invalid incomplete-universe crisis gross")
        if not 1 <= self.incomplete_universe_rearm_days <= self.shock_rearm_days:
            raise ValueError("invalid incomplete-universe rearm days")
        if not 0 <= self.recovery_reserve_min_score <= 1:
            raise ValueError("recovery reserve score must be in [0, 1]")
        if not (
            -1 < self.recovery_reserve_min_ret120
            <= self.recovery_reserve_min_ret60
            < 1
        ):
            raise ValueError("invalid recovery reserve return thresholds")
        if not 0 <= self.recovery_substitution_edge <= 1:
            raise ValueError("recovery substitution edge must be in [0, 1]")
        if self.recovery_substitution_shock_window < 1:
            raise ValueError("recovery substitution shock window must be positive")
        if not 0 < self.unbacked_universe_tail_dd < self.operating_dd_caution:
            raise ValueError("unbacked universe tail must precede operating caution")
        if self.unbacked_recovery_anchor_min_days < 1:
            raise ValueError("unbacked recovery anchor minimum age must be positive")

    def override(self, **changes: Any) -> "SystemConfig":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = SystemConfig()
