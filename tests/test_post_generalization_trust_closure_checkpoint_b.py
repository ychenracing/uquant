from __future__ import annotations

from pathlib import Path

from research.post_generalization_trust_closure_checkpoint_b import (
    EXECUTION_STRESS_SPECS,
    PARAMETER_VARIANT_SPECS,
    run_execution_stresses,
)
from uquant.contracts.strict_json import canonical_json_sha256, strict_json_loads

EVIDENCE = Path("benchmarks/post_generalization_trust_closure_checkpoint_b.json")

EXPECTED_PARAMETER_VARIANTS = {
    "P1_LOWER": ("strategic_reversal_max_ret240", -0.18),
    "P1_UPPER": ("strategic_reversal_max_ret240", -0.12),
    "P2_LOWER": ("strategic_reversal_min_ret5", 0.04),
    "P2_UPPER": ("strategic_reversal_min_ret5", 0.06),
    "P3_LOWER": ("strategic_reversal_min_median_ret20", -0.06),
    "P3_UPPER": ("strategic_reversal_min_median_ret20", -0.04),
    "P4_LOWER": ("strategic_reversal_max_tech_ret120", -0.02),
    "P4_UPPER": ("strategic_reversal_max_tech_ret120", 0.0),
    "P5_LOWER": ("strategic_dominant_min_leader_gap", 0.04),
    "P5_UPPER": ("strategic_dominant_min_leader_gap", 0.06),
    "P6_LOWER": ("strategic_transition_min_component", 0.65),
    "P6_UPPER": ("strategic_transition_min_component", 0.75),
    "P7_LOWER": ("strategic_dominant_profit_lock_mfe", 1.98),
    "P7_UPPER": ("strategic_dominant_profit_lock_mfe", 2.42),
    "P8_LOWER": ("strategic_dominant_retained_gross", 0.65),
    "P8_UPPER": ("strategic_dominant_retained_gross", 0.75),
}


def test_checkpoint_b_predeclared_budgets_cannot_turn_into_search() -> None:
    assert {
        spec.case_id: (spec.field, spec.value) for spec in PARAMETER_VARIANT_SPECS
    } == EXPECTED_PARAMETER_VARIANTS
    assert len(PARAMETER_VARIANT_SPECS) == 16
    assert tuple(spec.case_id for spec in EXECUTION_STRESS_SPECS) == (
        "S25",
        "S50",
        "S100",
        "S200",
        "P75",
        "P50",
        "P25",
        "B-UP",
        "B-DOWN",
        "B-SUSP",
        "B-CAP0",
    )


def test_execution_stresses_use_native_next_open_and_preserve_blocked_orders() -> None:
    evidence = run_execution_stresses()
    cases = {case["case_id"]: case for case in evidence["cases"]}

    assert set(cases) == {spec.case_id for spec in EXECUTION_STRESS_SPECS}
    assert all(case["same_signal_fill_count"] == 0 for case in cases.values())
    assert cases["P75"]["filled_shares"] == 7_500
    assert cases["P50"]["filled_shares"] == 5_000
    assert cases["P25"]["filled_shares"] == 2_500
    for case_id, ratio in (("P75", 0.75), ("P50", 0.50), ("P25", 0.25)):
        assert cases[case_id]["requested_shares"] == 10_000
        assert cases[case_id]["order_completion_ratio"] == ratio
    for case_id in ("B-UP", "B-DOWN", "B-SUSP", "B-CAP0"):
        assert cases[case_id]["blocked_sessions"] == 1
        assert cases[case_id]["fill_delay_sessions"] == 1
        assert cases[case_id]["pending_preserved_after_block"] is True
    assert all(all(case["invariants"].values()) for case in cases.values())
    assert evidence["scope"] == "DETERMINISTIC_ORDER_LEVEL_NATIVE_EXECUTION_STRESS"
    assert evidence["actual_broker_facts"] is False
    assert evidence["full_strategy_pnl"] is False
    assert evidence["worst_order_level_case"]["case_id"] == "S200"
    assert evidence["portfolio_level_outputs"] == {
        "stressed_wealth": None,
        "stressed_max_drawdown": None,
        "portfolio_turnover": None,
        "portfolio_opportunity_cost": None,
        "worst_key_trade": None,
        "degradation_vs_baseline": None,
        "status": "EVIDENCE GAP — ORDER_LEVEL SCOPE DOES NOT ESTABLISH PORTFOLIO PNL",
    }


def test_checkpoint_b_artifact_is_sealed_and_preserves_limits_and_gaps() -> None:
    payload = strict_json_loads(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)

    unsigned = dict(payload)
    claimed_sha256 = unsigned.pop("canonical_sha256")
    assert claimed_sha256 == canonical_json_sha256(unsigned)
    assert payload["schema_version"] == 1
    assert payload["authoritative_acceptance"] is False
    assert payload["future_holdout_used"] is False

    sensitivity = payload["parameter_sensitivity"]
    assert sensitivity["budget"] == {
        "baseline_replays": 1,
        "one_factor_variants": 16,
        "historical_production_engine_replays": 17,
        "grid_search": False,
        "selection": False,
    }
    assert sensitivity["window"] == {
        "start": "2023-01-03",
        "end": "2026-08-05",
        "future_holdout_boundary": "2026-08-06",
    }
    cases = sensitivity["cases"]
    assert len(cases) == 17
    assert cases[0]["case_id"] == "BASELINE"
    assert {
        case["case_id"]: (case["parameter"]["field"], case["parameter"]["value"]) for case in cases[1:]
    } == EXPECTED_PARAMETER_VARIANTS
    assert all(case["identity"] for case in cases)
    for case in cases:
        identity = dict(case["identity"])
        identity_sha256 = identity.pop("canonical_sha256")
        assert identity_sha256 == canonical_json_sha256(identity)
        assert len(identity["production_source_sha256"]) == 64
        assert len(identity["runner_source_sha256"]) == 64
        assert identity["frozen_data"]["files_verified"] == 36
        assert identity["runtime"]["uv_lock_sha256"]
        assert identity["effective_config_sha256"]
    assert all(case["status"] in {"SUCCESS", "INSUFFICIENT_SAMPLE", "REPLAY_ERROR"} for case in cases)
    assert {pair["classification"] for pair in sensitivity["pairs"]} <= {
        "STABLE",
        "SENSITIVE",
        "KNIFE_EDGE",
        "INACTIVE",
    }
    assert {pair["field"]: pair["classification"] for pair in sensitivity["pairs"]} == {
        "strategic_reversal_max_ret240": "INACTIVE",
        "strategic_reversal_min_ret5": "KNIFE_EDGE",
        "strategic_reversal_min_median_ret20": "INACTIVE",
        "strategic_reversal_max_tech_ret120": "KNIFE_EDGE",
        "strategic_dominant_min_leader_gap": "INACTIVE",
        "strategic_transition_min_component": "INACTIVE",
        "strategic_dominant_profit_lock_mfe": "KNIFE_EDGE",
        "strategic_dominant_retained_gross": "KNIFE_EDGE",
    }
    assert all(pair["default_is_best_positive_evidence"] is False for pair in sensitivity["pairs"])

    execution = payload["execution_stress"]
    assert len(execution["cases"]) == 11
    assert tuple(case["case_id"] for case in execution["cases"]) == tuple(
        spec.case_id for spec in EXECUTION_STRESS_SPECS
    )
    assert all(all(case["invariants"].values()) for case in execution["cases"])

    regimes = payload["regime_evidence"]
    assert regimes["observed_frozen_windows"]["ai_worst_5d"]["end"] == "2026-07-17"
    assert regimes["observed_frozen_windows"]["ai_worst_20d"]["end"] == "2026-07-28"
    assert regimes["observed_frozen_windows"]["optical_worst_5d"]["end"] == "2025-04-08"
    assert regimes["observed_frozen_windows"]["optical_worst_20d"]["end"] == "2026-07-28"
    assert set(regimes["evidence_gaps"]) == {
        "archived_state_trajectories",
        "formal_optical_failure_latencies",
        "real_rotation_events",
        "whipsaw",
    }
    assert regimes["checkpoint_a_no_optical_latency"]["observed_optical_failure"] is False

    utility = payload["state_guard_config_utility"]
    assert set(utility["allowed_classifications"]) == {
        "ACTIVE_USEFUL",
        "ACTIVE_REDUNDANT",
        "INACTIVE_REACHABLE",
        "UNREACHABLE",
        "COMPAT_ONLY",
        "DEAD",
    }
    compat = {row["name"]: row for row in utility["config_fields"] if row["classification"] == "COMPAT_ONLY"}
    assert set(compat) == {
        "hierarchical_industry_shrinkage_enabled",
        "group_balanced_reference_enabled",
        "same_day_leader_pipeline_enabled",
        "evidence_family_voting_enabled",
    }
    assert all(row["current_default_active"] is False for row in compat.values())
    assert all(row["deletion_checkpoint"] == "C" for row in compat.values())
    state_only = {
        row["name"]: row
        for row in utility["states_and_guards"]
        if row["name"] in {"chronic_overlay", "concentrated_break", "freeze"}
    }
    assert all(row["changed_target_sessions"] is None for row in state_only.values())
    assert all(row["deletion_changes_current_behavior"] is None for row in state_only.values())
