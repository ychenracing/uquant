"""Domain types and state carried by the only production engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Opportunity(str, Enum):
    STRONG_TREND = "STRONG_TREND"
    TREND = "TREND"
    RECOVERY = "RECOVERY"
    CHOPPY = "CHOPPY"
    WEAK = "WEAK"


class Risk(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RISK_OFF = "RISK_OFF"
    CRISIS = "CRISIS"


class Lifecycle(str, Enum):
    CORE = "CORE"
    ADD1 = "ADD1"
    ADD2 = "ADD2"
    SATELLITE = "SATELLITE"
    RECOVERY = "RECOVERY"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(slots=True)
class Tranche:
    tranche_id: str
    lifecycle: str
    shares: int
    avg_cost: float
    entry_date: str
    sellable_date: str
    highest_close: float


@dataclass(slots=True)
class Position:
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
    signal_date: str
    symbol: str
    side: str
    target_weight: float
    reason: str
    lifecycle: str
    remaining_shares: int = 0
    attempts: int = 0


@dataclass(slots=True)
class Fill:
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


@dataclass(slots=True)
class AccountState:
    initial_cash: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    pending_orders: list[PendingOrder] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    opportunity: str = Opportunity.CHOPPY.value
    risk: str = Risk.NORMAL.value
    shock_state: str = "NONE"
    cooldown_until: str = ""
    operating_peak: float = 0.0
    capital_peak: float = 0.0
    leader_tenure: dict[str, int] = field(default_factory=dict)
    candidate_tenure: dict[str, int] = field(default_factory=dict)
    replacement_tenure: dict[str, int] = field(default_factory=dict)
    risk_streaks: dict[str, int] = field(default_factory=dict)
    rotation_dates: list[str] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    anchor_weights: dict[str, float] = field(default_factory=dict)
    recovery_anchor_date: str = ""
    protected_weights: dict[str, float] = field(default_factory=dict)
    shock_start_date: str = ""
    shock_severity: str = "NORMAL"
    last_shock_date: str = ""
    last_successful_run: str = ""
    data_hash: str = ""
    code_hash: str = ""

    @classmethod
    def empty(cls, cash: float) -> "AccountState":
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
    symbol: str
    score: float
    confidence: float
    mature: bool
    emerging: bool
    industry: str
    components: dict[str, float]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    state: Risk
    target_gross_cap: float
    votes: int
    evidence: dict[str, float]
    reasons: tuple[str, ...]
    shock_state: str


@dataclass(frozen=True, slots=True)
class Target:
    symbol: str
    weight: float
    lifecycle: str
    alpha_score: float
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    date: str
    opportunity: Opportunity
    risk: Risk
    target_gross: float
    target_k: int
    targets: tuple[Target, ...]
    pending_orders: tuple[PendingOrder, ...]
    risk_summary: dict[str, Any]
    decision_digest: str
