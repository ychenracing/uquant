"""Continuous physical holdings and their current protection evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import AccountState, Fill


def holding_spans_date(account: AccountState, symbol: str, boundary: str) -> bool:
    """Prove that today's positive holding never closed after the boundary."""
    position = account.positions.get(symbol)
    if position is None or position.shares <= 0 or not boundary or not position.entry_date:
        return False
    if position.entry_date <= boundary:
        return True
    # FIFO may retire the oldest lot without ever closing the whole holding.
    shares = position.shares
    for fill in reversed(account.fills):
        if fill.symbol != symbol:
            continue
        shares += fill.shares if fill.side == "SELL" else -fill.shares
        if shares == 0:
            return fill.fill_date <= boundary
        if shares < 0:
            return False
    return False


def _recovery_owner_buy(fill: Fill) -> bool:
    return bool(fill.side == "BUY" and fill.origin_subsystem == "RECOVERY"
                and fill.origin_lifecycle == "RECOVERY"
                and fill.mechanism in {"RECOVERY_COHORT", "POST_SHOCK_RESTORATION"})


def _bound_owner_fill(fill: Fill) -> bool:
    return bool(fill.order_id and fill.event_id and not fill.grant_id and not fill.epoch_id)


def recovery_owner_open(account: AccountState, symbol: str, boundary: str | None = None) -> bool:
    """A real cohort owner survives risk sales, but no final strategy exit or new owner."""
    if symbol not in account.anchor_weights or not account.recovery_anchor_date:
        return False
    started, shares = "", 0
    for fill in account.fills:
        if fill.symbol != symbol:
            continue
        bound = _bound_owner_fill(fill)
        owned_buy = bound and _recovery_owner_buy(fill)
        if owned_buy:
            if not started:
                if fill.mechanism != "RECOVERY_COHORT" or fill.signal_date < account.recovery_anchor_date:
                    continue
                started = fill.fill_date
            shares += fill.shares
        elif started and bound and fill.side == "SELL" and fill.origin_subsystem == "RISK":
            shares -= fill.shares
            if shares < 0:
                started, shares = "", 0
        else:
            started, shares = "", 0
    position = account.positions.get(symbol)
    actual = position.shares if position is not None else 0
    return bool(started and shares == actual
                and (boundary is None or bool(boundary and started <= boundary)))


def protected_weights_for_current_episode(account: AccountState) -> dict[str, float]:
    """Keep protection only for strategic, continuous ordinary, or proven recovery owners."""
    strategic = (
        set(account.protected_weight_epoch_ids)
        | set(account.strategic_cohort_symbols)
        | set(account.strategic_cohort_targets)
        | {s for s, p in account.positions.items() if p.grant_id or p.epoch_id}
        | {o.symbol for o in account.pending_orders if o.grant_id or o.epoch_id}
    )
    return {
        symbol: weight for symbol, weight in account.protected_weights.items()
        if weight > 0 and (symbol in strategic or holding_spans_date(account, symbol, account.last_shock_date)
                           or recovery_owner_open(account, symbol, account.last_shock_date))
    }
