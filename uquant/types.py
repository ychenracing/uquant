"""Domain types and state carried by the only production engine."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date as date_type
from enum import Enum
from typing import Any, TypedDict

ACCOUNT_SCHEMA_VERSION = 5


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


_ATTRIBUTION_COMPATIBILITY: dict[
    tuple[OriginSubsystem, AttributionMechanism],
    frozenset[Side],
] = {
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
        raise ValueError(
            f"attribution pair {origin.value}/{causal_mechanism.value} is not registered"
        )
    if side is None:
        return
    direction = Side(side)
    if direction not in allowed_sides:
        raise ValueError(
            f"attribution pair {origin.value}/{causal_mechanism.value} "
            f"is not permitted for {direction.value}"
        )


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


class AttributionIdentity(TypedDict):
    """Canonical metadata copied without reinterpretation between domain objects."""

    event_id: str
    origin_subsystem: str
    mechanism: str
    origin_lifecycle: str
    replaces_symbol: str | None
    industry_at_entry: str
    industry_manifest_sha256: str


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
    if replaces_symbol is not None and (
        not isinstance(replaces_symbol, str) or not replaces_symbol
    ):
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


ATTRIBUTION_IDENTITY_FIELDS: tuple[str, ...] = (
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


@dataclass(slots=True)
class AccountState:
    """Complete durable state required to continue the next daily decision."""

    initial_cash: float
    cash: float
    schema_version: int = ACCOUNT_SCHEMA_VERSION
    positions: dict[str, Position] = field(default_factory=dict)
    pending_orders: list[PendingOrder] = field(default_factory=list)
    order_ledger: list[AccountOrder] = field(default_factory=list)
    next_order_sequence: int = 1
    fills: list[Fill] = field(default_factory=list)
    broker_as_of: str = ""
    opportunity: str = Opportunity.CHOPPY.value
    risk: str = Risk.NORMAL.value
    shock_state: str = "NONE"
    sector_shock_dates: list[str] = field(default_factory=list)
    sector_guard_active: bool = False
    sector_guard_started: str = ""
    sector_guard_symbols: list[str] = field(default_factory=list)
    sector_recovery_streak: int = 0
    cooldown_until: str = ""
    operating_peak: float = 0.0
    capital_peak: float = 0.0
    leader_tenure: dict[str, int] = field(default_factory=dict)
    candidate_tenure: dict[str, int] = field(default_factory=dict)
    replacement_tenure: dict[str, int] = field(default_factory=dict)
    active_leaders: list[str] = field(default_factory=list)
    dynamic_k: int = 0
    last_k_change_date: str = ""
    satellite_entry_dates: dict[str, str] = field(default_factory=dict)
    risk_streaks: dict[str, int] = field(default_factory=dict)
    rotation_dates: list[str] = field(default_factory=list)
    replacement_events: list[dict[str, Any]] = field(default_factory=list)
    lifecycle_events: list[dict[str, Any]] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    account_migrations: list[dict[str, Any]] = field(default_factory=list)
    anchor_weights: dict[str, float] = field(default_factory=dict)
    recovery_anchor_date: str = ""
    recovery_conviction_symbol: str = ""
    tactical_anchor_symbol: str = ""
    protected_weights: dict[str, float] = field(default_factory=dict)
    strategic_cohort_symbols: list[str] = field(default_factory=list)
    strategic_cohort_targets: dict[str, float] = field(default_factory=dict)
    strategic_exit_bands: dict[str, list[float]] = field(default_factory=dict)
    strategic_active_bands: dict[str, list[bool]] = field(default_factory=dict)
    strategic_restore_weights: dict[str, float] = field(default_factory=dict)
    strategic_epoch: int = 0
    strategic_epochs_completed: int = 0
    strategic_last_exit_date: str = ""
    strategic_rearm_date: str = ""
    strategic_candidate_signature: str = ""
    strategic_previous_symbols: list[str] = field(default_factory=list)
    risk_anchor_symbols: list[str] = field(default_factory=list)
    risk_anchor_signature: str = ""
    risk_anchor_candidate_signature: str = ""
    risk_anchor_candidate_streak: int = 0
    risk_signal_state: dict[str, float] = field(default_factory=dict)
    capital_budget_level: int = 0
    capital_budget_repair_streak: int = 0
    chronic_level: int = 0
    chronic_streak: int = 0
    chronic_repair_streak: int = 0
    scout_signature: str = ""
    scout_entry_date: str = ""
    reconciliation_events: list[dict[str, Any]] = field(default_factory=list)
    shock_start_date: str = ""
    shock_severity: str = "NORMAL"
    last_shock_date: str = ""
    last_successful_run: str = ""
    data_hash: str = ""
    data_hash_as_of: str = ""
    data_hash_symbols: list[str] = field(default_factory=list)
    code_hash: str = ""

    @classmethod
    def empty(cls, cash: float) -> AccountState:
        """Create an uninvested account with initialized equity high-water marks."""

        return cls(
            initial_cash=cash,
            cash=cash,
            operating_peak=cash,
            capital_peak=cash,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete durable account payload."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class LeaderScore:
    """Point-in-time leadership strength, confidence, and classification."""

    symbol: str
    score: float
    confidence: float
    mature: bool
    emerging: bool
    industry: str
    components: dict[str, float]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Risk state, exposure cap, and auditable evidence for one session."""

    state: Risk
    target_gross_cap: float
    votes: int
    evidence: dict[str, Any]
    reasons: tuple[str, ...]
    shock_state: str
    freeze_new_risk: bool = False
    reduction_level: int = 0
    severity: str = "NORMAL"


@dataclass(frozen=True, slots=True)
class Target:
    """Final desired weight and lifecycle for one symbol."""

    symbol: str
    weight: float
    lifecycle: str
    alpha_score: float
    confidence: float
    reason: str
    reduction_policy: str = ReductionPolicy.FIFO.value
    reason_code: str = "strategy_target"
    exit_kind: str = "strategy"
    entry_industry_strength: float = 0.0
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    """Immutable daily output containing targets, intents, and risk evidence."""

    date: str
    opportunity: Opportunity
    risk: Risk
    target_gross: float
    target_k: int
    targets: tuple[Target, ...]
    pending_orders: tuple[PendingOrder, ...]
    risk_summary: dict[str, Any]
    decision_digest: str

    def canonical_payload(self, *, effective_config_sha256: str) -> dict[str, Any]:
        """Return the complete deterministic decision contract for evidence."""

        pending_by_event = {
            item.event_id: item for item in self.pending_orders if item.event_id
        }
        targets: list[dict[str, Any]] = []
        for item in self.targets:
            retained = pending_by_event.get(item.event_id)
            event_signal_date = self.date if retained is None else retained.signal_date
            event_target_weight = item.weight if retained is None else retained.target_weight
            targets.append(
                {
                    "symbol": item.symbol,
                    "weight": round(item.weight, 12),
                    "lifecycle": item.lifecycle,
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "event_signal_date": event_signal_date,
                    "event_target_weight_hex": float(event_target_weight).hex(),
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                }
            )
        return {
            "schema": "uquant.decision-control-plane.v2",
            "date": self.date,
            "opportunity": self.opportunity.value,
            "risk": {
                # Descriptive shock/severity diagnostics stay in risk_summary, but
                # are not control evidence: no independent daily carrier can replay
                # them.  The fields below are cross-bound to the frozen digest,
                # daily ledger, compiled config, or exact targets.
                "state": self.risk.value,
                "target_gross_cap": round(
                    float(self.risk_summary.get("target_gross_cap", 0.0)),
                    12,
                ),
                "system_gross_cap": round(
                    float(self.risk_summary.get("system_gross_cap", 0.0)),
                    12,
                ),
            },
            "target_gross": round(self.target_gross, 12),
            "targets": targets,
            "orders": [
                {
                    "order_id": item.order_id,
                    "signal_date": item.signal_date,
                    "snapshot_kind": (
                        "ORIGIN" if item.signal_date == self.date else "CARRIED_FORWARD"
                    ),
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                }
                for item in self.pending_orders
            ],
            "effective_config_sha256": effective_config_sha256,
        }

    def legacy_canonical_payload(self) -> dict[str, Any]:
        """Reconstruct the exact frozen schema-v3 decision digest payload."""

        return {
            "date": self.date,
            "opportunity": self.opportunity.value,
            "risk": self.risk.value,
            "targets": [
                {
                    "symbol": item.symbol,
                    "weight": round(item.weight, 12),
                    "lifecycle": item.lifecycle,
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                }
                for item in self.targets
            ],
            "orders": [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                }
                for item in self.pending_orders
            ],
        }
