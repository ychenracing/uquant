"""Compatibility re-export for the canonical domain-model package."""

# Relocated classes retain ``uquant.types`` identity, so runtime hint resolution
# needs the historical annotation name in this facade namespace.
from typing import Any as Any

from .models import (
    ACCOUNT_SCHEMA_VERSION,
    ATTRIBUTION_IDENTITY_FIELDS,
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    AttributionIdentity,
    AttributionMechanism,
    Decision,
    Fill,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Side,
    Target,
    Tranche,
    derive_attribution_event_id,
    order_intent_metadata,
    validate_attribution_compatibility,
)
from .models import trading as _trading

_ATTRIBUTION_COMPATIBILITY = _trading.ATTRIBUTION_COMPATIBILITY
del _trading

__all__ = (  # noqa: RUF022 - preserve the frozen compatibility export order
    "ACCOUNT_SCHEMA_VERSION",
    "ATTRIBUTION_IDENTITY_FIELDS",
    "AccountOrder",
    "AccountState",
    "AttributionIdentity",
    "AttributionMechanism",
    "Decision",
    "Fill",
    "LeaderScore",
    "Lifecycle",
    "ORDER_INTENT_IMMUTABLE_FIELDS",
    "Opportunity",
    "OrderStatus",
    "OriginSubsystem",
    "PendingOrder",
    "Position",
    "ReductionPolicy",
    "Risk",
    "RiskAssessment",
    "Side",
    "Target",
    "Tranche",
    "_ATTRIBUTION_COMPATIBILITY",
    "derive_attribution_event_id",
    "order_intent_metadata",
    "validate_attribution_compatibility",
)
