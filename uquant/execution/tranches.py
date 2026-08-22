"""Task 6 mechanical owner for tranches."""

from __future__ import annotations

from typing import Any

from ..types import (
    Lifecycle,
    Position,
    ReductionPolicy,
    Tranche,
)

_RISK_LIFECYCLE_PRIORITY = {
    Lifecycle.SATELLITE.value: 0,
    Lifecycle.ADD2.value: 1,
    Lifecycle.ADD1.value: 2,
    Lifecycle.RECOVERY.value: 3,
    Lifecycle.CORE.value: 4,
}


def risk_priority_tranche_key(
    item: Tranche,
) -> tuple[int, float, float, float, str, str, str]:
    """Return the one canonical fragile-first ordering used by every execution path."""
    lifecycle_rank = _RISK_LIFECYCLE_PRIORITY.get(
        item.lifecycle,
        _RISK_LIFECYCLE_PRIORITY[Lifecycle.CORE.value],
    )
    if item.lifecycle == Lifecycle.CORE.value:
        # Within the protected core, retire structurally weaker lots first:
        # deeper adverse excursion, less favorable excursion, and weaker
        # entry evidence all sort ahead of a healthy long-term winner.
        damage_key = (item.mae, item.mfe, item.entry_score)
    else:
        damage_key = (0.0, 0.0, 0.0)
    return (
        lifecycle_rank,
        *damage_key,
        item.sellable_date,
        item.entry_date,
        item.tranche_id,
    )


def _sell_tranches(
    position: Position,
    *,
    date: str,
    reduction_policy: str,
) -> list[Tranche]:
    """Return eligible lots in the economic order requested by the strategy."""
    eligible = [item for item in position.tranches if item.sellable_date <= date]
    if reduction_policy != ReductionPolicy.RISK_PRIORITY.value:
        # Preserve the historical ordinary-exit contract exactly.
        return sorted(eligible, key=lambda item: (item.sellable_date, item.entry_date))

    return sorted(eligible, key=risk_priority_tranche_key)


def _consume_sell_tranches(
    position: Position,
    *,
    shares: int,
    date: str,
    reduction_policy: str,
) -> list[dict[str, Any]]:
    """Consume exact sellable lots and return immutable fill attribution."""
    remaining = shares
    sold_tranches: list[dict[str, Any]] = []
    for tranche in _sell_tranches(
        position,
        date=date,
        reduction_policy=reduction_policy,
    ):
        if remaining <= 0:
            break
        sold = min(tranche.shares, remaining)
        sold_tranches.append(
            {
                "tranche_id": tranche.tranche_id,
                "shares": sold,
                "cost": tranche.avg_cost,
                "unit_cost": tranche.avg_cost,
                "avg_cost": tranche.avg_cost,
                "cost_basis": sold * tranche.avg_cost,
                "lifecycle": tranche.lifecycle,
                "mfe": tranche.mfe,
                "mae": tranche.mae,
                "entry_date": tranche.entry_date,
                "entry_score": tranche.entry_score,
                "entry_confidence": tranche.entry_confidence,
                "entry_regime": tranche.entry_regime,
                "entry_industry_strength": tranche.entry_industry_strength,
                "event_id": tranche.event_id,
                "origin_subsystem": tranche.origin_subsystem,
                "mechanism": tranche.mechanism,
                "origin_lifecycle": tranche.origin_lifecycle,
                "replaces_symbol": tranche.replaces_symbol,
                "industry_at_entry": tranche.industry_at_entry,
                "industry_manifest_sha256": tranche.industry_manifest_sha256,
            }
        )
        tranche.shares -= sold
        remaining -= sold
    if remaining:
        raise RuntimeError("sell fill exceeds eligible T+1 tranche inventory")
    position.tranches = [item for item in position.tranches if item.shares > 0]
    return sold_tranches


def _allocate_sell_costs(
    sold_tranches: list[dict[str, Any]],
    *,
    commission: float,
    stamp_duty: float,
    transfer_fee: float,
    slippage_cost: float,
) -> None:
    """Allocate every fill-level selling cost by each lot's actual shares."""
    total_shares = sum(int(item["shares"]) for item in sold_tranches)
    if total_shares <= 0:
        return
    totals = {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "slippage_cost": slippage_cost,
    }
    allocated = {name: 0.0 for name in totals}
    for index, item in enumerate(sold_tranches):
        is_last = index == len(sold_tranches) - 1
        ratio = int(item["shares"]) / total_shares
        for name, total in totals.items():
            amount = total - allocated[name] if is_last else total * ratio
            item[name] = amount
            allocated[name] += amount
        item["fees"] = sum(float(item[name]) for name in ("commission", "stamp_duty", "transfer_fee"))
        item["transaction_costs"] = float(item["fees"]) + float(item["slippage_cost"])


def _rebuild_position_from_tranches(position: Position) -> None:
    """Recompute aggregate state solely from the economic lots still owned."""
    shares = sum(item.shares for item in position.tranches)
    if shares <= 0:
        position.shares = 0
        return
    position.shares = shares
    position.avg_cost = sum(item.shares * item.avg_cost for item in position.tranches) / shares
    position.entry_date = min(item.entry_date for item in position.tranches)
    position.highest_close = max(item.highest_close for item in position.tranches)
    newest = max(
        position.tranches,
        key=lambda item: (item.entry_date, item.sellable_date, item.tranche_id),
    )
    position.lifecycle = newest.lifecycle
