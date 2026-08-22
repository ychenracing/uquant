"""Deterministic strategic target construction; lifecycle owns all state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    OriginSubsystem,
    Target,
)

if TYPE_CHECKING:
    from .discovery import StrategicPortfolioPolicy

def _strategic_completed_exit_targets(
    self: StrategicPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[Target, ...]:
    return self._targets(
        proposed={},
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="strategic cohort completed staged exit",
        origin_subsystem=OriginSubsystem.STRATEGIC,
        mechanism=AttributionMechanism.STRATEGIC_TRAILING_EXIT,
    )


def _strategic_active_targets(
    self: StrategicPortfolioPolicy,
    *,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    dominant_profit_lock_armed_now: bool,
    dominant_symbol: str | None,
    current_selected: dict[str, float],
) -> tuple[Target, ...]:
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="prequalified strategic leader cohort with staged profit protection",
        origin_subsystem=OriginSubsystem.STRATEGIC,
        mechanism=AttributionMechanism.STRATEGIC_COHORT,
        reasons=(
            {
                dominant_symbol: "strategic dominant one-shot profit lock",
            }
            if dominant_profit_lock_armed_now and dominant_symbol is not None
            else None
        ),
        mechanisms={
            symbol: (
                AttributionMechanism.STRATEGIC_PROFIT_LOCK
                if dominant_profit_lock_armed_now and symbol == dominant_symbol
                else AttributionMechanism.STRATEGIC_TRAILING_EXIT
                if symbol in account.strategic_exit_bands
                else AttributionMechanism.STRATEGIC_RESTORATION
                if symbol in account.strategic_restore_weights
                and proposed.get(symbol, 0.0) > current_selected.get(symbol, 0.0) + 1e-12
                else AttributionMechanism.STRATEGIC_COHORT
            )
            for symbol in set(account.positions) | set(proposed)
        },
    )
