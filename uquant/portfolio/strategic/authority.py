"""Single authoritative view of strategic capital ownership backing."""

from __future__ import annotations

from dataclasses import dataclass

from ...models.strategic_epoch import StrategicEpochStatus
from ...models.strategic_grant import TERMINAL_STRATEGIC_GRANT_STATUSES
from ...models.trading import late_strategic_fill_allowed
from ...types import AccountState, OrderStatus


@dataclass(frozen=True, slots=True)
class StrategicCapitalAuthorityAssessment:
    """Economic backing and harmless residue for one durable account."""

    all_cash: bool
    positive_position_symbols: tuple[str, ...]
    pending_execution_symbols: tuple[str, ...]
    unsettled_order_ids: tuple[str, ...]
    late_fill_order_ids: tuple[str, ...]
    active_epoch_ids: tuple[str, ...]
    nonterminal_epoch_ids: tuple[str, ...]
    nonterminal_grant_id: str
    live_authority_fields: tuple[str, ...]
    orphan_residue_fields: tuple[str, ...]

    @property
    def has_live_authority(self) -> bool:
        """Return whether any strategic or execution owner still has backing."""

        return bool(self.live_authority_fields)


def assess_strategic_capital_authority(
    account: AccountState,
) -> StrategicCapitalAuthorityAssessment:
    """Classify durable containers by real position/execution/owner backing."""

    positions, pending_symbols, unsettled_orders, late_fill_orders = (
        _execution_authority_facts(account)
    )
    active_epochs, nonterminal_epochs, nonterminal_grant_id = (
        _ownership_authority_facts(account)
    )
    backing_symbols = _authority_backing_symbols(
        account,
        positions=positions,
        pending_symbols=pending_symbols,
        nonterminal_grant_id=nonterminal_grant_id,
    )
    live_fields = {
        field_name
        for field_name, present in (
            ("positions", bool(positions)),
            ("pending_orders", bool(pending_symbols)),
            ("unsettled_execution", bool(unsettled_orders)),
            ("late_fill_pending", bool(late_fill_orders)),
            (
                "active_strategic_epoch_id",
                bool(active_epochs or account.active_strategic_epoch_id),
            ),
            ("strategic_epochs", bool(nonterminal_epochs)),
            ("strategic_grant", bool(nonterminal_grant_id)),
        )
        if present
    }
    orphan_fields: set[str] = set()
    _classify_weighted_authority(
        account,
        backing_symbols=backing_symbols,
        live_fields=live_fields,
        orphan_fields=orphan_fields,
    )
    _classify_owner_authority(
        account,
        backing_symbols=backing_symbols,
        nonterminal_epochs=nonterminal_epochs,
        live_fields=live_fields,
        orphan_fields=orphan_fields,
    )
    return StrategicCapitalAuthorityAssessment(
        all_cash=not positions,
        positive_position_symbols=positions,
        pending_execution_symbols=pending_symbols,
        unsettled_order_ids=unsettled_orders,
        late_fill_order_ids=late_fill_orders,
        active_epoch_ids=active_epochs,
        nonterminal_epoch_ids=nonterminal_epochs,
        nonterminal_grant_id=nonterminal_grant_id,
        live_authority_fields=tuple(sorted(live_fields)),
        orphan_residue_fields=tuple(sorted(orphan_fields)),
    )


def _execution_authority_facts(
    account: AccountState,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    positions = tuple(
        sorted(symbol for symbol, position in account.positions.items() if position.shares > 0)
    )
    pending_symbols = tuple(sorted({order.symbol for order in account.pending_orders}))
    terminal_orders = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    unsettled_orders = tuple(
        sorted(
            order.order_id or f"unidentified:{index}"
            for index, order in enumerate(account.order_ledger)
            if order.status not in terminal_orders or late_strategic_fill_allowed(order)
        )
    )
    late_fill_orders = tuple(
        sorted(
            order.order_id or f"unidentified:{index}"
            for index, order in enumerate(account.order_ledger)
            if late_strategic_fill_allowed(order)
        )
    )
    return positions, pending_symbols, unsettled_orders, late_fill_orders


def _ownership_authority_facts(
    account: AccountState,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    active_epochs = tuple(
        epoch.epoch_id
        for epoch in account.strategic_epochs
        if epoch.realized_status == StrategicEpochStatus.ACTIVE.value
    )
    nonterminal_epochs = tuple(
        epoch.epoch_id for epoch in account.strategic_epochs if not epoch.terminal
    )
    grant = account.strategic_grant
    nonterminal_grant_id = (
        grant.grant_id
        if grant is not None and grant.status not in TERMINAL_STRATEGIC_GRANT_STATUSES
        else ""
    )
    return active_epochs, nonterminal_epochs, nonterminal_grant_id


def _authority_backing_symbols(
    account: AccountState,
    *,
    positions: tuple[str, ...],
    pending_symbols: tuple[str, ...],
    nonterminal_grant_id: str,
) -> set[str]:
    grant = account.strategic_grant
    terminal_orders = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    backing_symbols = set(positions) | set(pending_symbols)
    backing_symbols.update(
        order.symbol
        for order in account.order_ledger
        if order.status not in terminal_orders or late_strategic_fill_allowed(order)
    )
    if nonterminal_grant_id and grant is not None:
        backing_symbols.add(grant.candidate_symbol)
    backing_symbols.update(
        epoch.owner_symbol for epoch in account.strategic_epochs if not epoch.terminal
    )
    return backing_symbols


def _classify_weighted_authority(
    account: AccountState,
    *,
    backing_symbols: set[str],
    live_fields: set[str],
    orphan_fields: set[str],
) -> None:
    weighted_fields = (
        ("anchor_weights", account.anchor_weights),
        ("protected_weights", account.protected_weights),
        ("strategic_cohort_targets", account.strategic_cohort_targets),
        ("strategic_restore_weights", account.strategic_restore_weights),
    )
    for field_name, weights in weighted_fields:
        if not weights:
            continue
        if set(weights) & backing_symbols:
            live_fields.add(field_name)
        else:
            orphan_fields.add(field_name)
    if account.strategic_cohort_symbols:
        if set(account.strategic_cohort_symbols) & backing_symbols:
            live_fields.add("strategic_cohort_symbols")
        else:
            orphan_fields.add("strategic_cohort_symbols")


def _classify_owner_authority(
    account: AccountState,
    *,
    backing_symbols: set[str],
    nonterminal_epochs: tuple[str, ...],
    live_fields: set[str],
    orphan_fields: set[str],
) -> None:
    for field_name, symbol in (
        ("recovery_conviction_symbol", account.recovery_conviction_symbol),
        ("tactical_anchor_symbol", account.tactical_anchor_symbol),
    ):
        if not symbol:
            continue
        if symbol in backing_symbols:
            live_fields.add(field_name)
        else:
            orphan_fields.add(field_name)
    if account.recovery_owner_epoch_id:
        if account.recovery_owner_epoch_id in nonterminal_epochs:
            live_fields.add("recovery_owner_epoch_id")
        else:
            orphan_fields.add("recovery_owner_epoch_id")
    for field_name, owners in (
        ("protected_weight_epoch_ids", account.protected_weight_epoch_ids),
        ("strategic_restore_epoch_ids", account.strategic_restore_epoch_ids),
    ):
        if not owners:
            continue
        if set(owners.values()) & set(nonterminal_epochs):
            live_fields.add(field_name)
        else:
            orphan_fields.add(field_name)


def normalize_orphan_strategic_capital_residue(
    account: AccountState,
) -> tuple[str, ...]:
    """Release only residue whose recorded owner is provably terminal."""

    assessment = assess_strategic_capital_authority(account)
    if not assessment.all_cash or assessment.has_live_authority:
        return ()
    terminal_epoch_ids = {
        epoch.epoch_id for epoch in account.strategic_epochs if epoch.terminal
    }
    normalized: set[str] = set()
    for ownership_field, weights_field in (
        ("protected_weight_epoch_ids", "protected_weights"),
        ("strategic_restore_epoch_ids", "strategic_restore_weights"),
    ):
        ownership = getattr(account, ownership_field)
        weights = getattr(account, weights_field)
        for symbol, epoch_id in tuple(ownership.items()):
            if epoch_id not in terminal_epoch_ids:
                continue
            ownership.pop(symbol, None)
            weights.pop(symbol, None)
            normalized.update((ownership_field, weights_field))
    if account.recovery_owner_epoch_id in terminal_epoch_ids:
        account.recovery_owner_epoch_id = ""
        account.anchor_weights.clear()
        account.recovery_anchor_date = ""
        account.recovery_conviction_symbol = ""
        account.tactical_anchor_symbol = ""
        normalized.update(
            (
                "anchor_weights",
                "recovery_conviction_symbol",
                "recovery_owner_epoch_id",
                "tactical_anchor_symbol",
            )
        )
    return tuple(sorted(normalized))


__all__ = (
    "StrategicCapitalAuthorityAssessment",
    "assess_strategic_capital_authority",
    "normalize_orphan_strategic_capital_residue",
)
