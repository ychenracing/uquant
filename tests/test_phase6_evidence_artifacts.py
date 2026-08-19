from __future__ import annotations

import json
from pathlib import Path


def _artifact(name: str) -> dict[str, object]:
    return json.loads(
        (Path("artifacts/sentinel/phase6") / name).read_text(encoding="utf-8")
    )


def test_zero_economic_equivalence_covers_every_required_carrier() -> None:
    payload = _artifact("zero_economic_equivalence.json")
    hashes = payload["exact_equal_hashes"]
    assert isinstance(hashes, dict)

    assert payload["status"] == "PASS"
    assert {
        "decision_digests_sha256",
        "canonical_decisions_sha256",
        "risk_controls_sha256",
        "targets_and_event_ids_sha256",
        "pending_order_intents_sha256",
        "order_ledger_sha256",
        "fills_sha256",
        "pending_orders_sha256",
        "economic_account_projection_sha256",
        "economic_state_sha256",
    } == set(hashes)
    assert all(isinstance(value, str) and len(value) == 64 for value in hashes.values())
    assert payload["exact_equal_metrics"] == {
        "final_wealth": 1.9042531401193852,
        "max_drawdown": 0.156742775678121,
        "account_orders": 8,
        "gross_turnover": 2.0503083589999997,
        "annual_turnover": 4.204869685406779,
        "acute_return": 0.06390679898215934,
    }


def test_account_migration_changes_only_code_identity_fields() -> None:
    payload = _artifact("account_code_identity_migration.json")

    assert payload["status"] == "PASS"
    assert payload["schema_version_before"] == payload["schema_version_after"] == 5
    assert (
        payload["economic_state_sha256_before"]
        == payload["economic_state_sha256_after"]
    )
    assert payload["changed_fields"] == ["code_hash", "account_migrations[-1]"]
    assert payload["migration_event"] == {
        "migration_type": "code_identity_only",
        "migrated_at_utc": "2026-08-19T15:17:04.044702+00:00",
        "from_schema": 5,
        "to_schema": 5,
    }
