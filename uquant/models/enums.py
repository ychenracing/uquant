"""Closed enum registries used by domain models."""

from __future__ import annotations

from enum import Enum


class Opportunity(str, Enum):
    """Market opportunity regimes used by the portfolio allocator."""

    STRONG_TREND = "STRONG_TREND"
    TREND = "TREND"
    RECOVERY = "RECOVERY"
    CHOPPY = "CHOPPY"
    WEAK = "WEAK"


class Risk(str, Enum):
    """Persistent portfolio risk states ordered by severity."""

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK_OFF = "RISK_OFF"
    CRISIS = "CRISIS"


class Lifecycle(str, Enum):
    """Position roles used for sizing, additions, and attribution."""

    CORE = "CORE"
    ADD1 = "ADD1"
    ADD2 = "ADD2"
    SATELLITE = "SATELLITE"
    RECOVERY = "RECOVERY"


class OriginSubsystem(str, Enum):
    """Closed causal owners for attribution-bearing economic events."""

    LEADER = "LEADER"
    RECOVERY = "RECOVERY"
    STRATEGIC = "STRATEGIC"
    RISK = "RISK"
    BROKER_RECONCILIATION = "BROKER_RECONCILIATION"
    LEGACY_MIGRATION = "LEGACY_MIGRATION"
    UNATTRIBUTED_LEGACY = "UNATTRIBUTED_LEGACY"


class AttributionMechanism(str, Enum):
    """Closed registry of causal mechanisms that may create economic events."""

    LEADER_SELECTION = "LEADER_SELECTION"
    LEADER_ROTATION = "LEADER_ROTATION"
    LEADER_LIFECYCLE_EXIT = "LEADER_LIFECYCLE_EXIT"
    LEADER_LIFECYCLE_PROMOTION = "LEADER_LIFECYCLE_PROMOTION"
    LEADER_PYRAMID = "LEADER_PYRAMID"
    CHALLENGER_SCOUT = "CHALLENGER_SCOUT"
    SATELLITE_EXPIRY = "SATELLITE_EXPIRY"
    RECOVERY_COHORT = "RECOVERY_COHORT"
    RECOVERY_SUBSTITUTION = "RECOVERY_SUBSTITUTION"
    RECOVERY_CAP = "RECOVERY_CAP"
    RECOVERY_REARM = "RECOVERY_REARM"
    TACTICAL_REBOUND = "TACTICAL_REBOUND"
    POST_SHOCK_RESTORATION = "POST_SHOCK_RESTORATION"
    STRATEGIC_COHORT = "STRATEGIC_COHORT"
    STRATEGIC_TRAILING_EXIT = "STRATEGIC_TRAILING_EXIT"
    STRATEGIC_PROFIT_LOCK = "STRATEGIC_PROFIT_LOCK"
    STRATEGIC_RESTORATION = "STRATEGIC_RESTORATION"
    RISK_GROSS_CAP = "RISK_GROSS_CAP"
    SECTOR_GUARD = "SECTOR_GUARD"
    STRATEGIC_DAMAGE_GUARD = "STRATEGIC_DAMAGE_GUARD"
    RISK_OFF = "RISK_OFF"
    CRISIS = "CRISIS"
    CAPITAL_BUDGET = "CAPITAL_BUDGET"
    RISK_FREEZE = "RISK_FREEZE"
    BROKER_RECONCILIATION = "BROKER_RECONCILIATION"
    LEGACY_MIGRATION = "LEGACY_MIGRATION"
    LEGACY_UNCLASSIFIED = "LEGACY_UNCLASSIFIED"


class Side(str, Enum):
    """Supported cash-equity order directions."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Durable broker-order lifecycle states."""

    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REPLACED = "REPLACED"


class ReductionPolicy(str, Enum):
    """Economic lot selection used by a sell order.

    Ordinary strategy exits retain FIFO semantics.  Portfolio and structural
    risk reductions instead retire the most fragile incremental tranches
    before touching a healthy core.
    """

    FIFO = "FIFO"
    RISK_PRIORITY = "RISK_PRIORITY"


__all__ = (
    "AttributionMechanism",
    "Lifecycle",
    "Opportunity",
    "OrderStatus",
    "OriginSubsystem",
    "ReductionPolicy",
    "Risk",
    "Side",
)
