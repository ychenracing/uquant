"""Mechanical Task 8 owner extracted from the immutable allocator."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING

import pandas as pd

from ..portfolio_core import (
    current_weights,
    strategic_dominant_symbol,
)
from ..portfolio_recovery import RecoveryPortfolioPolicy
from ..risk_sentinel.integration import sentinel_freeze_authorized
from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Opportunity,
    Risk,
    RiskAssessment,
    Target,
)


def _confirmed_recovery_gross(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    account: AccountState,
) -> float:
    """Return one stable aggregate cap for a locked three-member cohort."""
    explicit_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
    fully_repaired = bool(
        risk.state is Risk.NORMAL
        and not risk.freeze_new_risk
        and not bool(risk.evidence.get("freeze_new_risk", False))
        and account.capital_budget_level == 0
        and account.chronic_level == 0
    )
    return (
        explicit_cap
        if fully_repaired
        else min(self.cfg.recovery_target_gross, explicit_cap)
    )

def allocate(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
) -> tuple[Target, ...]:
    """Apply the risk engine's gross cap to every strategy return path."""
    sentinel_only_freeze = sentinel_freeze_authorized(risk)
    strategy_risk = risk
    if sentinel_only_freeze:
        strategy_evidence = {
            **risk.evidence,
            "sentinel_freeze_new_risk": False,
            "freeze_new_risk": False,
        }
        strategy_risk = replace(
            risk,
            evidence=strategy_evidence,
            freeze_new_risk=False,
        )
    strategy_account = deepcopy(account) if sentinel_only_freeze else account
    try:
        targets = self._allocate_strategy(
            date=date,
            opportunity=opportunity,
            risk=strategy_risk,
            user_panel=user_panel,
            leaders=leaders,
            account=strategy_account,
            prices=prices,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"portfolio allocation failed on {date.date()} "
            f"for opportunity={opportunity.value}, risk={risk.state.value}: {exc}"
        ) from exc
    if sentinel_only_freeze:
        weights_now, _ = current_weights(account, prices)
        targets = self._frozen_existing_targets(
            strategy_targets=targets,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
        )
        allowed_exit_symbols = {
            target.symbol
            for target in targets
            if target.weight + 1e-12 < weights_now.get(target.symbol, 0.0)
        }
        self._commit_frozen_exit_state(
            account=account,
            planned_account=strategy_account,
            allowed_exit_symbols=allowed_exit_symbols,
        )
    gross_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
    risk_reason, risk_reason_code, risk_exit_kind = self._risk_reduction_metadata(risk)
    target_gross = sum(item.weight for item in targets if item.weight > 0)
    weights_now, _ = current_weights(account, prices)
    current_gross = sum(weight for weight in weights_now.values() if weight > 0)
    dominant_symbol = strategic_dominant_symbol(account)
    live_symbols = {
        symbol for symbol, weight in weights_now.items() if weight > 1e-12
    }
    dominant_level1_retention = bool(
        dominant_symbol is not None
        and live_symbols == {dominant_symbol}
        and risk.state in {Risk.NORMAL, Risk.CAUTION}
        and risk.reduction_level <= 1
        and not bool(risk.evidence.get("sector_guard_active", False))
        and not bool(risk.evidence.get("strategic_damage_guard", False))
        and not bool(risk.evidence.get("acute_sector_evacuation", False))
        # Strategy-owned reductions, including the one-shot profit lock,
        # remain authoritative.  This exception only converts an ordinary
        # level-1 cap into a freeze of an unchanged incumbent.
        and target_gross >= current_gross - 1e-12
    )
    if dominant_level1_retention:
        gross_cap = max(
            gross_cap,
            min(self.cfg.strategic_dominant_max_weight, current_gross),
        )
    if (
        current_gross > gross_cap + 1e-12
        and risk_reason_code != "strategic_damage_guard"
        and account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    ):
        # Only one risk owner may claim a young strategic transition.  If
        # the ordinary capital ladder has already forced a reduction in
        # this epoch, a later fall back into the early-warning band cannot
        # layer a second, tighter strategic guard onto the same damage.
        account.candidate_tenure[
            "strategic_external_risk_epoch"
        ] = account.strategic_epoch
    if target_gross <= gross_cap + 1e-12:
        if current_gross <= gross_cap + 1e-12:
            return targets
        return self._sparse_risk_reduce(
            targets=targets,
            weights_now=weights_now,
            account=account,
            gross_cap=gross_cap,
            risk_reason=risk_reason,
            risk_reason_code=risk_reason_code,
            risk_exit_kind=risk_exit_kind,
            prices=prices,
        )
    return self._sparse_risk_reduce(
        targets=targets,
        weights_now=weights_now,
        account=account,
        gross_cap=gross_cap,
        risk_reason=risk_reason,
        risk_reason_code=risk_reason_code,
        risk_exit_kind=risk_exit_kind,
        prices=prices,
    )


class PortfolioAllocator(RecoveryPortfolioPolicy):
    """Compose strategic, leader, recovery, and hard-constraint policies.

    The allocator remains the sole target-weight owner. The policy layers
    contain evidence and lifecycle behavior only; none can submit an order.
    """

    if TYPE_CHECKING:

        def _confirmed_recovery_gross(
            self,
            *,
            risk: RiskAssessment,
            account: AccountState,
        ) -> float: ...

        @staticmethod
        def _risk_attribution_mechanism(
            reason_code: str,
        ) -> AttributionMechanism: ...

        def _risk_retention_score(
            self,
            target: Target,
            account: AccountState,
        ) -> float: ...

        @staticmethod
        def _risk_retention_vector(
            target: Target,
            account: AccountState,
            retained_weight: float,
            current_weight: float,
        ) -> tuple[float, float, float, float, float, float]: ...

        @staticmethod
        def _risk_lifecycle_rank(
            retained: tuple[float, float, float, float, float, float],
        ) -> tuple[float, float, float, float, float, float]: ...

        def _subset_retention_vector(
            self,
            targets: tuple[Target, ...],
            account: AccountState,
            retained_weights: dict[str, float],
            weights_now: dict[str, float],
        ) -> tuple[float, float, float, float, float, float]: ...

        def _sparse_risk_reduce(
            self,
            *,
            targets: tuple[Target, ...],
            weights_now: dict[str, float],
            account: AccountState,
            gross_cap: float,
            risk_reason: str = "portfolio risk gross cap",
            risk_reason_code: str = "risk_gross_cap",
            risk_exit_kind: str = "risk",
            prices: dict[str, float] | None = None,
        ) -> tuple[Target, ...]: ...

        @staticmethod
        def _risk_reduction_metadata(
            risk: RiskAssessment,
        ) -> tuple[str, str, str]: ...

        def _turnover_aware_sector_cap(
            self,
            *,
            targets: tuple[Target, ...],
            weights_now: dict[str, float],
            account: AccountState,
            gross_cap: float,
        ) -> tuple[Target, ...]: ...

        def allocate(
            self,
            *,
            date: pd.Timestamp,
            opportunity: Opportunity,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            prices: dict[str, float],
        ) -> tuple[Target, ...]: ...

        @staticmethod
        def _commit_frozen_exit_state(
            *,
            account: AccountState,
            planned_account: AccountState,
            allowed_exit_symbols: set[str],
        ) -> None: ...

        @staticmethod
        def _frozen_existing_targets(
            *,
            strategy_targets: tuple[Target, ...] | None,
            leaders: dict[str, LeaderScore],
            account: AccountState,
            weights_now: dict[str, float],
        ) -> tuple[Target, ...]: ...

        def _allocate_strategy(
            self,
            *,
            date: pd.Timestamp,
            opportunity: Opportunity,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            prices: dict[str, float],
        ) -> tuple[Target, ...]: ...


PortfolioAllocator.__module__ = "uquant.portfolio"
