"""Explicit, audited account schema migration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..types import ACCOUNT_SCHEMA_VERSION, AccountState
from .codec import MIGRATABLE_ACCOUNT_SCHEMAS, account_from_dict, read_account_payload
from .economic_identity import economic_state_sha256
from .transaction import account_transaction

_SCHEMA_9_DEFAULTS: dict[str, object] = {
    "account_revision": 0,
    "broker_binding": "",
    "broker_snapshots": [],
    "external_cash_flows": [],
    "corporate_actions": [],
    "receivables": [],
    "dividend_tax_lots": [],
}


def migrate_account_schema(path: str | Path, *, code_hash: str) -> AccountState:
    """Upgrade one account file in place to the current schema under the account lock."""

    with account_transaction(path) as transaction:
        payload = read_account_payload(path)
        source_schema = payload.get("schema_version")
        if source_schema == ACCOUNT_SCHEMA_VERSION:
            raise RuntimeError("account already uses the current schema")
        if source_schema not in MIGRATABLE_ACCOUNT_SCHEMAS:
            raise RuntimeError(f"no migration from account schema {source_schema!r}")
        payload = {**payload, **{k: v for k, v in _SCHEMA_9_DEFAULTS.items() if k not in payload}}
        payload["schema_version"] = ACCOUNT_SCHEMA_VERSION
        state = account_from_dict(payload)
        state.account_migrations.append(
            {
                "migration_type": "schema_upgrade",
                "migrated_at_utc": datetime.now(UTC).isoformat(),
                "source_schema": source_schema,
                "target_schema": ACCOUNT_SCHEMA_VERSION,
                "source_code_hash": state.code_hash,
                "target_code_hash": code_hash,
                "economic_state_sha256_after": economic_state_sha256(state),
            }
        )
        state.code_hash = code_hash
        transaction.save(state)
        return state
