"""Crash-repair admission preserved as ordered domain stages."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from ...portfolio_leaders import LeaderPortfolioPolicy
from ...types import AccountState, LeaderScore, Opportunity, RiskAssessment, Target
from .cohort_admission import cohort_admission_targets
from .tactical_admission import tactical_admission_targets


class RecoveryPortfolioPolicy(LeaderPortfolioPolicy):
    """Replace a damaged recovery secondary without creating a second book."""

    if TYPE_CHECKING:

        def _confirmed_recovery_gross(
            self,
            *,
            risk: RiskAssessment,
            account: AccountState,
        ) -> float: ...

        def _recovery_anchor_substitution(
            self,
            *,
            date: pd.Timestamp,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            weights_now: dict[str, float],
            anchor_elapsed: int,
            risk_neutral_only: bool = False,
        ) -> tuple[Target, ...] | None: ...


RecoveryPortfolioPolicy.__module__ = "uquant.portfolio_recovery"


def _general_core_recovery_targets(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    general_core_symbols: set[str],
    risk_neutral_recovery_handoff: bool,
) -> tuple[Target, ...] | None:
    has_general_core = not account.anchor_weights and bool(general_core_symbols)
    if opportunity is Opportunity.RECOVERY and has_general_core and not risk_neutral_recovery_handoff:
        recovery_hold = self._leader_targets(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            prices=prices,
        )
        if recovery_hold is not None:
            return recovery_hold
    return None


def _recovery_admission_targets(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    anchored_held: dict[str, float],
    bounded_recovery_repair: bool,
    freeze_active: bool,
    general_core_symbols: set[str],
    level1_recovery_repair: bool,
    risk_neutral_recovery_handoff: bool,
    risk_neutral_recovery_transfer: bool,
    tactical_recovery_market: bool,
    transitional_recovery_market: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    targets = _general_core_recovery_targets(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        weights_now=weights_now,
        general_core_symbols=general_core_symbols,
        risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
    )
    if targets is not None:
        return targets
    targets = tactical_admission_targets(
        self,
        opportunity=opportunity,
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        level1_recovery_repair=level1_recovery_repair,
        bounded_recovery_repair=bounded_recovery_repair,
        tactical_recovery_market=tactical_recovery_market,
        transitional_recovery_market=transitional_recovery_market,
        weak_secular_market=weak_secular_market,
    )
    if targets is not None:
        return targets
    if opportunity is Opportunity.RECOVERY:
        return cohort_admission_targets(
            self,
            date=date,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            anchored_held=anchored_held,
            bounded_recovery_repair=bounded_recovery_repair,
            freeze_active=freeze_active,
            level1_recovery_repair=level1_recovery_repair,
            risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
            risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
            weak_secular_market=weak_secular_market,
        )
    return None


# Allocation-stage entry point owned by this module.
recovery_admission_targets = _recovery_admission_targets
