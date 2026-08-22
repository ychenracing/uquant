"""Task 6 mechanical owner for pending."""

from __future__ import annotations

from ..config import SystemConfig
from ..types import (
    ATTRIBUTION_IDENTITY_FIELDS,
    PendingOrder,
    ReductionPolicy,
    Side,
    Target,
)


def merge_pending_orders(
    *,
    retained: list[PendingOrder],
    planned: tuple[PendingOrder, ...],
    targets: tuple[Target, ...],
    cfg: SystemConfig | None = None,
) -> tuple[PendingOrder, ...]:
    """Keep blocked/partial orders while letting today's target supersede stale intent."""
    target_by_symbol = {target.symbol: target for target in targets}

    def same_attribution(order: PendingOrder, target: Target) -> bool:
        """Require the complete causal identity, including the stable event."""

        return all(getattr(order, field) == getattr(target, field) for field in ATTRIBUTION_IDENTITY_FIELDS)

    def durable_subthreshold_buy(
        order: PendingOrder,
        target: Target | None,
    ) -> bool:
        """Keep a partially filled buy when its economic target is unchanged."""

        return bool(
            cfg is not None
            and target is not None
            and order.side == Side.BUY.value
            and bool(order.order_id)
            and order.remaining_shares > 0
            and target.weight > 1e-12
            and order.lifecycle == target.lifecycle
            and order.reduction_policy == target.reduction_policy
            and same_attribution(order, target)
            and abs(order.target_weight - target.weight) < cfg.min_trade_weight
        )

    merged: dict[str, PendingOrder] = {}
    for order in retained:
        target = target_by_symbol.get(order.symbol)
        if target is None:
            continue
        consistent = (
            (order.side == Side.BUY.value and target.weight > 0) or order.side == Side.SELL.value
        ) and (abs(order.target_weight - target.weight) <= 1e-12)
        same_execution_policy = (
            order.lifecycle == target.lifecycle
            and order.reduction_policy == target.reduction_policy
            and same_attribution(order, target)
        )
        durable_partial_risk_exit = bool(
            cfg is not None
            and order.side == Side.SELL.value
            and order.order_id
            and order.remaining_shares > 0
            and order.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
            and target.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
            and target.weight > 1e-12
            and target.weight < order.target_weight
            and order.target_weight - target.weight < cfg.min_trade_weight
            and same_attribution(order, target)
        )
        if (
            (consistent and same_execution_policy)
            or durable_partial_risk_exit
            or durable_subthreshold_buy(order, target)
        ):
            merged[order.symbol] = order
    for order in planned:
        existing = merged.get(order.symbol)
        if existing is not None:
            target = target_by_symbol.get(order.symbol)
            if durable_subthreshold_buy(existing, target):
                continue
            durable_partial_risk_exit = bool(
                cfg is not None
                and target is not None
                and existing.side == Side.SELL.value
                and existing.order_id
                and existing.remaining_shares > 0
                and existing.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
                and target.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
                and target.weight > 1e-12
                and target.weight < existing.target_weight
                and existing.target_weight - target.weight < cfg.min_trade_weight
                and same_attribution(existing, target)
            )
            if durable_partial_risk_exit:
                continue
        if (
            existing is not None
            and existing.side == order.side
            and abs(existing.target_weight - order.target_weight) <= 1e-12
            and existing.lifecycle == order.lifecycle
            and existing.reduction_policy == order.reduction_policy
            and all(
                getattr(existing, field) == getattr(order, field) for field in ATTRIBUTION_IDENTITY_FIELDS
            )
        ):
            # An unchanged GTC instruction remains one broker order. Display
            # prose/reason codes cannot split a causal event.
            continue
        merged[order.symbol] = order
    return tuple(sorted(merged.values(), key=lambda item: (item.side != Side.SELL.value, item.symbol)))
