"""Canonical domain-model package with stable compatibility identities."""

from typing import Any, cast

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
from .strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    activate_strategic_epoch,
    close_strategic_epoch,
    derive_strategic_epoch_id,
)
from .strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    StrategicQualificationObservation,
    derive_strategic_grant_id,
)
from .strategic_rearm import (
    StrategicCashRearmPredicate,
    StrategicCashRearmRejectionReason,
    StrategicCashRearmState,
    StrategicCashRearmStatus,
    StrategicCashRearmStreakTransition,
    derive_strategic_cash_rearm_authorization_id,
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
cast(Any, AccountState.empty).__func__.__module__ = "uquant.types"
AccountState.to_dict.__module__ = "uquant.types"
AttributionIdentity.__module__ = "uquant.types"
AttributionMechanism.__module__ = "uquant.types"
Decision.__module__ = "uquant.types"
Decision.canonical_payload.__module__ = "uquant.types"
Decision.legacy_canonical_payload.__module__ = "uquant.types"
Fill.__module__ = "uquant.types"
LeaderScore.__module__ = "uquant.types"
Lifecycle.__module__ = "uquant.types"
Opportunity.__module__ = "uquant.types"
OrderStatus.__module__ = "uquant.types"
OriginSubsystem.__module__ = "uquant.types"
PendingOrder.__module__ = "uquant.types"
Position.__module__ = "uquant.types"
Position.sellable_shares.__module__ = "uquant.types"
ReductionPolicy.__module__ = "uquant.types"
Risk.__module__ = "uquant.types"
RiskAssessment.__module__ = "uquant.types"
Side.__module__ = "uquant.types"
StrategicGrantIntent.__module__ = "uquant.types"
StrategicGrantStatus.__module__ = "uquant.types"
StrategicQualificationObservation.__module__ = "uquant.types"
StrategicEpoch.__module__ = "uquant.types"
StrategicEpochStatus.__module__ = "uquant.types"
StrategicCashRearmPredicate.__module__ = "uquant.types"
StrategicCashRearmRejectionReason.__module__ = "uquant.types"
StrategicCashRearmState.__module__ = "uquant.types"
StrategicCashRearmStatus.__module__ = "uquant.types"
StrategicCashRearmStreakTransition.__module__ = "uquant.types"
Target.__module__ = "uquant.types"
Tranche.__module__ = "uquant.types"
derive_attribution_event_id.__module__ = "uquant.types"
derive_strategic_grant_id.__module__ = "uquant.types"
derive_strategic_cash_rearm_authorization_id.__module__ = "uquant.types"
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
    "StrategicGrantIntent",
    "StrategicGrantStatus",
    "StrategicQualificationObservation",
    "StrategicEpoch",
    "StrategicEpochStatus",
    "StrategicCashRearmPredicate",
    "StrategicCashRearmRejectionReason",
    "StrategicCashRearmState",
    "StrategicCashRearmStatus",
    "StrategicCashRearmStreakTransition",
    "Target",
    "Tranche",
    "derive_attribution_event_id",
    "derive_strategic_grant_id",
    "derive_strategic_cash_rearm_authorization_id",
    "derive_strategic_epoch_id",
    "activate_strategic_epoch",
    "close_strategic_epoch",
    "order_intent_metadata",
    "validate_attribution_compatibility",
)
