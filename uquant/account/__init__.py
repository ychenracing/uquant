"""Compatibility surface for strict account persistence."""

# ruff: noqa: F401

from __future__ import annotations

from .codec import account_from_dict, load_account
from .economic_identity import economic_state_sha256
from .migrations import (
    _legacy_attribution_owner,
    _legacy_industry,
    _migrate_v4_attribution_event_ids,
    _populate_legacy_attribution,
    migrate_account,
    migrate_code_identity,
)
from .store import save_account
from .validation_common import (
    _EVENT_ID,
    _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
    _LEGACY_INDUSTRY,
    _LEGACY_MANIFEST_SHA256,
    _ORDER_ID,
    _SHOCK_SEVERITIES,
    _SHOCK_STATES,
    _UNLINKED_LEGACY_IDENTITY_FIELDS,
    _UNLINKED_NATIVE_IDENTITY_FIELDS,
)
from .validation_orders import (
    _derive_v4_attribution_event_id,
    _validate_attribution_identity,
    _validate_fill,
    _validate_order_intent,
    validate_pending_order_for_account_write,
)
from .validation_orders import (
    _validate_lot_origin_chains as _validate_lot_origin_chains,
)
from .validation_orders import (
    _validate_order_state as _validate_order_state,
)
from .validation_positions import (
    _position,
    _tranche,
)
from .validation_positions import (
    _validate_position_state as _validate_position_state,
)
from .validation_strategy import (
    _validate_audit_events,
    _validate_risk_streaks,
)
from .validation_strategy import (
    _validate_strategy_risk_state as _validate_strategy_risk_state,
)

__all__ = (
    "_EVENT_ID",
    "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
    "_LEGACY_INDUSTRY",
    "_LEGACY_MANIFEST_SHA256",
    "_ORDER_ID",
    "_SHOCK_SEVERITIES",
    "_SHOCK_STATES",
    "_UNLINKED_LEGACY_IDENTITY_FIELDS",
    "_UNLINKED_NATIVE_IDENTITY_FIELDS",
    "account_from_dict",
    "economic_state_sha256",
    "load_account",
    "migrate_account",
    "migrate_code_identity",
    "save_account",
    "validate_pending_order_for_account_write",
)

for _exported in (
    account_from_dict,
    economic_state_sha256,
    load_account,
    migrate_account,
    migrate_code_identity,
    save_account,
    validate_pending_order_for_account_write,
):
    _exported.__module__ = __name__

del _exported
