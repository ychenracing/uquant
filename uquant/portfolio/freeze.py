"""Mechanical Task 8 owner extracted from the immutable allocator."""

from __future__ import annotations

from copy import deepcopy

from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    OriginSubsystem,
    Target,
)


def _commit_frozen_exit_state(
    *,
    account: AccountState,
    planned_account: AccountState,
    allowed_exit_symbols: set[str],
) -> None:
    """Commit only monotonic strategy cleanup for allowed independent exits."""

    live_symbols = {
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0
    }
    tactical_exit = bool(
        account.candidate_tenure.get("tactical_active", 0) == 1
        and planned_account.candidate_tenure.get("tactical_active", 0) == 0
    )
    strategic_exit = bool(
        account.candidate_tenure.get("strategic_cohort_active", 0) == 1
        and planned_account.candidate_tenure.get("strategic_cohort_active", 0) == 0
    )
    recovery_exit = bool(
        account.anchor_weights != planned_account.anchor_weights
        and not set(account.anchor_weights).intersection(live_symbols)
    )
    cleanup_symbols = set(allowed_exit_symbols)
    if tactical_exit and account.tactical_anchor_symbol:
        cleanup_symbols.add(account.tactical_anchor_symbol)
    if strategic_exit:
        cleanup_symbols.update(account.strategic_cohort_symbols)
    if recovery_exit:
        cleanup_symbols.update(account.anchor_weights)
    if not cleanup_symbols and not (tactical_exit or strategic_exit or recovery_exit):
        return
    for field_name in (
        "active_leaders",
        "strategic_cohort_symbols",
        "strategic_previous_symbols",
        "risk_anchor_symbols",
    ):
        current = getattr(account, field_name)
        planned = set(getattr(planned_account, field_name))
        setattr(
            account,
            field_name,
            [
                symbol
                for symbol in current
                if symbol not in cleanup_symbols or symbol in planned
            ],
        )
    for field_name in (
        "leader_tenure",
        "satellite_entry_dates",
        "anchor_weights",
        "protected_weights",
        "strategic_cohort_targets",
        "strategic_exit_bands",
        "strategic_active_bands",
        "strategic_restore_weights",
    ):
        current = getattr(account, field_name)
        planned = getattr(planned_account, field_name)
        for symbol in cleanup_symbols:
            if symbol in current and symbol not in planned:
                current.pop(symbol, None)
    for field_name in ("recovery_conviction_symbol", "tactical_anchor_symbol"):
        symbol = getattr(account, field_name)
        if (
            symbol in cleanup_symbols
            and not getattr(planned_account, field_name)
        ):
            setattr(account, field_name, "")
    existing_events = len(account.lifecycle_events)
    for event in planned_account.lifecycle_events[existing_events:]:
        event_symbol = event.get("symbol")
        event_name = str(event.get("event", "")).lower()
        if (
            isinstance(event_symbol, str)
            and event_symbol in cleanup_symbols
            and "exit" in event_name
        ):
            account.lifecycle_events.append(deepcopy(event))

    def commit_tenure_prefixes(prefixes: tuple[str, ...]) -> None:
        keys = set(account.candidate_tenure) | set(planned_account.candidate_tenure)
        for key in keys:
            if not key.startswith(prefixes):
                continue
            if key in planned_account.candidate_tenure:
                account.candidate_tenure[key] = planned_account.candidate_tenure[key]
            else:
                account.candidate_tenure.pop(key, None)

    if tactical_exit:
        commit_tenure_prefixes(("tactical_", "recovery_cycle_"))
        account.tactical_anchor_symbol = planned_account.tactical_anchor_symbol
    if recovery_exit:
        # Commit the canonical old-cohort release, never the unrestricted
        # planner's possible same-day recovery admission.
        account.recovery_anchor_date = ""
        account.candidate_tenure["recovery_cohort_locked"] = 0
        account.candidate_tenure["recovery_cohort_graduated"] = 1
        account.candidate_tenure["diversification_capped"] = 0
        account.candidate_tenure["confirmed_anchor_pair"] = 0
        account.candidate_tenure["confirmed_pair_balanced"] = 0
        account.candidate_tenure["recovery_substitution_pending"] = 0
        account.candidate_tenure["recovery_substitution_completed"] = 0
        account.candidate_tenure["cross_industry_hard_risk_trail"] = 0
        for key in tuple(account.replacement_tenure):
            if key.startswith("hard_risk_winner_trail:"):
                account.replacement_tenure.pop(key, None)
    if strategic_exit:
        commit_tenure_prefixes(("strategic_",))
        account.strategic_epochs_completed = planned_account.strategic_epochs_completed
        account.strategic_last_exit_date = planned_account.strategic_last_exit_date
        account.strategic_rearm_date = planned_account.strategic_rearm_date
        account.strategic_candidate_signature = planned_account.strategic_candidate_signature
        account.strategic_previous_symbols = list(
            planned_account.strategic_previous_symbols
        )

def _frozen_existing_targets(
    *,
    strategy_targets: tuple[Target, ...] | None,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
) -> tuple[Target, ...]:
    """Block additions while preserving every durable reduction intent.

    A risk freeze cancels unfinished BUYs but is not an exit signal.  An
    existing SELL must retain its exact immutable order intent, and a new
    strategy-owned reduction (for example a strategic trailing band) must
    remain executable.  Only proposed increases are replaced by the live
    marked weight.
    """
    proposed_by_symbol = {target.symbol: target for target in strategy_targets or ()}
    frozen: list[Target] = []
    for symbol in sorted(account.positions):
        position = account.positions[symbol]
        if position.shares <= 0:
            continue
        current_weight = weights_now.get(symbol, 0.0)
        pending_sell = next(
            (
                order
                for order in account.pending_orders
                if order.symbol == symbol and order.side == "SELL"
            ),
            None,
        )
        if pending_sell is not None and current_weight > pending_sell.target_weight + 1e-12:
            frozen.append(
                Target(
                    symbol=symbol,
                    weight=pending_sell.target_weight,
                    lifecycle=pending_sell.lifecycle,
                    alpha_score=pending_sell.entry_score,
                    confidence=pending_sell.entry_confidence,
                    reason=pending_sell.reason,
                    reduction_policy=pending_sell.reduction_policy,
                    reason_code=pending_sell.reason_code,
                    exit_kind=pending_sell.exit_kind,
                    entry_industry_strength=pending_sell.entry_industry_strength,
                    event_id=pending_sell.event_id,
                    origin_subsystem=pending_sell.origin_subsystem,
                    mechanism=pending_sell.mechanism,
                    origin_lifecycle=pending_sell.origin_lifecycle,
                    replaces_symbol=pending_sell.replaces_symbol,
                    industry_at_entry=pending_sell.industry_at_entry,
                    industry_manifest_sha256=(
                        pending_sell.industry_manifest_sha256
                    ),
                )
            )
            continue
        strategy_target = proposed_by_symbol.get(symbol)
        if (
            strategy_target is not None
            and strategy_target.weight + 1e-12 < current_weight
            and strategy_target.mechanism
            not in {
                AttributionMechanism.LEADER_ROTATION.value,
                AttributionMechanism.RECOVERY_SUBSTITUTION.value,
            }
        ):
            frozen.append(strategy_target)
            continue
        score = leaders.get(symbol)
        frozen.append(
            Target(
                symbol=symbol,
                weight=current_weight,
                lifecycle=position.lifecycle,
                alpha_score=score.score if score else 0.0,
                confidence=score.confidence if score else 0.0,
                reason="level-1 risk freeze; retain existing exposure",
                reason_code="risk_freeze_hold",
                exit_kind="risk",
                origin_subsystem=OriginSubsystem.RISK.value,
                mechanism=AttributionMechanism.RISK_FREEZE.value,
                origin_lifecycle=position.lifecycle,
            )
        )
    return tuple(frozen)
