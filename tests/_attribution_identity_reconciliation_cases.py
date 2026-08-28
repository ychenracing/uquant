from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_attribution_identity import (
    _assert_reconcile_rejection_is_byte_atomic,
    _identity,
    _schema_v3_payload,
)

from uquant import types as domain
from uquant.account import load_account, migrate_account, save_account
from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    plan_orders,
    reconcile_account_orders,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def test_schema_v3_identity_migration_is_explicit_deterministic_and_prose_free(tmp_path) -> None:
    first_payload = _schema_v3_payload(reason="first human explanation")
    second_payload = _schema_v3_payload(reason="completely different prose")
    first_source = tmp_path / "first-v3.json"
    second_source = tmp_path / "second-v3.json"
    first_destination = tmp_path / "first-v5.json"
    second_destination = tmp_path / "second-v5.json"
    first_source.write_text(json.dumps(first_payload), encoding="utf-8")
    second_source.write_text(json.dumps(second_payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="explicit migration"):
        load_account(first_source)
    first = migrate_account(
        first_source,
        first_destination,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )
    second = migrate_account(
        second_source,
        second_destination,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )

    assert first.schema_version == second.schema_version == domain.ACCOUNT_SCHEMA_VERSION
    assert first.initial_cash == second.initial_cash == 2_000_000.0
    assert first.cash == second.cash == 1_998_994.9
    assert first.positions["sz300502"].shares == second.positions["sz300502"].shares == 100
    first_order = first.order_ledger[0]
    second_order = second.order_ledger[0]
    assert first_order.event_id == second_order.event_id
    assert first_order.event_id == first.fills[0].event_id
    assert first_order.origin_subsystem == domain.OriginSubsystem.LEADER.value
    assert first_order.mechanism == domain.AttributionMechanism.LEADER_SELECTION.value
    assert first_order.origin_lifecycle == domain.Lifecycle.CORE.value
    assert first_order.industry_at_entry == "optical"
    assert first_order.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256
    migrated_tranche = first.positions["sz300502"].tranches[0]
    assert migrated_tranche.origin_subsystem == domain.OriginSubsystem.LEGACY_MIGRATION.value
    assert migrated_tranche.mechanism == domain.AttributionMechanism.LEGACY_MIGRATION.value
    assert migrated_tranche.origin_lifecycle == domain.Lifecycle.CORE.value
    assert migrated_tranche.industry_at_entry == "optical"
    assert migrated_tranche.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256
    assert load_account(first_destination).to_dict() == first.to_dict()

def test_schema_v3_unknown_buy_code_remains_explicitly_unattributed_and_not_a_leader(
    tmp_path,
) -> None:
    payload = _schema_v3_payload(reason="historical custom BUY prose")
    for item in (payload["order_ledger"][0], payload["fills"][0]):
        item["reason_code"] = "historical_custom_buy_route"
        item["exit_kind"] = "historical_custom_exit"
    source = tmp_path / "unknown-buy-v3.json"
    destination = tmp_path / "unknown-buy-v5.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        source,
        destination,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )

    order = migrated.order_ledger[0]
    assert order.side == domain.Side.BUY.value
    assert order.origin_subsystem == "UNATTRIBUTED_LEGACY"
    assert order.mechanism == "LEGACY_UNCLASSIFIED"
    assert order.origin_subsystem != domain.OriginSubsystem.LEADER.value
    assert order.mechanism != domain.AttributionMechanism.LEADER_SELECTION.value
    assert migrated.fills[0].event_id == order.event_id
    audit = migrated.account_migrations[-1]["legacy_unknown_buy_classification"]
    assert audit == {
        "policy": "pre_v4_unknown_buy_to_unattributed_legacy",
        "events": [
            {
                "event_id": order.event_id,
                "signal_date": "2026-01-05",
                "symbol": "sz300502",
            }
        ],
    }
    assert load_account(destination).to_dict() == migrated.to_dict()

    migrated.order_ledger[0].reason_code = "changed_display_code"
    migrated.fills[0].reason_code = "another_changed_display_code"
    display_mutated = tmp_path / "unknown-buy-display-mutated-v5.json"
    save_account(migrated, display_mutated)
    assert load_account(display_mutated).to_dict() == migrated.to_dict()

    # The audit is provenance, never authorization: removing or editing it
    # cannot upgrade this event into leader attribution.
    migrated.account_migrations[-1].pop("legacy_unknown_buy_classification")
    no_audit = tmp_path / "unknown-buy-no-audit-v5.json"
    save_account(migrated, no_audit)
    reloaded_no_audit = load_account(no_audit)
    assert reloaded_no_audit.to_dict() == migrated.to_dict()
    assert reloaded_no_audit.order_ledger[0].origin_subsystem == "UNATTRIBUTED_LEGACY"

    migrated.account_migrations.append(
        {"editable_claim": "LEADER", "event_id": order.event_id}
    )
    self_signed = tmp_path / "unknown-buy-self-signed-v5.json"
    save_account(migrated, self_signed)
    assert load_account(self_signed).order_ledger[0].origin_subsystem == (
        "UNATTRIBUTED_LEGACY"
    )

    degraded_target = domain.Target(
        symbol=order.symbol,
        weight=order.target_weight,
        lifecycle=order.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason="must not be emitted by production",
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        event_id=order.event_id,
        origin_subsystem=order.origin_subsystem,
        mechanism=order.mechanism,
        origin_lifecycle=order.origin_lifecycle,
        replaces_symbol=order.replaces_symbol,
        industry_at_entry=order.industry_at_entry,
        industry_manifest_sha256=order.industry_manifest_sha256,
    )
    with pytest.raises(RuntimeError, match="unattributed legacy"):
        plan_orders(
            signal_date=order.signal_date,
            targets=(degraded_target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={order.symbol: 10.0},
            cfg=DEFAULT_CONFIG,
        )

def test_reconcile_account_orders_batch_is_atomic_when_a_later_buy_is_invalid() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    valid = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="valid structured BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(),
    )
    invalid = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz002371",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="semantically fabricated RISK BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        reason_code="risk_off",
        exit_kind="risk_off",
        **_identity(
            symbol="sz002371",
            origin_subsystem=domain.OriginSubsystem.RISK.value,
            mechanism=domain.AttributionMechanism.RISK_OFF.value,
            industry_at_entry="pcb",
            reason_code="risk_off",
            exit_kind="risk_off",
        ),
    )
    before = account.to_dict()
    canonical_before = json.dumps(
        before,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(RuntimeError, match="not permitted for BUY"):
        reconcile_account_orders(
            account=account,
            previous=[],
            current=(valid, invalid),
            submitted_date="2026-01-05",
        )

    assert account.to_dict() == before
    assert json.dumps(
        account.to_dict(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") == canonical_before
    assert valid.order_id == invalid.order_id == ""

def test_reconcile_rejects_same_side_duplicate_current_symbol_atomically() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    first = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="first valid BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.05),
    )
    second = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.10,
        reason="second valid BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.10),
    )

    _assert_reconcile_rejection_is_byte_atomic(
        account=account,
        previous=[],
        current=(first, second),
        error="duplicate current symbol sz300502",
    )

def test_reconcile_rejects_opposing_current_sides_for_one_symbol_atomically() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    buy = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="valid BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.05),
    )
    sell = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="valid risk SELL",
        lifecycle=domain.Lifecycle.CORE.value,
        reason_code="risk_off",
        exit_kind="risk_off",
        **_identity(
            target_weight=0.0,
            origin_subsystem=domain.OriginSubsystem.RISK.value,
            mechanism=domain.AttributionMechanism.RISK_OFF.value,
            reason_code="risk_off",
            exit_kind="risk_off",
        ),
    )

    _assert_reconcile_rejection_is_byte_atomic(
        account=account,
        previous=[],
        current=(buy, sell),
        error="duplicate current symbol sz300502",
    )

def test_reconcile_rejects_duplicate_nonidentical_current_order_ids_atomically() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    first = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="first valid BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.05),
    )
    reconcile_account_orders(
        account=account,
        previous=[],
        current=(first,),
        submitted_date="2026-01-05",
    )
    second = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz002371",
        side=domain.Side.BUY.value,
        target_weight=0.10,
        reason="different valid BUY reusing the first ID",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id=first.order_id,
        **_identity(
            symbol="sz002371",
            target_weight=0.10,
            industry_at_entry="pcb",
        ),
    )

    _assert_reconcile_rejection_is_byte_atomic(
        account=account,
        previous=[],
        current=(first, second),
        error=f"duplicate current order_id {first.order_id}",
    )

def test_reconcile_rejects_duplicate_previous_symbol_atomically() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    first = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="first valid prior BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.05),
    )
    second = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.10,
        reason="second valid prior BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.10),
    )

    _assert_reconcile_rejection_is_byte_atomic(
        account=account,
        previous=[first, second],
        current=(),
        error="duplicate previous symbol sz300502",
    )

def test_reconcile_rejects_conflicting_previous_current_order_id_atomically() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    previous = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="valid prior BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        **_identity(target_weight=0.05),
    )
    reconcile_account_orders(
        account=account,
        previous=[],
        current=(previous,),
        submitted_date="2026-01-05",
    )
    conflicting = replace(
        previous,
        target_weight=0.10,
        reason="different intent claiming retained ID",
        **_identity(target_weight=0.10),
    )

    _assert_reconcile_rejection_is_byte_atomic(
        account=account,
        previous=[previous],
        current=(conflicting,),
        error=f"conflicting previous/current order_id {previous.order_id}",
    )
