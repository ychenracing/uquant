"""Explicit, audited account schema migration."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from ..types import ACCOUNT_SCHEMA_VERSION, AccountState
from .codec import MIGRATABLE_ACCOUNT_SCHEMAS, account_from_dict, read_account_payload
from .economic_identity import economic_state_sha256
from .transaction import account_transaction

SCHEMA_9_DEFAULTS: Mapping[str, object] = MappingProxyType({
    "account_revision": 0,
    "broker_binding": "",
    "broker_snapshots": [],
    "external_cash_flows": [],
    "corporate_actions": [],
    "receivables": [],
    "dividend_tax_lots": [],
})


def upgraded_account_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a migratable payload with the additive current-schema fields filled in."""

    source_schema = payload.get("schema_version")
    if source_schema not in MIGRATABLE_ACCOUNT_SCHEMAS:
        raise RuntimeError(f"no migration from account schema {source_schema!r}")
    upgraded = {**payload, **{k: copy.deepcopy(v) for k, v in SCHEMA_9_DEFAULTS.items() if k not in payload}}
    upgraded["schema_version"] = ACCOUNT_SCHEMA_VERSION
    return upgraded


def migrate_account_schema(path: str | Path, *, code_hash: str) -> AccountState:
    """Upgrade one account file in place to the current schema under the account lock."""

    with account_transaction(path) as transaction:
        payload = read_account_payload(path)
        source_schema = payload.get("schema_version")
        if source_schema == ACCOUNT_SCHEMA_VERSION:
            raise RuntimeError("account already uses the current schema")
        state = account_from_dict(upgraded_account_payload(payload))
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
