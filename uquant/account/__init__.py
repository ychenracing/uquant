"""Strict current-account persistence and code-identity operations."""

from __future__ import annotations

from .code_identity import migrate_code_identity
from .codec import UnsupportedAccountSchemaError, account_from_dict, load_account
from .economic_identity import economic_state_sha256
from .store import save_account

__all__ = (
    "UnsupportedAccountSchemaError",
    "account_from_dict",
    "economic_state_sha256",
    "load_account",
    "migrate_code_identity",
    "save_account",
)

for _exported in (
    UnsupportedAccountSchemaError,
    account_from_dict,
    economic_state_sha256,
    load_account,
    migrate_code_identity,
    save_account,
):
    _exported.__module__ = __name__

del _exported
