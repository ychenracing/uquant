"""Compatibility surface for strict account persistence."""

# ruff: noqa: F401

from __future__ import annotations

from .codec import account_from_dict, load_account
from .economic_identity import economic_state_sha256
from .migrations import (
    legacy_attribution_owner as _legacy_attribution_owner,
)
from .migrations import (
    legacy_industry as _legacy_industry,
)
from .migrations import (
    migrate_account,
    migrate_code_identity,
)
from .migrations import (
    migrate_v4_attribution_event_ids as _migrate_v4_attribution_event_ids,
)
from .migrations import (
    populate_legacy_attribution as _populate_legacy_attribution,
)
from .store import save_account
from .validation_attribution import (
    derive_v4_attribution_event_id as _derive_v4_attribution_event_id,
)
from .validation_attribution import (
    validate_attribution_identity as _validate_attribution_identity,
)
from .validation_attribution import (
    validate_lot_origin_chains as _validate_lot_origin_chains,
)
from .validation_attribution import (
    validate_order_intent as _validate_order_intent,
)
from .validation_common import (
    EVENT_ID_PATTERN as _EVENT_ID,
)
from .validation_common import (
    HISTORICAL_ATTRIBUTION_SCHEMA_VERSION as _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
)
from .validation_common import (
    LEGACY_INDUSTRY as _LEGACY_INDUSTRY,
)
from .validation_common import (
    LEGACY_MANIFEST_SHA256 as _LEGACY_MANIFEST_SHA256,
)
from .validation_common import (
    ORDER_ID_PATTERN as _ORDER_ID,
)
from .validation_common import (
    SHOCK_SEVERITIES as _SHOCK_SEVERITIES,
)
from .validation_common import (
    SHOCK_STATES as _SHOCK_STATES,
)
from .validation_common import (
    UNLINKED_LEGACY_IDENTITY_FIELDS as _UNLINKED_LEGACY_IDENTITY_FIELDS,
)
from .validation_common import (
    UNLINKED_NATIVE_IDENTITY_FIELDS as _UNLINKED_NATIVE_IDENTITY_FIELDS,
)
from .validation_orders import (
    validate_fill as _validate_fill,
)
from .validation_orders import (
    validate_order_state as _validate_order_state,
)
from .validation_orders import (
    validate_pending_order_for_account_write,
)
from .validation_positions import (
    position_from_payload as _position,
)
from .validation_positions import (
    tranche_from_payload as _tranche,
)
from .validation_positions import (
    validate_position_state as _validate_position_state,
)
from .validation_strategy import (
    validate_audit_events as _validate_audit_events,
)
from .validation_strategy import (
    validate_risk_streaks as _validate_risk_streaks,
)
from .validation_strategy import (
    validate_strategy_risk_state as _validate_strategy_risk_state,
)

validate_lot_origin_chains = _validate_lot_origin_chains
validate_order_state = _validate_order_state
validate_position_state = _validate_position_state
validate_strategy_risk_state = _validate_strategy_risk_state

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
