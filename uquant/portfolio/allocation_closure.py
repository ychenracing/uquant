"""Terminal leader and live-core allocation routes."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Risk,
    RiskAssessment,
    Target,
)
from .context import AllocationState

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _allocate_armed_leader(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
    leader_cycle_armed: bool,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now

    if leader_cycle_armed:
        leader_targets = self._leader_targets(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            prices=prices,
        )
        if leader_targets is not None:
            return leader_targets
    return None


def _retain_confirmed_live_core(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
) -> tuple[set[str], tuple[Target, ...] | None]:
    freeze_active = state.freeze_active
    weights_now = state.weights_now

    live_symbols = {symbol for symbol, position in account.positions.items() if position.shares > 0}
    confirmed_live_core = {
        symbol
        for symbol in live_symbols
        if account.positions[symbol].lifecycle
        in {
            Lifecycle.CORE.value,
            Lifecycle.ADD1.value,
            Lifecycle.ADD2.value,
        }
        and symbol in leaders
        and leaders[symbol].mature
        and leaders[symbol].confidence >= self.cfg.leader_min_confidence
        and leaders[symbol].score >= self.cfg.leader_cycle_min_score
        and symbol in user_panel
        and date in user_panel[symbol].index
        and self._structure_ok(user_panel[symbol], date)
    }
    if (
        live_symbols
        and confirmed_live_core == live_symbols
        and risk.state is Risk.NORMAL
        and not freeze_active
    ):
        # Re-arming controls *new* generic leader risk.  A one-session
        # evidence gap in that owner must not turn two currently mature,
        # structurally intact Core holdings into an all-cash liquidation.
        # Hold only the marked broker book; the normal confirmation streak
        # still has to finish before any admission, add, or rotation.
        return live_symbols, self._targets(
            proposed={symbol: weights_now[symbol] for symbol in live_symbols},
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="confirmed live leader continuity while owner rearms",
            origin_subsystem=OriginSubsystem.LEADER,
            mechanism=AttributionMechanism.LEADER_SELECTION,
            lifecycles={symbol: Lifecycle(account.positions[symbol].lifecycle) for symbol in live_symbols},
        )
    return live_symbols, None


def _slow_market_owner_is_active(
    self: PortfolioAllocator,
    *,
    opportunity: Opportunity,
    risk: RiskAssessment,
    account: AccountState,
    state: AllocationState,
    live_symbols: set[str],
) -> tuple[set[str], bool]:
    freeze_active = state.freeze_active
    live_generic_core = {
        symbol
        for symbol in live_symbols
        if account.positions[symbol].lifecycle
        in {
            Lifecycle.CORE.value,
            Lifecycle.ADD1.value,
            Lifecycle.ADD2.value,
        }
    }
    cohort_prefix = "slow_market_owner_cohort:"
    for key in tuple(account.replacement_tenure):
        if key.startswith(cohort_prefix) and key[len(cohort_prefix) :] not in live_symbols:
            account.replacement_tenure[key] = 0
    slow_market_owner_trigger = bool(
        live_symbols
        and live_generic_core == live_symbols
        and risk.state is Risk.NORMAL
        and not freeze_active
        and opportunity is Opportunity.STRONG_TREND
        and len(live_symbols) <= self.cfg.leader_cycle_min_mature
        and min(
            float(risk.evidence.get("broad_ret120", -math.inf)),
            float(risk.evidence.get("tech_ret120", -math.inf)),
        )
        < self.cfg.leader_cycle_min_market_ret120
    )
    if slow_market_owner_trigger:
        for symbol in live_symbols:
            account.replacement_tenure[f"{cohort_prefix}{symbol}"] = 1
    slow_market_owner_active = any(
        account.replacement_tenure.get(f"{cohort_prefix}{symbol}", 0) == 1 for symbol in live_symbols
    )
    return live_generic_core, slow_market_owner_active


def _close_live_core(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    live_symbols: set[str],
) -> tuple[Target, ...]:
    freeze_active = state.freeze_active
    weights_now = state.weights_now
    live_generic_core, slow_market_owner_active = _slow_market_owner_is_active(
        self,
        opportunity=opportunity,
        risk=risk,
        account=account,
        state=state,
        live_symbols=live_symbols,
    )
    if (
        live_symbols
        and live_generic_core == live_symbols
        and slow_market_owner_active
        and risk.state is Risk.NORMAL
        and not freeze_active
    ):
        # A minimum viable leader cohort can face a strong short-term
        # impulse while one slow index leg remains just below the ordinary
        # owner threshold. Once that exact handoff occurs, keep its live
        # cohort under the existing per-symbol exit confirmation until
        # every marked member has left the broker book.
        confirmed_exits = {
            symbol
            for symbol in live_symbols
            if self._leader_lifecycle_exit_confirmed(
                symbol=symbol,
                date=date,
                user_panel=user_panel,
                leaders=leaders,
                account=account,
            )
        }
        retained = live_symbols - confirmed_exits
        if retained:
            account.active_leaders = [
                symbol for symbol in account.active_leaders if symbol not in confirmed_exits
            ]
            return self._targets(
                proposed={symbol: weights_now[symbol] for symbol in retained},
                leaders=leaders,
                account=account,
                lifecycle=Lifecycle.CORE,
                reason="live core retained through slow-market owner handoff",
                origin_subsystem=OriginSubsystem.LEADER,
                mechanism=AttributionMechanism.LEADER_SELECTION,
                lifecycles={
                    symbol: Lifecycle(account.positions[symbol].lifecycle) for symbol in live_symbols
                },
                reasons={
                    symbol: "leader lifecycle exit: confirmed structural deterioration"
                    for symbol in confirmed_exits
                },
                mechanisms={symbol: AttributionMechanism.LEADER_LIFECYCLE_EXIT for symbol in confirmed_exits},
            )

    # With no independently confirmed recovery leader the robust action is
    # cash. This prevents a broad input pool from turning into a generic,
    # high-churn momentum strategy merely because more symbols were supplied.
    return self._targets(
        proposed={},
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="no independently confirmed leader",
        origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_LIFECYCLE_EXIT,
    )


def close_allocation(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    state: AllocationState,
    leader_cycle_armed: bool,
) -> tuple[Target, ...]:
    """Return the final leader or retained-core allocation."""

    targets = _allocate_armed_leader(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
        leader_cycle_armed=leader_cycle_armed,
    )
    if targets is not None:
        return targets
    live_symbols, targets = _retain_confirmed_live_core(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
    )
    if targets is not None:
        return targets
    return _close_live_core(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
        live_symbols=live_symbols,
    )
