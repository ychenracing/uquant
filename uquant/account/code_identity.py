"""Explicit code-identity rebinding for a current account."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..types import AccountState
from .codec import load_account
from .economic_identity import economic_state_sha256
from .store import save_account


def migrate_code_identity(
    source: str | Path,
    destination: str | Path,
    *,
    new_code_hash: str,
    acknowledge_code_change: bool,
) -> AccountState:
    """Rebind a current account to reviewed code without economic mutation."""

    if not acknowledge_code_change:
        raise RuntimeError("account-code-migrate requires --acknowledge-code-change")
    if not isinstance(new_code_hash, str) or not new_code_hash.strip():
        raise RuntimeError("account-code-migrate requires a non-empty code hash")
    state = load_account(source)
    if state.code_hash == new_code_hash:
        raise RuntimeError("account already uses the requested code hash")
    before = economic_state_sha256(state)
    previous_code_hash = state.code_hash
    state.code_hash = new_code_hash
    after = economic_state_sha256(state)
    if after != before:
        raise RuntimeError("account-code-migrate changed economic state")
    state.account_migrations.append(
        {
            "migration_type": "code_identity_only",
            "migrated_at_utc": datetime.now(UTC).isoformat(),
            "from_schema": state.schema_version,
            "to_schema": state.schema_version,
            "from_code_hash": previous_code_hash,
            "to_code_hash": new_code_hash,
            "economic_state_sha256_before": before,
            "economic_state_sha256_after": after,
        }
    )
    save_account(state, destination)
    persisted = load_account(destination)
    if economic_state_sha256(persisted) != before:
        raise RuntimeError("persisted account-code-migrate changed economic state")
    return persisted
