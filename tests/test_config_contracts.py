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
    ({"max_volume_participation": 1.01}, "max_volume_participation"),
    ({"restoration_min_trade_weight": 0.06}, "restoration_min_trade_weight"),
    ({"concentrated_crisis_gross": 0.95}, "crisis/recovery"),
    ({"severe_recovery_gross": 0.60}, "differentiated recovery"),
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
    ({"recovery_cohort_tail_guard_days": 59}, "recovery cohort tail guard"),
    ({"recovery_cohort_tail_line": 0.05}, "recovery cohort tail line"),
    ({"recovery_transition_weak_leg_ret120": 0.0}, "weak-leg return"),
    ({"recovery_transition_strong_leg_max_ret120": 0.0}, "strong-leg return"),
    ({"recovery_transition_min_divergence": 0.0}, "transition divergence"),
    ({"recovery_member_confirm_days": 1}, "at least 2"),
    ({"leader_cycle_confirm_days": 0}, "leader_cycle_confirm_days"),
    ({"leader_cycle_min_mature": 7}, "leader_cycle_min_mature"),
    ({"leader_cycle_min_score": 1.1}, "leader_cycle_min_score"),
    ({"leader_cycle_impulse_breadth": 1.1}, "impulse_breadth"),
    ({"leader_cycle_min_market_ret120": 1.0}, "min_market_ret120"),
    ({"strategic_cohort_symbols": ("arbitrary_a",)}, "retired"),
    ({"strategic_epoch_cooldown_sessions": 19}, "epoch cooldown"),
    ({"strategic_epoch_min_symbol_change": 0}, "epoch symbol change"),
    ({"strategic_long_cycle_max_tech_ret120": 0.0}, "long_cycle_max_tech_ret120"),
    ({"strategic_long_cycle_min_ret60": -1.0}, "long_cycle_min_ret60"),
    ({"strategic_long_cycle_min_ret120": -1.0}, "long_cycle_min_ret120"),
    ({"strategic_current_factor_floor": 1.1}, "current_factor_floor"),
    ({"strategic_transition_min_score": 1.1}, "transition_min_score"),
    ({"strategic_transition_min_component": 1.1}, "transition_min_component"),
    ({"strategic_transition_impulse_min_history": 240}, "transition_impulse_min_history"),
    ({"strategic_transition_impulse_min_score": 1.1}, "transition_impulse_min_score"),
    ({"strategic_transition_impulse_min_leader_score": 1.1}, "transition_impulse_min_leader_score"),
    ({"strategic_transition_impulse_min_secular_score": 1.1}, "transition_impulse_min_secular_score"),
    (
        {"strategic_transition_impulse_min_secular_confidence": 1.1},
        "transition_impulse_min_secular_confidence",
    ),
    ({"strategic_transition_impulse_min_ret20": -1.0}, "transition_impulse_min_ret20"),
    ({"strategic_transition_impulse_min_ret60": -1.0}, "transition_impulse_min_ret60"),
    ({"strategic_transition_impulse_min_ret120": -1.0}, "transition_impulse_min_ret120"),
    ({"strategic_transition_impulse_max_ret120": 1.0}, "transition_impulse_max_ret120"),
    (
        {
            "strategic_transition_impulse_min_ret120": 0.10,
            "strategic_transition_impulse_max_ret120": 0.10,
        },
        "ret120 bounds",
    ),
    (
        {"strategic_transition_impulse_min_market_ret20": -1.0},
        "transition_impulse_min_market_ret20",
    ),
    ({"strategic_cohort_confirm_days": 0}, "cohort_confirm_days"),
    ({"strategic_cohort_profit_arm": 1.1}, "cohort_profit_arm"),
    ({"strategic_cohort_trail_atr": 0.0}, "trailing distances"),
    ({"strategic_cohort_trail_bands": 4}, "trail_bands"),
    ({"strategic_cohort_exit_step": 0.0}, "cohort exit step"),
    ({"strategic_cohort_disaster_stop": 0.0}, "disaster stop"),
    ({"strategic_cohort_tail_line": 0.05}, "cohort tail line"),
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
    ({"recovery_substitution_max_ret20": 1.0}, "recovery substitution max ret20"),
    ({"recovery_substitution_shock_window": 0}, "substitution shock window"),
    ({"unbacked_universe_tail_dd": 0.09}, "unbacked universe tail"),
    ({"unbacked_recovery_anchor_min_days": 0}, "unbacked recovery anchor"),
    ({"risk_anchor_count": 0}, "risk_anchor_count"),
    ({"risk_anchor_min_groups": 4}, "risk_anchor_min_groups"),
    ({"risk_anchor_confirm_days": 0}, "risk_anchor_confirm_days"),
    ({"sector_weighted_shock_return": 0.0}, "sector_weighted_shock_return"),
    ({"sector_weighted_negative_exposure": 1.1}, "sector_weighted_negative_exposure"),
    ({"challenger_scout_incumbent_hysteresis": 0.21}, "scout incumbent hysteresis"),
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
    assert payload["sector_guard_gross"] == pytest.approx(0.40)
    assert payload["industry_rotation_enabled"] is True
    assert payload["strategic_cohort_symbols"] == ()
    assert payload["strategic_dynamic_enabled"] is True
    assert payload["dynamic_risk_anchors_enabled"] is True
    assert payload["strategic_epoch_cooldown_sessions"] == 30
    assert payload["risk_anchor_confirm_days"] == 5
    assert payload["sector_weighted_shock_return"] == pytest.approx(-0.024)
    assert payload["sector_weighted_negative_exposure"] == pytest.approx(0.70)
    assert payload["recovery_cohort_tail_guard_days"] == 90
    assert payload["recovery_cohort_tail_line"] == pytest.approx(0.12)
    assert payload["recovery_transition_weak_leg_ret120"] == pytest.approx(-0.08)
    assert payload["recovery_transition_strong_leg_max_ret120"] == pytest.approx(0.08)
    assert payload["recovery_transition_min_divergence"] == pytest.approx(0.10)
    assert payload["recovery_substitution_max_ret20"] == pytest.approx(0.30)
    payload["max_gross"] = 0.0
    assert DEFAULT_CONFIG.max_gross == 1.0


def test_package_and_project_versions_stay_in_sync() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert uquant.__version__ == project["project"]["version"]
