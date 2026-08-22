"""Canonical economic projection and identity for AccountState."""

from __future__ import annotations

import hashlib
import json

from ..types import AccountState


def economic_state_sha256(state: AccountState) -> str:
    """Hash every durable economic field while excluding code-only audit data."""

    if not isinstance(state, AccountState):
        raise ValueError("economic state hash requires AccountState")
    payload = state.to_dict()
    payload.pop("code_hash", None)
    payload.pop("account_migrations", None)
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
