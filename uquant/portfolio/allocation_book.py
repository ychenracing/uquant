"""The single daily capital book shared by allocation stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..types import AccountState, AttributionMechanism, LeaderScore, RiskAssessment, Target
from .capital import funded_increment

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


@dataclass
class AllocationBook:
    """One daily working book shared by every sequential allocation step."""

    policy: PortfolioAllocator
    date: pd.Timestamp
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    prices: dict[str, float]
    weights_now: dict[str, float]
    owned: set[str]
    strategic_targets: dict[str, Target]
    proposed: dict[str, float]
    committed: dict[str, float]
    cash_room: float
    reasons: dict[str, str] = field(default_factory=dict)
    mechanisms: dict[str, AttributionMechanism] = field(default_factory=dict)
    replacements: dict[str, str] = field(default_factory=dict)
    trace: dict[str, dict[str, Any]] = field(default_factory=dict)
    recovery_targets: dict[str, Target] = field(default_factory=dict)
    recovery_restore_symbols: set[str] = field(default_factory=set)

    @property
    def gross_cap(self) -> float:
        return min(self.policy.cfg.max_gross, self.risk.target_gross_cap)

    def record(self, symbol: str) -> dict[str, Any]:
        row = self.trace.setdefault(symbol, {})
        row.setdefault("held_weight", self.weights_now.get(symbol, 0.0))
        return row

    def fund(
        self, symbol: str, desired: float, *, phase: str, minimum: float = 0.0,
        symbol_cap: float | None = None, concentration_cap: float | None = None,
    ) -> bool:
        current = self.weights_now.get(symbol, 0.0)
        reserved = max(0.0, self.committed.get(symbol, 0.0) - current)
        diagnostic: dict[str, Any] = {"phase": phase, "minimum_increment": minimum}
        increment = funded_increment(
            cfg=self.policy.cfg, symbol=symbol, desired=desired, current=current,
            committed=self.committed, cash_room=self.cash_room, leaders=self.leaders,
            user_panel=self.user_panel, date=self.date, gross_cap=self.gross_cap,
            symbol_cap=symbol_cap, concentration_cap=concentration_cap, diagnostics=diagnostic,
        )
        row = self.record(symbol)
        row.setdefault("budget_checks", []).append(diagnostic)
        accepted = increment > 0 and increment + 1e-12 >= minimum
        diagnostic["accepted"] = accepted
        row["allocation_reason"] = phase if accepted else "CAPITAL_LIMIT"
        if accepted:
            self.proposed[symbol] = current + increment
            self.committed[symbol] = max(self.committed.get(symbol, 0.0), self.proposed[symbol])
            self.cash_room -= max(0.0, increment - reserved)
        return accepted
