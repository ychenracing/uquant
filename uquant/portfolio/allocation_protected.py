"""Protected-book restoration in original transaction order."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    OriginSubsystem,
    RiskAssessment,
    Target,
)
from .context import AllocationState, ProtectedRestoration

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _protected_restoration_is_open(
    *,
    risk: RiskAssessment,
    account: AccountState,
    state: AllocationState,
) -> bool:
    return bool(
        account.protected_weights
        and risk.state.value in {"NORMAL", "CAUTION"}
        and not state.risk_neutral_recovery_handoff
    )


def _protected_proposal(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    state: AllocationState,
) -> tuple[dict[str, float], set[str], bool] | None:
    weights_now = state.weights_now
    if _protected_restoration_is_open(risk=risk, account=account, state=state):
        # Restoration intent survives transient FAILED_REPAIR observations.
        # The CAUTION freeze above still blocks a buy while continuous
        # damage is active; once independent repair clears it, retaining
        # the protected target prevents a one-day state label from
        # permanently stranding the book at crisis gross.
        account.candidate_tenure["post_shock_recovery"] = int(account.shock_severity == "SEVERE")
        proposed = {
            symbol: min(self.cfg.max_symbol_weight, weight)
            for symbol, weight in account.protected_weights.items()
            if symbol in user_panel and symbol not in account.strategic_cohort_targets
        }
        pending_replacement_members: set[str] = set()
        pending_recovery_alternative = any(
            key.startswith("recovery_admission:") and tenure > 0
            for key, tenure in account.replacement_tenure.items()
        )
        if pending_recovery_alternative and proposed:
            # An independently observed alternative is already inside the
            # existing recovery-admission confirmation. Rebuying every
            # underweight secondary now, only to fund its imminent owner
            # handoff, is deterministic churn. Restore the conviction lead
            # while retaining secondary capital at its live weight until
            # the existing admission/substitution process resolves.
            protected_lead = max(
                proposed,
                key=lambda symbol: (proposed[symbol], symbol),
            )
            for symbol, desired in tuple(proposed.items()):
                current = weights_now.get(symbol, 0.0)
                if symbol != protected_lead and current < desired - 1e-12:
                    proposed[symbol] = current
                    pending_replacement_members.add(symbol)
        total = sum(proposed.values())
        explicit_cap = min(self.cfg.max_gross, max(0.0, risk.target_gross_cap))
        current_gross = sum(max(0.0, weight) for weight in weights_now.values())
        if total > explicit_cap and total > 0 and current_gross <= explicit_cap + 1e-12:
            proposed = {symbol: weight * explicit_cap / total for symbol, weight in proposed.items()}
        fully_repaired = bool(
            risk.state.value == "NORMAL"
            and risk.shock_state == "NONE"
            and not risk.freeze_new_risk
            and account.capital_budget_level == 0
            and account.chronic_level == 0
        )
        return proposed, pending_replacement_members, fully_repaired
    return None


def _restoration_status(
    self: PortfolioAllocator,
    *,
    account: AccountState,
    state: AllocationState,
    proposed: dict[str, float],
    pending_replacement_members: set[str],
    fully_repaired: bool,
) -> ProtectedRestoration:
    weights_now = state.weights_now
    equity = state.equity
    # RiskAssessment is the only owner of the day's gross cap.  A book
    # already below the allowance may BUY saved intent up to that cap.
    # An overweight book keeps full targets here so the single outer
    # reducer can apply global tranche priority to the required SELL.
    # ``protected_weights`` remains alive until the risk engine has
    # observed structural normalization, but it must not remain a
    # permanent rebalancing target.  Once the one economic restore is
    # filled (including every capacity-limited child fill), switch to
    # the same drift-tolerant hold semantics as a mature anchor.  A
    # later independent crisis resets this marker when it captures a
    # fresh protected book.
    restore_complete_key = "post_shock_restore_complete"
    restore_submitted_key = "post_shock_restore_submitted"
    restore_deferred_key = "post_shock_restore_deferred_expansion"
    restore_previously_submitted = account.candidate_tenure.get(restore_submitted_key, 0) == 1
    restore_expansion_deferred = account.candidate_tenure.get(restore_deferred_key, 0) == 1
    pending_restore_buys = {
        order.symbol for order in account.pending_orders if order.side == "BUY" and order.symbol in proposed
    }
    restore_confirmation_ready = bool(
        account.risk_streaks.get("protected_structure_normalization", 0)
        >= self.cfg.recovery_risk_confirm_days
    )
    if restore_expansion_deferred and restore_confirmation_ready and not pending_restore_buys:
        # The existing recovery confirmation has now caught up with
        # the first bounded step. Reopen one final submission against
        # the saved intent instead of treating that step as complete.
        account.candidate_tenure[restore_submitted_key] = 0
        account.candidate_tenure[restore_deferred_key] = 0
        restore_previously_submitted = False
        restore_expansion_deferred = False
    executable_buy_gap = {
        symbol: max(
            0.0,
            (desired - weights_now.get(symbol, 0.0)) * equity,
        )
        for symbol, desired in proposed.items()
    }
    restoration_trade_threshold = {
        symbol: (
            self.cfg.protected_restore_min_trade_weight
            if desired >= self.cfg.core_admission_weight
            else self.cfg.restoration_min_trade_weight
        )
        * equity
        for symbol, desired in proposed.items()
    }
    restoration_completion_threshold = self.cfg.min_trade_weight * equity
    economic_restore_complete = bool(
        proposed
        and not pending_restore_buys
        and (
            (restore_previously_submitted and not restore_expansion_deferred)
            or (
                fully_repaired
                and all(
                    gap + 1e-12 * equity < restoration_trade_threshold[symbol]
                    or (
                        gap < restoration_completion_threshold
                        and weights_now.get(symbol, 0.0) >= 0.95 * proposed[symbol]
                    )
                    for symbol, gap in executable_buy_gap.items()
                )
            )
        )
    )
    restore_submission_has_buy = bool(
        pending_restore_buys
        or any(
            gap + 1e-12 * equity >= restoration_trade_threshold[symbol]
            for symbol, gap in executable_buy_gap.items()
        )
    )
    return ProtectedRestoration(
        proposed=proposed,
        pending_replacement_members=pending_replacement_members,
        fully_repaired=fully_repaired,
        restore_complete_key=restore_complete_key,
        restore_submitted_key=restore_submitted_key,
        restore_deferred_key=restore_deferred_key,
        pending_restore_buys=pending_restore_buys,
        restore_confirmation_ready=restore_confirmation_ready,
        restore_expansion_deferred=restore_expansion_deferred,
        economic_restore_complete=economic_restore_complete,
        restore_submission_has_buy=restore_submission_has_buy,
    )


def _deferred_restoration_targets(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    restoration: ProtectedRestoration,
) -> tuple[Target, ...] | None:
    weights_now = state.weights_now
    proposed = restoration.proposed
    pending_restore_buys = restoration.pending_restore_buys
    restore_confirmation_ready = restoration.restore_confirmation_ready
    restore_expansion_deferred = restoration.restore_expansion_deferred
    economic_restore_complete = restoration.economic_restore_complete
    if (
        restore_expansion_deferred
        and not restore_confirmation_ready
        and not pending_restore_buys
        and not economic_restore_complete
    ):
        return self._targets(
            proposed={
                symbol: weights_now.get(symbol, 0.0)
                for symbol in proposed
                if weights_now.get(symbol, 0.0) > 1e-12
            },
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="awaiting confirmed recovery before restore expansion",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
        )
    return None


def _commit_restoration(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    restoration: ProtectedRestoration,
) -> tuple[dict[str, AttributionMechanism], tuple[Target, ...] | None]:
    weights_now = state.weights_now
    synchronized_protected_restore = state.synchronized_protected_restore
    proposed = restoration.proposed
    restore_complete_key = restoration.restore_complete_key
    restore_submitted_key = restoration.restore_submitted_key
    restore_deferred_key = restoration.restore_deferred_key
    restore_confirmation_ready = restoration.restore_confirmation_ready
    economic_restore_complete = restoration.economic_restore_complete
    restore_submission_has_buy = restoration.restore_submission_has_buy
    if proposed and (synchronized_protected_restore or restore_submission_has_buy):
        # Pending capacity-limited children may finish. A generic
        # bounded step waits for the existing recovery confirmation
        # before any cap expansion; a synchronized or already-
        # confirmed step then keeps the original one-shot semantics.
        account.candidate_tenure[restore_submitted_key] = 1
        account.candidate_tenure[restore_deferred_key] = int(
            not synchronized_protected_restore and not restore_confirmation_ready
        )
    if economic_restore_complete:
        account.candidate_tenure[restore_complete_key] = 1
        account.candidate_tenure[restore_submitted_key] = 0
        account.candidate_tenure[restore_deferred_key] = 0
    restoration_sell_mechanisms = {
        symbol: AttributionMechanism.RECOVERY_COHORT
        for symbol in account.positions
        if weights_now.get(symbol, 0.0) > proposed.get(symbol, 0.0) + 1e-12
    }
    if account.candidate_tenure.get(restore_complete_key, 0) == 1:
        return restoration_sell_mechanisms, self._targets(
            proposed={
                symbol: weights_now.get(symbol, 0.0)
                for symbol in proposed
                if weights_now.get(symbol, 0.0) > 1e-12
            },
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.RECOVERY,
            reason="completed post-shock restoration; retain price drift",
            origin_subsystem=OriginSubsystem.RECOVERY,
            mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
            mechanisms=restoration_sell_mechanisms,
        )
    return restoration_sell_mechanisms, None


def _final_restoration_targets(
    self: PortfolioAllocator,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
    restoration: ProtectedRestoration,
    restoration_sell_mechanisms: dict[str, AttributionMechanism],
) -> tuple[Target, ...]:
    weights_now = state.weights_now
    proposed = restoration.proposed
    pending_replacement_members = restoration.pending_replacement_members
    fully_repaired = restoration.fully_repaired
    # Once risk has fully reopened, restoration is buy-only.  A
    # winner that drifted above its saved target receives a sticky
    # hold reason while lagging members retain the one saved BUY
    # target. During an incomplete repair the severity cap remains a
    # genuine risk reduction and must not receive this exemption.
    restore_reasons: dict[str, str] | None = (
        {
            symbol: "post-shock restoration; retain winner drift"
            for symbol, desired in proposed.items()
            if weights_now.get(symbol, 0.0) >= desired - 1e-12
        }
        if fully_repaired
        else None
    )
    if pending_replacement_members:
        restore_reasons = dict(restore_reasons or {})
        restore_reasons.update(
            {
                symbol: "post-shock restoration; retain pending replacement capital"
                for symbol in pending_replacement_members
            }
        )
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.RECOVERY,
        reason="confirmed post-shock restoration",
        origin_subsystem=OriginSubsystem.RECOVERY,
        mechanism=AttributionMechanism.POST_SHOCK_RESTORATION,
        reasons=restore_reasons,
        mechanisms=restoration_sell_mechanisms,
    )


def restore_protected_allocation(
    self: PortfolioAllocator,
    *,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    state: AllocationState,
) -> tuple[Target, ...] | None:
    """Restore saved intent without changing cap or completion ordering."""

    proposal = _protected_proposal(
        self,
        risk=risk,
        user_panel=user_panel,
        account=account,
        state=state,
    )
    if proposal is None:
        return None
    proposed, pending_replacement_members, fully_repaired = proposal
    restoration = _restoration_status(
        self,
        account=account,
        state=state,
        proposed=proposed,
        pending_replacement_members=pending_replacement_members,
        fully_repaired=fully_repaired,
    )
    targets = _deferred_restoration_targets(
        self,
        leaders=leaders,
        account=account,
        state=state,
        restoration=restoration,
    )
    if targets is not None:
        return targets
    restoration_sell_mechanisms, targets = _commit_restoration(
        self,
        leaders=leaders,
        account=account,
        state=state,
        restoration=restoration,
    )
    if targets is not None:
        return targets
    return _final_restoration_targets(
        self,
        leaders=leaders,
        account=account,
        state=state,
        restoration=restoration,
        restoration_sell_mechanisms=restoration_sell_mechanisms,
    )
