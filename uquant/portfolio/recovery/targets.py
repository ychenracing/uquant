"""Deterministic recovery target construction; lifecycle owners mutate state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    OriginSubsystem,
    RiskAssessment,
    Target,
)

if TYPE_CHECKING:
    from ...portfolio_leaders import LeaderPortfolioPolicy


def _overextended_pullback_targets(
    self: LeaderPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[Target, ...]:
    return self._targets(
        proposed={},
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="overextended pullback cooldown",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.TACTICAL_REBOUND,
    )


def _controlled_oversold_rebound_targets(
    self: LeaderPortfolioPolicy,
    *,
    pick: LeaderScore,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[Target, ...]:
    return self._targets(
        proposed={
            pick.symbol: min(
                self.cfg.tactical_probe_weight,
                risk.target_gross_cap,
            )
        },
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="controlled oversold rebound probe",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.TACTICAL_REBOUND,
    )


def _locked_recovery_cohort_targets(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[Target, ...]:
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="causal crash-recovery leader",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.RECOVERY_COHORT,
    )


def _awaiting_recovery_cohort_targets(
    self: LeaderPortfolioPolicy,
    *,
    anchored_held: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[Target, ...]:
    return self._targets(
        proposed=anchored_held,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="awaiting recovery cohort member confirmation",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.RECOVERY_COHORT,
    )


def _recovery_cohort_targets(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    capped: bool,
    cohort_changed: bool,
) -> tuple[Target, ...]:
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason=(
            "under-diversified recovery cap"
            if capped
            else "recovery cohort construction"
            if cohort_changed
            else "causal crash-recovery leader"
        ),
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=(AttributionMechanism.RECOVERY_CAP if capped else AttributionMechanism.RECOVERY_COHORT),
    )


# Stable owned APIs for Task-10 admission stages.  Historical private names
# remain exact Task-8 implementation identities for immutable expansion.
overextended_pullback_targets = _overextended_pullback_targets
controlled_oversold_rebound_targets = _controlled_oversold_rebound_targets
locked_recovery_cohort_targets = _locked_recovery_cohort_targets
awaiting_recovery_cohort_targets = _awaiting_recovery_cohort_targets
recovery_cohort_targets = _recovery_cohort_targets


def _pending_recovery_substitution_targets(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    structured_replacements: dict[str, str],
) -> tuple[Target, ...]:
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="confirmed recovery anchor substitution",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.RECOVERY_SUBSTITUTION,
        replaces_symbols=structured_replacements,
    )


def _confirmed_recovery_substitution_targets(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    incumbent: str,
    challenger: LeaderScore,
) -> tuple[Target, ...]:
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="confirmed recovery anchor substitution",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.RECOVERY_SUBSTITUTION,
        reasons={
            incumbent: f"recovery anchor exit: {challenger.symbol} confirmed edge",
            challenger.symbol: f"recovery anchor entry: replaces {incumbent}",
        },
        replaces_symbols={challenger.symbol: incumbent},
    )


confirmed_recovery_substitution_targets = _confirmed_recovery_substitution_targets
pending_recovery_substitution_targets = _pending_recovery_substitution_targets
