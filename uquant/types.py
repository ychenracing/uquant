"""Domain types and state carried by the only production engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

ACCOUNT_SCHEMA_VERSION = 2


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
    opportunity: str = Opportunity.CHOPPY.value
    risk: str = Risk.NORMAL.value
    shock_state: str = "NONE"
    sector_shock_dates: list[str] = field(default_factory=list)
    sector_guard_active: bool = False
    sector_guard_started: str = ""
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
    tactical_anchor_symbol: str = ""
    protected_weights: dict[str, float] = field(default_factory=dict)
    strategic_cohort_symbols: list[str] = field(default_factory=list)
    strategic_cohort_targets: dict[str, float] = field(default_factory=dict)
    strategic_exit_bands: dict[str, list[float]] = field(default_factory=dict)
    strategic_active_bands: dict[str, list[bool]] = field(default_factory=dict)
    strategic_restore_weights: dict[str, float] = field(default_factory=dict)
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
        return cls(
            initial_cash=cash,
            cash=cash,
            operating_peak=cash,
            capital_peak=cash,
        )

    def to_dict(self) -> dict[str, Any]:
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


@dataclass(frozen=True, slots=True)
class Target:
    """Final desired weight and lifecycle for one symbol."""
    symbol: str
    weight: float
    lifecycle: str
    alpha_score: float
    confidence: float
    reason: str


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
