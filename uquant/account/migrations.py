"""Explicit account schema, attribution, and code-identity migrations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe
from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    OriginSubsystem,
    PendingOrder,
    ReductionPolicy,
    Side,
    derive_attribution_event_id,
)
from .codec import account_from_dict, load_account
from .codec import read_account_payload as _read_account_payload
from .economic_identity import economic_state_sha256
from .store import save_account
from .validation_attribution import (
    derive_v4_attribution_event_id as _derive_v4_attribution_event_id,
)
from .validation_common import (
    HISTORICAL_ATTRIBUTION_SCHEMA_VERSION as _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
)
from .validation_common import (
    LEGACY_INDUSTRY as _LEGACY_INDUSTRY,
)
from .validation_common import (
    LEGACY_MANIFEST_SHA256 as _LEGACY_MANIFEST_SHA256,
)
from .validation_common import (
    unlinked_fill_matches_order as _unlinked_fill_matches_order,
)
from .validation_orders import order_sequence as _order_sequence


def _legacy_attribution_owner(
    reason_code: str,
    exit_kind: str,
    *,
    side: str,
) -> tuple[str, str, bool]:
    """Classify legacy stable codes without inspecting human-readable reason."""

    exact: dict[str, tuple[OriginSubsystem, AttributionMechanism]] = {
        "strategy_target": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_SELECTION,
        ),
        "rotation": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_ROTATION,
        ),
        "lifecycle_exit": (
            OriginSubsystem.LEADER,
            AttributionMechanism.LEADER_LIFECYCLE_EXIT,
        ),
        "challenger_scout": (
            OriginSubsystem.LEADER,
            AttributionMechanism.CHALLENGER_SCOUT,
        ),
        "satellite_expiry": (
            OriginSubsystem.LEADER,
            AttributionMechanism.SATELLITE_EXPIRY,
        ),
        "recovery_cohort": (
            OriginSubsystem.RECOVERY,
            AttributionMechanism.RECOVERY_COHORT,
        ),
        "recovery_exit": (
            OriginSubsystem.RECOVERY,
            AttributionMechanism.TACTICAL_REBOUND,
        ),
        "strategic_cohort": (
            OriginSubsystem.STRATEGIC,
            AttributionMechanism.STRATEGIC_COHORT,
        ),
        "strategic_tail": (
            OriginSubsystem.STRATEGIC,
            AttributionMechanism.STRATEGIC_TRAILING_EXIT,
        ),
        "risk_gross_cap": (
            OriginSubsystem.RISK,
            AttributionMechanism.RISK_GROSS_CAP,
        ),
        "sector_guard": (
            OriginSubsystem.RISK,
            AttributionMechanism.SECTOR_GUARD,
        ),
        "strategic_damage_guard": (
            OriginSubsystem.RISK,
            AttributionMechanism.STRATEGIC_DAMAGE_GUARD,
        ),
        "risk_off": (OriginSubsystem.RISK, AttributionMechanism.RISK_OFF),
        "crisis": (OriginSubsystem.RISK, AttributionMechanism.CRISIS),
        "capital_budget": (
            OriginSubsystem.RISK,
            AttributionMechanism.CAPITAL_BUDGET,
        ),
        "risk_freeze_hold": (
            OriginSubsystem.RISK,
            AttributionMechanism.RISK_FREEZE,
        ),
    }
    selected = exact.get(reason_code)
    if selected is None and exit_kind in exact:
        selected = exact[exit_kind]
    unclassified_buy = selected is None and side == Side.BUY.value
    if unclassified_buy:
        # Preserve uncertainty honestly. This closed degraded category is
        # machine-valid but is never emitted by production Target call sites.
        selected = (
            OriginSubsystem.UNATTRIBUTED_LEGACY,
            AttributionMechanism.LEGACY_UNCLASSIFIED,
        )
    elif selected is None:
        selected = (
            OriginSubsystem.LEGACY_MIGRATION,
            AttributionMechanism.LEGACY_MIGRATION,
        )
    return selected[0].value, selected[1].value, unclassified_buy


def _legacy_industry(symbol: str, entry_date: str) -> tuple[str, str]:
    """Resolve the best deterministic PIT industry available during migration."""

    try:
        industry = default_ai_universe().industry_of(symbol, entry_date)
    except (TypeError, ValueError):
        industry = "unknown"
    if industry == "unknown":
        return _LEGACY_INDUSTRY, _LEGACY_MANIFEST_SHA256
    return industry, REQUIRED_AI_UNIVERSE_SHA256


def _populate_legacy_attribution_stage_1(
    *,
    state: Any,
) -> tuple[Any, Any]:
    unknown_buy_reclassifications: dict[str, dict[str, str]] = {}

    replacements = {
        (str(event.get("signal_date", "")), str(event.get("new_symbol", ""))): str(
            event.get("old_symbol", "")
        )
        for event in state.replacement_events
        if event.get("signal_date") and event.get("new_symbol") and event.get("old_symbol")
    }
    return replacements, unknown_buy_reclassifications


def _populate_legacy_attribution_stage_2() -> Any:
    identity_fields = (
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
    )
    return identity_fields


def _populate_legacy_orders(
    state: AccountState,
    *,
    replacements: dict[tuple[str, str], str],
    unknown_buy_reclassifications: dict[str, dict[str, str]],
) -> dict[str, AccountOrder]:
    def populate_order(order: PendingOrder | AccountOrder) -> None:
        origin, mechanism, unclassified_buy = _legacy_attribution_owner(
            order.reason_code,
            order.exit_kind,
            side=order.side,
        )
        industry, manifest = _legacy_industry(order.symbol, order.signal_date)
        replaces_symbol = replacements.get((order.signal_date, order.symbol))
        order.origin_subsystem = origin
        order.mechanism = mechanism
        order.origin_lifecycle = order.lifecycle
        order.replaces_symbol = replaces_symbol
        order.industry_at_entry = industry
        order.industry_manifest_sha256 = manifest
        order.event_id = derive_attribution_event_id(
            signal_date=order.signal_date,
            symbol=order.symbol,
            target_weight=order.target_weight,
            lifecycle=order.lifecycle,
            origin_lifecycle=order.origin_lifecycle,
            origin_subsystem=order.origin_subsystem,
            mechanism=order.mechanism,
            replaces_symbol=order.replaces_symbol,
            industry_at_entry=order.industry_at_entry,
            industry_manifest_sha256=order.industry_manifest_sha256,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )
        if unclassified_buy:
            unknown_buy_reclassifications.setdefault(
                order.event_id,
                {
                    "event_id": order.event_id,
                    "signal_date": order.signal_date,
                    "symbol": order.symbol,
                },
            )

    for ledger_order in state.order_ledger:
        populate_order(ledger_order)
    ledger = {order.order_id: order for order in state.order_ledger}
    for pending_order in state.pending_orders:
        linked = ledger.get(pending_order.order_id) if pending_order.order_id else None
        if linked is None:
            populate_order(pending_order)
            continue
        for field in (
            "event_id",
            "origin_subsystem",
            "mechanism",
            "origin_lifecycle",
            "replaces_symbol",
            "industry_at_entry",
            "industry_manifest_sha256",
        ):
            setattr(pending_order, field, getattr(linked, field))
    return ledger


def _populate_legacy_positions(state: AccountState) -> None:
    for symbol, position in state.positions.items():
        for tranche in position.tranches:
            industry, manifest = _legacy_industry(symbol, tranche.entry_date)
            tranche.origin_subsystem = OriginSubsystem.LEGACY_MIGRATION.value
            tranche.mechanism = AttributionMechanism.LEGACY_MIGRATION.value
            tranche.origin_lifecycle = tranche.lifecycle
            tranche.replaces_symbol = None
            tranche.industry_at_entry = industry
            tranche.industry_manifest_sha256 = manifest
            tranche.event_id = derive_attribution_event_id(
                signal_date=tranche.entry_date,
                symbol=symbol,
                target_weight=0.0,
                lifecycle=tranche.lifecycle,
                origin_lifecycle=tranche.origin_lifecycle,
                origin_subsystem=tranche.origin_subsystem,
                mechanism=tranche.mechanism,
                replaces_symbol=None,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
                reduction_policy=ReductionPolicy.FIFO.value,
                reason_code=f"legacy_tranche:{tranche.tranche_id}",
                exit_kind="legacy_migration",
            )


def _populate_legacy_fill(
    fill: Any,
    *,
    fill_index: int,
    identity_fields: tuple[str, ...],
    ledger: dict[str, AccountOrder],
    replacements: dict[tuple[str, str], str],
    state: AccountState,
    unknown_buy_reclassifications: dict[str, dict[str, str]],
) -> None:
    linked = ledger.get(fill.order_id) if fill.order_id else None
    if not fill.order_id:
        candidates = [
            order for order in state.order_ledger if _unlinked_fill_matches_order(fill, order, native=False)
        ]
        if len(candidates) > 1:
            raise RuntimeError("legacy unlinked fill has ambiguous structured order identity")
        linked = candidates[0] if candidates else None
    if linked is not None:
        for field in identity_fields:
            setattr(fill, field, getattr(linked, field))
    else:
        origin, mechanism, unclassified_buy = _legacy_attribution_owner(
            fill.reason_code,
            fill.exit_kind,
            side=fill.side,
        )
        industry, manifest = _legacy_industry(fill.symbol, fill.signal_date)
        fill.origin_subsystem = origin
        fill.mechanism = mechanism
        fill.origin_lifecycle = fill.lifecycle
        fill.replaces_symbol = replacements.get((fill.signal_date, fill.symbol))
        fill.industry_at_entry = industry
        fill.industry_manifest_sha256 = manifest
        fill.event_id = derive_attribution_event_id(
            signal_date=fill.signal_date,
            symbol=fill.symbol,
            target_weight=0.0,
            lifecycle=fill.lifecycle,
            origin_lifecycle=fill.origin_lifecycle,
            origin_subsystem=fill.origin_subsystem,
            mechanism=fill.mechanism,
            replaces_symbol=fill.replaces_symbol,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
            reduction_policy=fill.reduction_policy,
            reason_code=f"{fill.reason_code}:legacy_fill:{fill.fill_id or fill_index}",
            exit_kind=fill.exit_kind,
        )
        if unclassified_buy:
            unknown_buy_reclassifications.setdefault(
                fill.event_id,
                {
                    "event_id": fill.event_id,
                    "signal_date": fill.signal_date,
                    "symbol": fill.symbol,
                },
            )
    for allocation_index, allocation in enumerate(fill.sold_tranches, start=1):
        entry_date = str(allocation.get("entry_date") or fill.fill_date)
        lifecycle = str(allocation.get("lifecycle") or fill.lifecycle)
        industry, manifest = _legacy_industry(fill.symbol, entry_date)
        allocation.update(
            origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
            mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
            origin_lifecycle=lifecycle,
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
        )
        allocation["event_id"] = derive_attribution_event_id(
            signal_date=entry_date,
            symbol=fill.symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            origin_lifecycle=lifecycle,
            origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
            mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
            reduction_policy=ReductionPolicy.FIFO.value,
            reason_code=("legacy_sold_tranche:" + str(allocation.get("tranche_id") or allocation_index)),
            exit_kind="legacy_migration",
        )


def _populate_legacy_attribution(state: AccountState) -> list[dict[str, str]]:
    """Populate v1-v3 identity from stable structured fields only."""

    replacements, unknown_buy_reclassifications = _populate_legacy_attribution_stage_1(
        state=state,
    )

    ledger = _populate_legacy_orders(
        state,
        replacements=replacements,
        unknown_buy_reclassifications=unknown_buy_reclassifications,
    )
    _populate_legacy_positions(state)
    identity_fields = _populate_legacy_attribution_stage_2()
    for fill_index, fill in enumerate(state.fills, start=1):
        _populate_legacy_fill(
            fill,
            fill_index=fill_index,
            identity_fields=identity_fields,
            ledger=ledger,
            replacements=replacements,
            state=state,
            unknown_buy_reclassifications=unknown_buy_reclassifications,
        )
    return [unknown_buy_reclassifications[event_id] for event_id in sorted(unknown_buy_reclassifications)]


def _migrate_v4_attribution_event_ids_stage_1() -> tuple[
    list[tuple[dict[str, Any], str]],
    dict[str, str],
    list[tuple[Any, str]],
    Callable[[str, str], None],
]:
    event_id_map: dict[str, str] = {}
    reverse_event_id_map: dict[str, str] = {}
    object_assignments: list[tuple[Any, str]] = []
    allocation_assignments: list[tuple[dict[str, Any], str]] = []

    def record_mapping(old_event_id: str, new_event_id: str) -> None:
        existing = event_id_map.get(old_event_id)
        if existing is not None and existing != new_event_id:
            raise RuntimeError("v4 event_id maps to conflicting machine identities")
        reverse_existing = reverse_event_id_map.get(new_event_id)
        if reverse_existing is not None and reverse_existing != old_event_id:
            raise RuntimeError("v4 event_id migration has a reverse-map collision")
        event_id_map[old_event_id] = new_event_id
        reverse_event_id_map[new_event_id] = old_event_id

    return allocation_assignments, event_id_map, object_assignments, record_mapping


def _migrate_v4_attribution_event_ids_stage_2(
    *,
    state: AccountState,
) -> tuple[Callable[..., str], list[AccountOrder | PendingOrder]]:
    def current_event_id(
        item: Any,
        *,
        signal_date: str,
        symbol: str,
        target_weight: float,
        lifecycle: str,
        reduction_policy: str,
        reason_code: str,
        exit_kind: str,
    ) -> str:
        return derive_attribution_event_id(
            signal_date=signal_date,
            symbol=symbol,
            target_weight=target_weight,
            lifecycle=lifecycle,
            origin_lifecycle=item.origin_lifecycle,
            origin_subsystem=item.origin_subsystem,
            mechanism=item.mechanism,
            replaces_symbol=item.replaces_symbol,
            industry_at_entry=item.industry_at_entry,
            industry_manifest_sha256=item.industry_manifest_sha256,
            reduction_policy=reduction_policy,
            reason_code=reason_code,
            exit_kind=exit_kind,
        )

    durable_orders: list[AccountOrder | PendingOrder] = [
        *state.order_ledger,
        *state.pending_orders,
    ]
    return current_event_id, durable_orders


def _migrate_detached_v4_lot(
    lot: Any,
    *,
    current_event_id: Callable[..., str],
    record_mapping: Callable[[str, str], None],
    signal_date: str,
    symbol: str,
    lifecycle: str,
    reason_code: str,
    exit_kind: str,
    label: str,
) -> str:
    old_event_id = lot.event_id
    expected_old = _derive_v4_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=0.0,
        lifecycle=lifecycle,
        origin_lifecycle=lot.origin_lifecycle,
        origin_subsystem=lot.origin_subsystem,
        mechanism=lot.mechanism,
        replaces_symbol=lot.replaces_symbol,
        industry_at_entry=lot.industry_at_entry,
        industry_manifest_sha256=lot.industry_manifest_sha256,
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    if old_event_id != expected_old:
        raise RuntimeError(f"{label} v4 event_id differs from canonical derivation")
    new_event_id = current_event_id(
        lot,
        signal_date=signal_date,
        symbol=symbol,
        target_weight=0.0,
        lifecycle=lifecycle,
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    record_mapping(old_event_id, new_event_id)
    return new_event_id


def _migrate_v4_attribution_event_ids(state: AccountState) -> dict[str, Any]:
    """Map validated schema-v4 events to the machine-only schema-v5 format."""

    allocation_assignments, event_id_map, object_assignments, record_mapping = (
        _migrate_v4_attribution_event_ids_stage_1()
    )

    current_event_id, durable_orders = _migrate_v4_attribution_event_ids_stage_2(
        state=state,
    )
    for order in durable_orders:
        old_event_id = order.event_id
        new_event_id = current_event_id(
            order,
            signal_date=order.signal_date,
            symbol=order.symbol,
            target_weight=order.target_weight,
            lifecycle=order.lifecycle,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )
        record_mapping(old_event_id, new_event_id)
        object_assignments.append((order, new_event_id))

    for fill in state.fills:
        old_event_id = fill.event_id
        mapped_fill_event_id = event_id_map.get(old_event_id)
        if mapped_fill_event_id is None:
            raise RuntimeError("v4 fill event_id lacks a validated originating order")
        object_assignments.append((fill, mapped_fill_event_id))

    for symbol, position in state.positions.items():
        for tranche in position.tranches:
            mapped_event_id = event_id_map.get(tranche.event_id)
            if mapped_event_id is not None:
                object_assignments.append((tranche, mapped_event_id))
                continue
            if (
                tranche.origin_subsystem != OriginSubsystem.LEGACY_MIGRATION.value
                or tranche.mechanism != AttributionMechanism.LEGACY_MIGRATION.value
            ):
                raise RuntimeError("v4 tranche event_id lacks a validated originating BUY")
            migrated_event_id = _migrate_detached_v4_lot(
                tranche,
                current_event_id=current_event_id,
                record_mapping=record_mapping,
                signal_date=tranche.entry_date,
                symbol=symbol,
                lifecycle=tranche.lifecycle,
                reason_code=f"legacy_tranche:{tranche.tranche_id}",
                exit_kind="legacy_migration",
                label="account tranche",
            )
            object_assignments.append((tranche, migrated_event_id))

    for fill in state.fills:
        for allocation_index, allocation in enumerate(fill.sold_tranches, start=1):
            old_event_id = str(allocation["event_id"])
            mapped_event_id = event_id_map.get(old_event_id)
            if mapped_event_id is not None:
                allocation_assignments.append((allocation, mapped_event_id))
                continue
            lot = SimpleNamespace(**allocation)
            if (
                lot.origin_subsystem == OriginSubsystem.LEGACY_MIGRATION.value
                and lot.mechanism == AttributionMechanism.LEGACY_MIGRATION.value
            ):
                reason_code = "legacy_sold_tranche:" + str(allocation.get("tranche_id") or allocation_index)
                exit_kind = "legacy_migration"
            elif (
                lot.origin_subsystem == OriginSubsystem.BROKER_RECONCILIATION.value
                and lot.mechanism == AttributionMechanism.BROKER_RECONCILIATION.value
                and allocation.get("degraded") is True
            ):
                reason_code = f"broker_reconciliation:degraded-sale:{fill.fill_id}"
                exit_kind = "broker_reconciliation"
            else:
                raise RuntimeError("v4 sold lot event_id lacks a validated originating BUY")
            migrated_event_id = _migrate_detached_v4_lot(
                lot,
                current_event_id=current_event_id,
                record_mapping=record_mapping,
                signal_date=str(allocation["entry_date"]),
                symbol=fill.symbol,
                lifecycle=str(allocation["lifecycle"]),
                reason_code=reason_code,
                exit_kind=exit_kind,
                label="fill sold lot",
            )
            allocation_assignments.append((allocation, migrated_event_id))

    # Apply only after every old identity, chain, and bidirectional mapping has
    # been validated. A collision therefore cannot leave even the in-memory
    # migration candidate partially resealed.
    for item, event_id in object_assignments:
        item.event_id = event_id
    for allocation, event_id in allocation_assignments:
        allocation["event_id"] = event_id

    return {
        "policy": "validated_v4_to_v5_machine_identity",
        "event_id_map": [
            {
                "from_event_id": old_event_id,
                "to_event_id": event_id_map[old_event_id],
            }
            for old_event_id in sorted(event_id_map)
        ],
    }


legacy_attribution_owner = _legacy_attribution_owner
legacy_industry = _legacy_industry
migrate_v4_attribution_event_ids = _migrate_v4_attribution_event_ids
populate_legacy_attribution = _populate_legacy_attribution


def migrate_code_identity(
    source: str | Path,
    destination: str | Path,
    *,
    new_code_hash: str,
    acknowledge_code_change: bool,
) -> AccountState:
    """Rebind a current account to reviewed code without economic mutation."""

    if not acknowledge_code_change:
        raise RuntimeError("account migration requires --acknowledge-code-change")
    if not isinstance(new_code_hash, str) or not new_code_hash.strip():
        raise RuntimeError("account migration requires a non-empty code hash")
    state = load_account(source)
    if state.code_hash == new_code_hash:
        raise RuntimeError("account already uses the requested code hash")
    before = economic_state_sha256(state)
    previous_code_hash = state.code_hash
    state.code_hash = new_code_hash
    after = economic_state_sha256(state)
    if after != before:
        raise RuntimeError("code identity migration changed economic state")
    state.account_migrations.append(
        {
            "migration_type": "code_identity_only",
            "migrated_at_utc": datetime.now(UTC).isoformat(),
            "from_schema": state.schema_version,
            "to_schema": state.schema_version,
            "from_code_hash": previous_code_hash,
            "to_code_hash": new_code_hash,
            "economic_state_sha256_before": before,
            "economic_state_sha256_after": after,
        }
    )
    save_account(state, destination)
    persisted = load_account(destination)
    if economic_state_sha256(persisted) != before:
        raise RuntimeError("persisted code identity migration changed economic state")
    return persisted


def migrate_account(
    source: str | Path,
    destination: str | Path,
    *,
    new_code_hash: str,
    acknowledge_code_change: bool,
) -> AccountState:
    """Normalize one durable account and bind it to reviewed production code.

    The caller explicitly acknowledges the target code fingerprint. Market-data
    provenance, broker state, orders, fills, and strategy state remain intact.
    """
    if not acknowledge_code_change:
        raise RuntimeError("account migration requires --acknowledge-code-change")
    if not new_code_hash:
        raise RuntimeError("account migration requires a non-empty code hash")
    source_payload = _read_account_payload(source)
    source_sequence_was_explicit = "next_order_sequence" in source_payload
    state = account_from_dict(
        source_payload,
        allow_legacy_schema=True,
    )
    previous_schema = state.schema_version
    previous_code_hash = state.code_hash
    previous_next_order_sequence = state.next_order_sequence
    degraded_sell_attributions: list[dict[str, Any]] = []
    if previous_schema == 2:
        for index, fill in enumerate(state.fills, start=1):
            if fill.side != Side.SELL.value or not fill.order_id or fill.sold_tranches:
                continue
            attribution_id = fill.fill_id or (f"{fill.order_id}:{fill.fill_date}:{index}")
            fill.sold_tranches = [
                {
                    "tranche_id": f"legacy-v2-unattributed:{attribution_id}",
                    "lifecycle": fill.lifecycle,
                    "shares": fill.shares,
                    "attribution_quality": "degraded_schema_v2_missing_sold_tranches",
                    "source_schema": 2,
                }
            ]
            degraded_sell_attributions.append(
                {
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "symbol": fill.symbol,
                    "fill_date": fill.fill_date,
                    "shares": fill.shares,
                }
            )
    attribution_event_id_migration: dict[str, Any] | None = None
    legacy_unknown_buy_classifications: list[dict[str, str]] = []
    if previous_schema < _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
        legacy_unknown_buy_classifications = _populate_legacy_attribution(state)
    elif previous_schema == _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
        attribution_event_id_migration = _migrate_v4_attribution_event_ids(state)
    order_sequence_migration: dict[str, Any] | None = None
    if previous_schema < ACCOUNT_SCHEMA_VERSION:
        exact_next_order_sequence = (
            max(
                (_order_sequence(order.order_id) for order in state.order_ledger),
                default=0,
            )
            + 1
        )
        state.next_order_sequence = exact_next_order_sequence
        order_sequence_migration = {
            "policy": "legacy_nonreuse_to_v5_exact_ledger_max_plus_one",
            "source_was_explicit": source_sequence_was_explicit,
            "old_next_order_sequence": previous_next_order_sequence,
            "new_next_order_sequence": exact_next_order_sequence,
            "reason": "v5_requires_exact_max_durable_order_id_plus_one",
        }
    state.schema_version = ACCOUNT_SCHEMA_VERSION
    state.code_hash = new_code_hash
    migration_event: dict[str, Any] = {
        "migrated_at_utc": datetime.now(UTC).isoformat(),
        "from_schema": previous_schema,
        "to_schema": ACCOUNT_SCHEMA_VERSION,
        "from_code_hash": previous_code_hash,
        "to_code_hash": new_code_hash,
    }
    if degraded_sell_attributions:
        migration_event["degraded_sell_attribution"] = {
            "policy": "synthetic_single_lot_exact_share_backfill",
            "fills": degraded_sell_attributions,
        }
    if attribution_event_id_migration is not None:
        migration_event["attribution_event_id_migration"] = attribution_event_id_migration
    if legacy_unknown_buy_classifications:
        migration_event["legacy_unknown_buy_classification"] = {
            "policy": "pre_v4_unknown_buy_to_unattributed_legacy",
            "events": legacy_unknown_buy_classifications,
        }
    if order_sequence_migration is not None:
        migration_event["order_sequence_migration"] = order_sequence_migration
    state.account_migrations.append(migration_event)
    save_account(state, destination)
    return state
