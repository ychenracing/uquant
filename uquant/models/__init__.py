"""Canonical domain-model package with stable compatibility identities."""

from .account import ACCOUNT_SCHEMA_VERSION, AccountState
from .decision import Decision, LeaderScore, RiskAssessment, Target
from .enums import (
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    ReductionPolicy,
    Risk,
    Side,
)
from .trading import (
    ATTRIBUTION_IDENTITY_FIELDS,
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AttributionIdentity,
    Fill,
    PendingOrder,
    Position,
    Tranche,
    derive_attribution_event_id,
    order_intent_metadata,
    validate_attribution_compatibility,
)

AccountOrder.__module__ = "uquant.types"
AccountState.__module__ = "uquant.types"
AttributionIdentity.__module__ = "uquant.types"
AttributionMechanism.__module__ = "uquant.types"
Decision.__module__ = "uquant.types"
Fill.__module__ = "uquant.types"
LeaderScore.__module__ = "uquant.types"
Lifecycle.__module__ = "uquant.types"
Opportunity.__module__ = "uquant.types"
OrderStatus.__module__ = "uquant.types"
OriginSubsystem.__module__ = "uquant.types"
PendingOrder.__module__ = "uquant.types"
Position.__module__ = "uquant.types"
ReductionPolicy.__module__ = "uquant.types"
Risk.__module__ = "uquant.types"
RiskAssessment.__module__ = "uquant.types"
Side.__module__ = "uquant.types"
Target.__module__ = "uquant.types"
Tranche.__module__ = "uquant.types"
derive_attribution_event_id.__module__ = "uquant.types"
order_intent_metadata.__module__ = "uquant.types"
validate_attribution_compatibility.__module__ = "uquant.types"

__all__ = (
    "ACCOUNT_SCHEMA_VERSION",
    "ATTRIBUTION_IDENTITY_FIELDS",
    "ORDER_INTENT_IMMUTABLE_FIELDS",
    "AccountOrder",
    "AccountState",
    "AttributionIdentity",
    "AttributionMechanism",
    "Decision",
    "Fill",
    "LeaderScore",
    "Lifecycle",
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
    "derive_attribution_event_id",
    "order_intent_metadata",
    "validate_attribution_compatibility",
)
