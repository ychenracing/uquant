"""Stable recovery policy class; daily book owns active admission stages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...types import AccountState, RiskAssessment
from ..leaders import LeaderPortfolioPolicy


class RecoveryPortfolioPolicy(LeaderPortfolioPolicy):
    """Keep the allocator's public recovery policy class identity."""

    if TYPE_CHECKING:

        def _confirmed_recovery_gross(
            self,
            *,
            risk: RiskAssessment,
            account: AccountState,
        ) -> float: ...


RecoveryPortfolioPolicy.__module__ = "uquant.portfolio_recovery"
