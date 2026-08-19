from __future__ import annotations

from pathlib import Path

import pytest

from uquant.account import (
    economic_state_sha256,
    load_account,
    migrate_code_identity,
    save_account,
)
from uquant.types import AccountState


def _state() -> AccountState:
    state = AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        operating_peak=2_100_000.0,
        capital_peak=2_200_000.0,
        data_hash="data-hash",
        code_hash="phase-3-code",
    )
    state.risk_streaks["CAUTION"] = 1
    state.pending_orders = []
    return state


def test_code_identity_migration_changes_only_hash_and_audit_event(
    tmp_path: Path,
) -> None:
    source = tmp_path / "account.json"
    destination = tmp_path / "migrated.json"
    before = _state()
    save_account(before, source)
    before_economic = economic_state_sha256(before)

    migrated = migrate_code_identity(
        source,
        destination,
        new_code_hash="phase-4-code",
        acknowledge_code_change=True,
    )
    reloaded = load_account(destination)

    assert migrated.to_dict() == reloaded.to_dict()
    assert migrated.code_hash == "phase-4-code"
    assert economic_state_sha256(migrated) == before_economic
    event = migrated.account_migrations[-1]
    assert event["migration_type"] == "code_identity_only"
    assert event["from_code_hash"] == "phase-3-code"
    assert event["to_code_hash"] == "phase-4-code"
    assert event["economic_state_sha256_before"] == before_economic
    assert event["economic_state_sha256_after"] == before_economic


def test_code_identity_migration_requires_explicit_acknowledgement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "account.json"
    save_account(_state(), source)

    with pytest.raises(RuntimeError, match="acknowledge-code-change"):
        migrate_code_identity(
            source,
            tmp_path / "migrated.json",
            new_code_hash="phase-4-code",
            acknowledge_code_change=False,
        )


def test_economic_hash_ignores_only_code_identity_audit_fields() -> None:
    state = _state()
    baseline = economic_state_sha256(state)
    state.code_hash = "another-code"
    state.account_migrations.append({"migration_type": "code_identity_only"})
    assert economic_state_sha256(state) == baseline

    state.cash -= 1.0
    assert economic_state_sha256(state) != baseline
