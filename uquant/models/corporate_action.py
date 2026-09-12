"""Source-bound corporate actions and durable account entitlements."""
from __future__ import annotations

from dataclasses import dataclass, field

from .trading import Tranche


@dataclass(frozen=True, slots=True)
class CorporateAction:
    action_id: str
    symbol: str
    disclosed_date: str
    record_date: str
    ex_date: str
    payment_date: str
    payment_phase: str
    cash_per_share: float
    cash_adjustment_per_share: float
    share_ratio: float
    reference_share_ratio: float
    share_available_date: str
    share_tax_acquired_date: str
    share_tax_acquisition_rule: str
    source_url: str
    source_sha256: str
    tax_category: str
    bonus_tax_per_share: float


@dataclass(slots=True)
class DividendTaxLot:
    lot_id: str
    acquired_date: str
    shares: int
    remaining_shares: int


@dataclass(slots=True)
class CorporateActionState:
    action: CorporateAction
    entitled_lots: list[Tranche] = field(default_factory=list)
    tax_lots: list[DividendTaxLot] = field(default_factory=list)
    recorded_date: str = ""
    ex_processed_date: str = ""
    paid_date: str = ""
    distributed_date: str = ""
    income_cash: float = 0.0
    tax_assessed: float = 0.0
    tax_cash: float = 0.0
    distributed_shares: int = 0


@dataclass(frozen=True, slots=True)
class DividendTaxDebit:
    debit_id: str
    action_id: str
    date: str
    phase: str
    amount: float
    source_url: str
    source_sha256: str
