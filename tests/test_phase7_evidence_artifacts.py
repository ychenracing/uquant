from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from uquant.types import AccountState

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "sentinel" / "exclusive_freeze"


def _load(name: str) -> dict[str, object]:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def test_phase6_baseline_is_exact_and_preserves_account_schema() -> None:
    payload = _load("phase6_baseline.json")

    assert payload["schema"] == "uquant.sentinel-exclusive-freeze-phase6-baseline.v1"
    assert payload["start_main_commit"] == (
        "711af1179aa72ce48ca3a6af58ecddb3a029a7ce"
    )
    assert payload["start_tree"] == "1889abb78f708f8fe1e1a80f7eb0b6ad4e2d2e14"
    assert payload["baseline_config_sha256"] == (
        "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
    )
    assert payload["locked_candidate_config_sha256"] == (
        "b75d02d238e7ea18793c6f15727b34bc15b7a002b5ec4c4e620f86f1c39c93fa"
    )
    assert payload["baseline_code_sha256"] == (
        "7ba223f678ffc3c26ffb254985108c81ebac5c57ea298b0d898219825263a990"
    )
    assert payload["fixed_parameters"] == {
        "mode": "FREEZE_ONLY",
        "min_confidence": 0.8,
        "confirm_days": 2,
        "repair_days": 3,
        "severe_direct_enabled": True,
        "gross_cap": None,
    }
    assert payload["production_default_enabled"] is False
    assert payload["account_state_fields"] == [field.name for field in fields(AccountState)]
    assert payload["baseline_test_suite"] == {"passed": 1434, "failed": 0}


def test_candidate_lock_forbids_retuning_and_non_freeze_authority() -> None:
    payload = _load("candidate_lock.json")

    assert payload == {
        "schema": "uquant.sentinel-exclusive-freeze-candidate-lock.v1",
        "baseline_commit": "711af1179aa72ce48ca3a6af58ecddb3a029a7ce",
        "candidate_change": {
            "risk_sentinel_causal_confirmation_enabled": True,
        },
        "fixed_parameters": {
            "mode": "FREEZE_ONLY",
            "min_confidence": 0.8,
            "confirm_days": 2,
            "repair_days": 3,
            "severe_direct_enabled": True,
            "gross_cap": None,
        },
        "config_sha256": {
            "baseline": (
                "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
            ),
            "candidate": (
                "b75d02d238e7ea18793c6f15727b34bc15b7a002b5ec4c4e620f86f1c39c93fa"
            ),
        },
        "comparable_history_families": [
            "breadth_structure",
            "covariance_stress",
            "market_velocity",
        ],
        "current_day_diagnostic_only_families": [
            "capital_damage",
            "live_book_damage",
        ],
        "forbidden_authorities": [
            "account_state_schema",
            "capital_budget_level",
            "direct_sell",
            "fill",
            "reduction_level",
            "risk_action",
            "risk_gross_cap",
            "risk_state",
            "shock_state",
            "target_gross_cap",
        ],
        "forbidden_retuning": True,
        "official_economic_matrix_started": False,
    }


def test_atomicity_boundary_records_rotation_and_broker_visible_buy_rules() -> None:
    payload = _load("atomicity_boundary.json")

    assert payload == {
        "schema": "uquant.sentinel-exclusive-freeze-atomicity-boundary.v1",
        "rotation": {
            "one_to_one_replacement_sell_suppressed_with_blocked_buy": True,
            "multi_symbol_replacement_sells_suppressed_with_blocked_buys": True,
            "independent_strategy_exit_preserved": True,
            "healthy_holdings_preserved": True,
        },
        "pending_buy": {
            "unsubmitted_buy_cancelled": True,
            "broker_visible_buy_uses_cancel_requested": True,
            "broker_confirmation_required_for_terminal_cancel": True,
            "partial_fill_quantity_and_identity_preserved": True,
            "remaining_expansion_blocked": True,
            "duplicate_replacement_blocked_until_terminal": True,
            "independent_sell_preserved": True,
        },
        "production_source_change_required": False,
        "new_account_state_fields": 0,
    }


def test_small_gate_rejects_before_full_matrix_without_authority_pollution() -> None:
    payload = _load("small_gate.json")

    assert payload["decision"] == "REJECT"
    assert payload["hard_gate"] == {
        "target_gross_cap_equal_to_base": True,
        "sentinel_direct_sell_count": 0,
        "sentinel_risk_gross_cap_event_count": 0,
        "healthy_holding_reduction_count": 0,
        "risk_state_drift_count": 0,
        "reduction_level_drift_count": 0,
        "shock_state_drift_count": 0,
        "capital_budget_level_drift_count": 0,
        "new_account_state_fields": 0,
        "passed": True,
    }
    assert payload["blocked_new_risk_detection"] == {
        "unsubmitted_planned_buy_absence": True,
        "broker_cancel_requested": True,
        "partial_fill_remaining_expansion": True,
        "stable_order_or_event_identity": True,
        "event_id_only_churn_is_not_blocked_risk": True,
    }
    assert payload["value_gate"] == {
        "required_qualifying_non_severe_events": 1,
        "observed_qualifying_non_severe_events": 0,
        "passed": False,
    }
    stop = payload["stop_early"]
    assert isinstance(stop, dict)
    assert stop == {
        "triggered": True,
        "phase1_matrix_run": False,
        "phase2_six_window_matrix_run": False,
        "generalization_matrix_run": False,
        "parameter_search_run": False,
        "gross_cap_restarted": False,
    }
    cells = payload["cells"]
    assert isinstance(cells, dict)
    for cell in cells.values():
        assert isinstance(cell, dict)
        assert cell["baseline"] == cell["candidate"]


def test_first_divergence_and_all_exclusive_events_are_preserved() -> None:
    first = _load("first_divergence.json")
    events = _load("exclusive_freeze_events.json")

    assert first["date"] == "2024-06-25"
    assert first["changed_fields"] == ["risk", "targets"]
    causal = first["causal_evidence"]
    assert isinstance(causal, dict)
    assert causal["confirmation_history_trusted"] is True
    assert causal["confirmation_days"] == 2
    assert causal["comparison_class"] == "incremental_same_day"
    assert causal["incremental_families"] == ["market_velocity"]
    assert causal["base_active_families"] == ["breadth_structure"]
    assert causal["sentinel_active_families"] == [
        "breadth_structure",
        "market_velocity",
    ]
    assert causal["derived_incremental_families"] == ["market_velocity"]
    assert causal["timeline_verification"] == {
        "sessions_aligned": True,
        "full_warmup_prefix_recomputed": True,
        "account_derived_history_used": False,
        "first_family_maps_recomputed_at_authority_boundary": True,
    }
    effect = first["economic_effect"]
    assert isinstance(effect, dict)
    assert effect["blocked_new_risk_count"] == 0

    assert events["event_count"] == 1
    assert events["qualifying_value_event_count"] == 0
    raw_events = events["events"]
    assert isinstance(raw_events, list)
    assert len(raw_events) == 1
    event = raw_events[0]
    assert isinstance(event, dict)
    assert event["date"] == "2024-06-25"
    assert event["sentinel_exclusive_freeze"] is True
    assert event["blocked_new_risk_count"] == 0
    assert event["sentinel_direct_sell_count"] == 0
    assert event["healthy_holding_reduction_count"] == 0


def test_rejected_candidate_code_identity_migration_is_economically_exact() -> None:
    payload = _load("account_code_identity_migration.json")

    assert payload["status"] == "PASS"
    assert payload["schema_version_before"] == payload["schema_version_after"] == 5
    assert payload["economic_state_sha256_before"] == payload[
        "economic_state_sha256_after"
    ]
    assert payload["changed_fields"] == ["code_hash", "account_migrations[-1]"]
    event = payload["migration_event"]
    assert isinstance(event, dict)
    assert event["migration_type"] == "code_identity_only"


def test_final_decision_keeps_rejected_candidate_out_of_main_and_holdout() -> None:
    payload = _load("final_decision.json")

    assert payload["decision"] == "REJECT"
    assert payload["small_gate"]["sentinel_exclusive_freeze_events"] == 1
    assert payload["small_gate"]["qualifying_events_that_blocked_new_risk"] == 0
    assert payload["release_actions"] == {
        "merge_candidate_to_main": False,
        "main_remains_phase6": True,
        "future_holdout_lane_registered": False,
        "stable_tag_created": False,
        "active_sentinel_expansion_stopped": True,
    }
    assert payload["account_code_identity_migration"] == {
        "status": "PASS",
        "economic_state_exact": True,
        "final_production_code_sha256": (
            "ffe0f742884f3284cbbeef963dcfa3584536be15982464a281ee049f1098a64b"
        ),
        "evidence": "account_code_identity_migration.json",
    }
    assert payload["engineering"]["status"] == "PASS"
    assert payload["engineering"]["pytest_passed"] == 1464
    assert payload["engineering"]["branch_coverage_percent"] >= 85.0
