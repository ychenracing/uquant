"""Fixed-order portfolio allocation pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from ..models.strategic_universe import StrategicUniverseRoles
from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment, Target
from .allocation_closure import close_allocation
from .allocation_opening import prepare_allocation
from .allocation_protected import restore_protected_allocation
from .allocation_recovery import allocate_recovery
from .allocation_tactical import allocate_tactical

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _allocate_strategy(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> tuple[Target, ...]:
    """Select one strategy route and return targets before final hard caps."""

    state, targets = prepare_allocation(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    if targets is not None:
        return targets
    targets, leader_cycle_armed = allocate_tactical(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        state=state,
    )
    if targets is not None:
        return targets
    targets = restore_protected_allocation(
        self,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        state=state,
    )
    if targets is not None:
        return targets
    targets = allocate_recovery(
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
    return close_allocation(
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


allocate_strategy = _allocate_strategy
