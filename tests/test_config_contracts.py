from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

import uquant
from uquant.config import DEFAULT_CONFIG

INVALID_OVERRIDES: tuple[tuple[dict[str, Any], str], ...] = (
    ({"initial_cash": 0}, "initial_cash"),
    ({"max_gross": 0}, "max_gross"),
    ({"max_symbol_weight": 0.61}, "max_symbol_weight"),
    ({"max_positions": 7}, "max_positions"),
    ({"emerging_min_history": 19}, "emerging_min_history"),
    ({"leader_mature_score": 1.1}, "leader_mature_score"),
    ({"commission_rate": -0.1}, "commission_rate"),
    ({"production_stage": "UNKNOWN"}, "production_stage"),
    ({"concentrated_crisis_gross": 0.95}, "crisis/recovery"),
    ({"severe_recovery_gross": 0.60}, "differentiated recovery"),
    ({"caution_gross": 1.1}, "caution gross"),
    ({"caution_gross_min_votes": 0}, "caution gross minimum"),
    ({"sector_guard_min_symbols": 1}, "sector_guard_min_symbols"),
    ({"sector_shock_return": 0.0}, "sector_shock_return"),
    ({"sector_shock_breadth": 1.1}, "sector_shock_breadth"),
    ({"sector_shock_confirmations": 5}, "sector shock confirmation"),
    ({"sector_guard_divergence": -0.1}, "sector_guard_divergence"),
    ({"sector_guard_gross": 1.1}, "sector_guard_gross"),
    ({"sector_guard_min_sessions": 0}, "sector guard recovery windows"),
    ({"sector_recovery_breadth": 1.1}, "sector_recovery_breadth"),
    ({"sector_recovery_confirmations": 0}, "sector_recovery_confirmations"),
    ({"trend_entry_gross": 1.1}, "trend gross"),
    ({"satellite_weight": 0.61}, "satellite weight"),
    ({"add1_weight": 0.50, "add2_weight": 0.40}, "add tranche"),
    ({"add1_min_mfe": 0.20, "add2_min_mfe": 0.10}, "MFE"),
    ({"add_tranche_cooldown_sessions": 0}, "cooldown"),
    ({"add_index_chase_ret5": 1.0}, "add_index_chase_ret5"),
    ({"replacement_edge": 1.1}, "replacement_edge"),
    ({"industry_signal_min_members": 0}, "industry_signal_min_members"),
    ({"industry_rotation_edge": 1.1}, "industry_rotation_edge"),
    ({"replacement_confirm_days": 0}, "replacement confirmation"),
    ({"replacement_transfer_cap": 0.0}, "replacement transfer cap"),
    ({"max_satellites": 3}, "max_satellites"),
    ({"dynamic_k_expand_interval": 21}, "dynamic K change"),
    ({"dynamic_k_confirm_days": 0}, "dynamic_k_confirm_days"),
    ({"recovery_cohort_weak_graduation_days": 19}, "graduation days"),
    ({"recovery_cohort_weak_market_ret120": 0.0}, "weak_market_ret120"),
    ({"recovery_weak_market_min_index_ret60": 1.0}, "min_index_ret60"),
    ({"leader_cycle_confirm_days": 0}, "leader_cycle_confirm_days"),
    ({"leader_cycle_min_mature": 7}, "leader_cycle_min_mature"),
    ({"leader_cycle_min_score": 1.1}, "leader_cycle_min_score"),
    ({"leader_cycle_impulse_breadth": 1.1}, "impulse_breadth"),
    ({"leader_cycle_min_market_ret120": 1.0}, "min_market_ret120"),
    ({"strategic_cohort_symbols": ("a", "a", "b")}, "three unique"),
    ({"strategic_cohort_min_ret240": -0.1}, "min_ret240"),
    ({"strategic_cohort_confirm_days": 0}, "cohort_confirm_days"),
    ({"strategic_reversal_max_ret240": 0.0}, "reversal_max_ret240"),
    ({"strategic_reversal_min_ret5": 0.0}, "reversal_min_ret5"),
    ({"strategic_reversal_min_median_ret20": 0.1}, "median_ret20"),
    ({"strategic_reversal_max_tech_ret120": 0.1}, "max_tech_ret120"),
    ({"strategic_reversal_confirm_days": 0}, "reversal_confirm_days"),
    ({"strategic_cohort_profit_arm": 1.1}, "cohort_profit_arm"),
    ({"strategic_cohort_trail_atr": 0.0}, "trailing distances"),
    ({"strategic_cohort_trail_bands": 4}, "trail_bands"),
    ({"strategic_cohort_exit_step": 0.0}, "cohort exit step"),
    ({"strategic_cohort_disaster_stop": 0.0}, "disaster stop"),
    ({"strategic_cohort_tail_line": 0.05}, "cohort tail line"),
    ({"strategic_cohort_residual_gross": 0.50}, "residual gross guard"),
    ({"strategic_cohort_crisis_gross": 0.20}, "cohort crisis gross"),
    ({"strategic_cohort_guard_days": 0}, "cohort_guard_days"),
    ({"capital_guard_cooldown_days": 0}, "capital_guard_cooldown_days"),
    ({"capital_guard_min_recovery_days": 0}, "capital_guard_min_recovery_days"),
    ({"capital_guard_relapse_dd": 0.09}, "relapse_dd"),
    ({"strategic_cohort_tail_confirm_days": 0}, "tail_confirm_days"),
    ({"fast_v_recovery_confirm_days": 0}, "fast_v_recovery_confirm_days"),
    ({"persistent_v_recovery_wait_days": 4}, "persistent V-recovery"),
    ({"fast_v_recovery_breadth": 1.1}, "breadth thresholds"),
    ({"fast_v_recovery_gross": 1.0}, "fast V-recovery gross"),
    ({"narrow_anchor_guard_gross": 0.70}, "narrow anchor guard gross"),
    ({"narrow_anchor_divergence": 1.1}, "narrow_anchor_divergence"),
    ({"capital_dd_risk_off": 0.05}, "drawdown thresholds"),
    ({"incomplete_universe_crisis_gross": 0.80}, "incomplete-universe crisis gross"),
    ({"incomplete_universe_rearm_days": 91}, "incomplete-universe rearm"),
    ({"recovery_reserve_min_score": 1.1}, "recovery reserve score"),
    (
        {"recovery_reserve_min_ret120": 0.50, "recovery_reserve_min_ret60": 0.10},
        "recovery reserve return",
    ),
    ({"recovery_substitution_edge": 1.1}, "recovery substitution edge"),
    ({"recovery_substitution_shock_window": 0}, "substitution shock window"),
    ({"unbacked_universe_tail_dd": 0.09}, "unbacked universe tail"),
    ({"unbacked_recovery_anchor_min_days": 0}, "unbacked recovery anchor"),
)


@pytest.mark.parametrize(("changes", "message"), INVALID_OVERRIDES)
def test_every_configuration_safety_contract_fails_closed(
    changes: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DEFAULT_CONFIG.override(**changes)


def test_configuration_serialization_is_complete_and_detached() -> None:
    payload = DEFAULT_CONFIG.to_dict()
    assert payload["sector_guard_enabled"] is True
    assert payload["industry_rotation_enabled"] is True
    payload["max_gross"] = 0.0
    assert DEFAULT_CONFIG.max_gross == 1.0


def test_package_and_project_versions_stay_in_sync() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert uquant.__version__ == project["project"]["version"]
