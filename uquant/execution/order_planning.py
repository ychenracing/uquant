"""Task 6 mechanical owner for order planning."""

from __future__ import annotations

from ..config import SystemConfig
from ..contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe
from ..types import (
    ATTRIBUTION_IDENTITY_FIELDS,
    AccountState,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Side,
    Target,
    derive_attribution_event_id,
    validate_attribution_compatibility,
)


def plan_orders(
    *,
    signal_date: str,
    targets: tuple[Target, ...],
    account: AccountState,
    prices: dict[str, float],
    cfg: SystemConfig,
) -> tuple[PendingOrder, ...]:
    """Translate final target weights into one next-open intent per symbol.

    No fill is simulated here. Small adjustments remain inside the configured
    no-trade band, and sticky strategic holdings are not rebalanced merely due
    to close-price drift.
    """
    market = sum(position.shares * prices.get(symbol, 0.0) for symbol, position in account.positions.items())
    equity = account.cash + market
    planned: list[PendingOrder] = []
    cancel_pending_buy_symbols = {
        order.symbol
        for order in account.order_ledger
        if order.side == Side.BUY.value
        and order.cancel_reason == "sentinel_freeze_new_risk"
        and order.status
        not in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REPLACED.value,
        }
    }
    for target in targets:
        current = account.positions.get(target.symbol)
        if (
            current is not None
            and current.shares > 0
            and target.weight > 0
            and target.reason
            in {
                "mature anchored leader",
                "causal crash-recovery leader",
                "completed post-shock restoration; retain price drift",
                "post-shock restoration; retain winner drift",
            }
        ):
            # Sticky strategic holdings express a hold decision, not a request
            # to rebalance price drift back to yesterday's close weight.
            continue
        current_value = (current.shares if current else 0) * prices.get(target.symbol, 0.0)
        difference = target.weight * equity - current_value
        threshold = max(cfg.min_trade_value, cfg.min_trade_weight * equity)
        # Restoration has a smaller weight band than ordinary rebalancing, but
        # it must still respect the broker's absolute minimum ticket.  Keeping
        # these two thresholds separate previously generated orders that the
        # allocator itself considered economically unexecutable.
        full_recovery_seat = bool(
            target.symbol in account.protected_weights
            and target.weight >= cfg.recovery_target_gross / cfg.max_positions
        )
        restoration_weight_threshold = (
            min(
                cfg.protected_restore_min_trade_weight,
                # A confirmed full-size recovery seat cannot be declared
                # restored while less than 80% funded merely because its
                # absolute portfolio gap sits just inside the no-trade band.
                0.20 * target.weight,
            )
            if full_recovery_seat
            else cfg.restoration_min_trade_weight
        )
        restoration_threshold = max(
            cfg.min_trade_value,
            restoration_weight_threshold * equity,
        )
        restoration_buy_below_completion = bool(
            difference > 0
            and target.symbol
            in {
                *account.protected_weights,
                *account.strategic_restore_weights,
            }
            and equity > 1e-12
            and current_value / equity < 0.95 * target.weight
            and difference >= restoration_threshold
        )
        buy_will_be_planned = bool(
            difference > 0
            and not (
                target.weight != 0 and abs(difference) < threshold and not restoration_buy_below_completion
            )
        )
        if buy_will_be_planned:
            if target.symbol in cancel_pending_buy_symbols:
                continue
            if target.origin_subsystem == OriginSubsystem.UNATTRIBUTED_LEGACY.value:
                raise RuntimeError("unattributed legacy identity cannot originate a production Target BUY")
            if not target.event_id:
                raise RuntimeError(f"new BUY for {target.symbol} requires a canonical event_id")
            try:
                validate_attribution_compatibility(
                    origin_subsystem=target.origin_subsystem,
                    mechanism=target.mechanism,
                    side=Side.BUY.value,
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"new BUY for {target.symbol} has incompatible attribution: {exc}"
                ) from exc
            retained_identity = next(
                (
                    order
                    for order in account.pending_orders
                    if order.side == Side.BUY.value
                    and order.symbol == target.symbol
                    and abs(order.target_weight - target.weight) < cfg.min_trade_weight
                    and order.lifecycle == target.lifecycle
                    and order.reduction_policy == target.reduction_policy
                    and all(
                        getattr(order, field) == getattr(target, field)
                        for field in ATTRIBUTION_IDENTITY_FIELDS
                    )
                ),
                None,
            )
            identity_signal_date = (
                retained_identity.signal_date if retained_identity is not None else signal_date
            )
            industry = default_ai_universe().industry_of(
                target.symbol,
                identity_signal_date,
            )
            if industry == "unknown":
                raise RuntimeError(f"new BUY for {target.symbol} has no point-in-time AI-universe membership")
            if (
                target.industry_at_entry != industry
                or target.industry_manifest_sha256 != REQUIRED_AI_UNIVERSE_SHA256
            ):
                raise RuntimeError(
                    f"new BUY for {target.symbol} has invalid point-in-time industry attribution"
                )
            try:
                expected_event_id = derive_attribution_event_id(
                    signal_date=identity_signal_date,
                    symbol=target.symbol,
                    target_weight=(
                        retained_identity.target_weight if retained_identity is not None else target.weight
                    ),
                    lifecycle=target.lifecycle,
                    origin_lifecycle=target.origin_lifecycle,
                    origin_subsystem=target.origin_subsystem,
                    mechanism=target.mechanism,
                    replaces_symbol=target.replaces_symbol,
                    industry_at_entry=target.industry_at_entry,
                    industry_manifest_sha256=target.industry_manifest_sha256,
                    reduction_policy=target.reduction_policy,
                    reason_code=target.reason_code,
                    exit_kind=target.exit_kind,
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"new BUY for {target.symbol} has malformed attribution identity") from exc
            if target.event_id != expected_event_id:
                raise RuntimeError("new BUY event_id differs from canonical derivation")
            if retained_identity is not None:
                # Count today's still-live intent while carrying the exact
                # canonical order object. Merge retains it without fabricating
                # a new signal date, target weight, event, or broker order.
                planned.append(retained_identity)
                continue
        if target.weight == 0 and current_value > 0:
            difference = -current_value
        elif abs(difference) < threshold and not restoration_buy_below_completion:
            continue
        side = Side.BUY.value if difference > 0 else Side.SELL.value
        try:
            validate_attribution_compatibility(
                origin_subsystem=target.origin_subsystem,
                mechanism=target.mechanism,
                side=side,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"new {side} for {target.symbol} has incompatible attribution: {exc}") from exc
        planned.append(
            PendingOrder(
                signal_date=signal_date,
                symbol=target.symbol,
                side=side,
                target_weight=target.weight,
                reason=target.reason,
                lifecycle=target.lifecycle,
                reduction_policy=target.reduction_policy,
                reason_code=target.reason_code,
                exit_kind=target.exit_kind,
                entry_score=target.alpha_score,
                entry_confidence=target.confidence,
                entry_regime=account.opportunity,
                entry_industry_strength=target.entry_industry_strength,
                event_id=target.event_id,
                origin_subsystem=target.origin_subsystem,
                mechanism=target.mechanism,
                origin_lifecycle=target.origin_lifecycle,
                replaces_symbol=target.replaces_symbol,
                industry_at_entry=target.industry_at_entry,
                industry_manifest_sha256=target.industry_manifest_sha256,
            )
        )
    # Exactly one direction per symbol. Sells execute first at the next open.
    unique: dict[str, PendingOrder] = {}
    for order in planned:
        unique[order.symbol] = order
    return tuple(sorted(unique.values(), key=lambda item: (item.side != Side.SELL.value, item.symbol)))
