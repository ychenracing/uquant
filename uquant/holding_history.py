"""Continuous physical holdings and their current protection evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import AccountState


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


def protected_weights_for_current_episode(account: AccountState) -> dict[str, float]:
    """Ignore stale ordinary rights without consuming them or strategic authority."""
    strategic = (
        set(account.protected_weight_epoch_ids)
        | set(account.strategic_cohort_symbols)
        | set(account.strategic_cohort_targets)
        | {s for s, p in account.positions.items() if p.grant_id or p.epoch_id}
        | {o.symbol for o in account.pending_orders if o.grant_id or o.epoch_id}
    )
    return {
        symbol: weight for symbol, weight in account.protected_weights.items()
        if weight > 0 and (symbol in strategic or holding_spans_date(account, symbol, account.last_shock_date))
    }
