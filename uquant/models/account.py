"""Durable flat account state model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .enums import Opportunity, Risk
from .strategic_grant import StrategicGrantIntent, StrategicQualificationObservation
from .trading import AccountOrder, Fill, PendingOrder, Position

ACCOUNT_SCHEMA_VERSION = 5


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
    account_identity: str = ""
    strategic_qualification: StrategicQualificationObservation = field(
        default_factory=StrategicQualificationObservation
    )
    strategic_grant: StrategicGrantIntent | None = None
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


__all__ = ("ACCOUNT_SCHEMA_VERSION", "AccountState")
