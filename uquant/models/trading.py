"""Trading, attribution, order, tranche, and fill models."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as date_type
from types import MappingProxyType
from typing import Any, TypedDict

from .enums import (
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    ReductionPolicy,
    Side,
)

_ATTRIBUTION_COMPATIBILITY: Mapping[
    tuple[OriginSubsystem, AttributionMechanism],
    frozenset[Side],
] = MappingProxyType(
    {
        (OriginSubsystem.LEADER, AttributionMechanism.LEADER_SELECTION): frozenset(Side),
        (OriginSubsystem.LEADER, AttributionMechanism.LEADER_ROTATION): frozenset(Side),
        (OriginSubsystem.LEADER, AttributionMechanism.LEADER_LIFECYCLE_EXIT): frozenset({Side.SELL}),
        (OriginSubsystem.LEADER, AttributionMechanism.LEADER_LIFECYCLE_PROMOTION): frozenset(),
        (OriginSubsystem.LEADER, AttributionMechanism.LEADER_PYRAMID): frozenset({Side.BUY}),
        (OriginSubsystem.LEADER, AttributionMechanism.CHALLENGER_SCOUT): frozenset({Side.BUY}),
        (OriginSubsystem.LEADER, AttributionMechanism.SATELLITE_EXPIRY): frozenset({Side.SELL}),
        (OriginSubsystem.RECOVERY, AttributionMechanism.RECOVERY_COHORT): frozenset(Side),
        (OriginSubsystem.RECOVERY, AttributionMechanism.RECOVERY_SUBSTITUTION): frozenset(Side),
        (OriginSubsystem.RECOVERY, AttributionMechanism.RECOVERY_CAP): frozenset(Side),
        (OriginSubsystem.RECOVERY, AttributionMechanism.RECOVERY_REARM): frozenset({Side.BUY}),
        (OriginSubsystem.RECOVERY, AttributionMechanism.TACTICAL_REBOUND): frozenset(Side),
        (OriginSubsystem.RECOVERY, AttributionMechanism.POST_SHOCK_RESTORATION): frozenset({Side.BUY}),
        (OriginSubsystem.STRATEGIC, AttributionMechanism.STRATEGIC_COHORT): frozenset(Side),
        (OriginSubsystem.STRATEGIC, AttributionMechanism.STRATEGIC_TRAILING_EXIT): frozenset({Side.SELL}),
        (OriginSubsystem.STRATEGIC, AttributionMechanism.STRATEGIC_PROFIT_LOCK): frozenset({Side.SELL}),
        (OriginSubsystem.STRATEGIC, AttributionMechanism.STRATEGIC_RESTORATION): frozenset({Side.BUY}),
        (OriginSubsystem.RISK, AttributionMechanism.RISK_GROSS_CAP): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.SECTOR_GUARD): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.STRATEGIC_DAMAGE_GUARD): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.RISK_OFF): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.CRISIS): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.CAPITAL_BUDGET): frozenset({Side.SELL}),
        (OriginSubsystem.RISK, AttributionMechanism.RISK_FREEZE): frozenset(),
        (
            OriginSubsystem.BROKER_RECONCILIATION,
            AttributionMechanism.BROKER_RECONCILIATION,
        ): frozenset({Side.SELL}),
        (OriginSubsystem.LEGACY_MIGRATION, AttributionMechanism.LEGACY_MIGRATION): frozenset({Side.SELL}),
        (
            OriginSubsystem.UNATTRIBUTED_LEGACY,
            AttributionMechanism.LEGACY_UNCLASSIFIED,
        ): frozenset({Side.BUY}),
    }
)
ATTRIBUTION_COMPATIBILITY = _ATTRIBUTION_COMPATIBILITY


def validate_attribution_compatibility(
    *,
    origin_subsystem: str,
    mechanism: str,
    side: str | None = None,
) -> None:
    """Validate the one authoritative origin/mechanism/side registry."""

    origin = OriginSubsystem(origin_subsystem)
    causal_mechanism = AttributionMechanism(mechanism)
    allowed_sides = _ATTRIBUTION_COMPATIBILITY.get((origin, causal_mechanism))
    if allowed_sides is None:
        raise ValueError(f"attribution pair {origin.value}/{causal_mechanism.value} is not registered")
    if side is None:
        return
    direction = Side(side)
    if direction not in allowed_sides:
        raise ValueError(
            f"attribution pair {origin.value}/{causal_mechanism.value} is not permitted for {direction.value}"
        )


class AttributionIdentity(TypedDict):
    """Canonical metadata copied without reinterpretation between domain objects."""

    event_id: str
    origin_subsystem: str
    mechanism: str
    origin_lifecycle: str
    replaces_symbol: str | None
    industry_at_entry: str
    industry_manifest_sha256: str
    grant_id: str


def derive_attribution_event_id(
    *,
    signal_date: str,
    symbol: str,
    target_weight: float,
    lifecycle: str,
    origin_lifecycle: str,
    origin_subsystem: str,
    mechanism: str,
    replaces_symbol: str | None,
    industry_at_entry: str,
    industry_manifest_sha256: str,
    reduction_policy: str,
    reason_code: str,
    exit_kind: str,
) -> str:
    """Derive one replay-stable attribution identity from canonical intent.

    The v2 payload deliberately excludes prose, wall-clock state, process
    hashes, UUIDs, broker fill timing, and mutable account order sequencing.
    IEEE-754 hexadecimal weight encoding avoids locale or decimal-rendering
    ambiguity while symbol, weight, mechanism, and replacement dimensions
    prevent same-session economic intents from sharing an identity.
    """

    try:
        date_type.fromisoformat(signal_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("attribution signal_date must be an ISO date") from exc
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("attribution symbol must be non-empty text")
    if not isinstance(target_weight, (int, float)) or isinstance(target_weight, bool):
        raise ValueError("attribution target_weight must be finite")
    weight = float(target_weight)
    if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
        raise ValueError("attribution target_weight must be between zero and one")
    Lifecycle(lifecycle)
    Lifecycle(origin_lifecycle)
    OriginSubsystem(origin_subsystem)
    AttributionMechanism(mechanism)
    validate_attribution_compatibility(
        origin_subsystem=origin_subsystem,
        mechanism=mechanism,
    )
    ReductionPolicy(reduction_policy)
    if replaces_symbol is not None and (not isinstance(replaces_symbol, str) or not replaces_symbol):
        raise ValueError("attribution replacement symbol must be non-empty text")
    if not isinstance(industry_at_entry, str) or not industry_at_entry:
        raise ValueError("attribution industry_at_entry must be non-empty text")
    if (
        not isinstance(industry_manifest_sha256, str)
        or len(industry_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in industry_manifest_sha256)
    ):
        raise ValueError("attribution industry manifest must be SHA-256")
    # Kept as compatibility/display arguments for persisted domain objects.
    # Neither display/backward field participates in attribution identity.
    del reason_code, exit_kind
    payload = {
        "schema": "uquant.attribution-event.v2",
        "signal_date": signal_date,
        "symbol": symbol,
        "target_weight": weight.hex(),
        "lifecycle": lifecycle,
        "origin_lifecycle": origin_lifecycle,
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "replaces_symbol": replaces_symbol,
        "industry_at_entry": industry_at_entry,
        "industry_manifest_sha256": industry_manifest_sha256,
        "reduction_policy": reduction_policy,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "evt_" + hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class Tranche:
    """A T+1-aware lot belonging to one position lifecycle."""

    tranche_id: str
    lifecycle: str
    shares: int
    avg_cost: float
    entry_date: str
    sellable_date: str
    highest_close: float
    lowest_close: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    entry_score: float = 0.0
    entry_confidence: float = 0.0
    entry_regime: str = Opportunity.CHOPPY.value
    entry_industry_strength: float = 0.0
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""
    grant_id: str = ""


@dataclass(slots=True)
class Position:
    """Aggregated holding plus its independently sellable tranches."""

    symbol: str
    shares: int = 0
    avg_cost: float = 0.0
    entry_date: str = ""
    highest_close: float = 0.0
    lifecycle: str = Lifecycle.CORE.value
    tranches: list[Tranche] = field(default_factory=list)
    grant_id: str = ""

    def sellable_shares(self, date: str) -> int:
        """Return tranche shares whose T+1 sellable date has arrived."""

        return sum(item.shares for item in self.tranches if item.sellable_date <= date)


@dataclass(slots=True)
class PendingOrder:
    """Next-open execution intent derived from a final target weight."""

    signal_date: str
    symbol: str
    side: str
    target_weight: float
    reason: str
    lifecycle: str
    remaining_shares: int = 0
    attempts: int = 0
    order_id: str = ""
    reduction_policy: str = ReductionPolicy.FIFO.value
    reason_code: str = "strategy_target"
    exit_kind: str = "strategy"
    entry_score: float = 0.0
    entry_confidence: float = 0.0
    entry_regime: str = Opportunity.CHOPPY.value
    entry_industry_strength: float = 0.0
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""
    grant_id: str = ""


@dataclass(slots=True)
class AccountOrder:
    """One broker-visible order throughout its complete lifecycle."""

    order_id: str
    signal_date: str
    submitted_date: str
    symbol: str
    side: str
    target_weight: float
    reason: str
    lifecycle: str
    status: str = OrderStatus.SUBMITTED.value
    requested_shares: int = 0
    filled_shares: int = 0
    remaining_shares: int = 0
    attempts: int = 0
    last_update_date: str = ""
    last_event: str = "SUBMITTED"
    replaced_by: str = ""
    cancel_reason: str = ""
    reduction_policy: str = ReductionPolicy.FIFO.value
    reason_code: str = "strategy_target"
    exit_kind: str = "strategy"
    entry_score: float = 0.0
    entry_confidence: float = 0.0
    entry_regime: str = Opportunity.CHOPPY.value
    entry_industry_strength: float = 0.0
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""
    grant_id: str = ""


ATTRIBUTION_IDENTITY_FIELDS: tuple[str, ...] = (
    "grant_id",
    "event_id",
    "origin_subsystem",
    "mechanism",
    "origin_lifecycle",
    "replaces_symbol",
    "industry_at_entry",
    "industry_manifest_sha256",
)


ORDER_INTENT_IMMUTABLE_FIELDS: tuple[str, ...] = (
    "signal_date",
    "symbol",
    "side",
    "target_weight",
    "reason",
    "lifecycle",
    "reduction_policy",
    "reason_code",
    "exit_kind",
    "entry_score",
    "entry_confidence",
    "entry_regime",
    "entry_industry_strength",
    *ATTRIBUTION_IDENTITY_FIELDS,
)


def order_intent_metadata(order: PendingOrder | AccountOrder) -> tuple[Any, ...]:
    """Return the immutable economic identity shared by pending and ledger orders."""
    return tuple(getattr(order, name) for name in ORDER_INTENT_IMMUTABLE_FIELDS)


@dataclass(slots=True)
class Fill:
    """One simulated or broker-reported execution with explicit costs."""

    signal_date: str
    fill_date: str
    symbol: str
    side: str
    shares: int
    price: float
    gross_value: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    reason: str
    lifecycle: str
    order_id: str = ""
    fill_id: str = ""
    reduction_policy: str = ReductionPolicy.FIFO.value
    reason_code: str = "strategy_target"
    exit_kind: str = "strategy"
    sold_tranches: list[dict[str, Any]] = field(default_factory=list)
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""
    grant_id: str = ""


__all__ = (
    "ATTRIBUTION_COMPATIBILITY",
    "ATTRIBUTION_IDENTITY_FIELDS",
    "ORDER_INTENT_IMMUTABLE_FIELDS",
    "_ATTRIBUTION_COMPATIBILITY",
    "AccountOrder",
    "AttributionIdentity",
    "Fill",
    "PendingOrder",
    "Position",
    "Tranche",
    "derive_attribution_event_id",
    "order_intent_metadata",
    "validate_attribution_compatibility",
)
