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
