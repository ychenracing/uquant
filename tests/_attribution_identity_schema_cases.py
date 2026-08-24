from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from test_attribution_identity import (
    _schema_v3_payload,
)

from uquant import account as account_module
from uquant import types as domain
from uquant.account import load_account, migrate_account
from uquant.account import migrations as account_migrations_module
from uquant.engine import ProductionEngine
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def test_pre_fix_v4_identity_requires_validated_deterministic_v5_migration(
    tmp_path,
) -> None:
    # Locked outputs from the exact machine-only attribution-event.v1 payload
    # written by commit 5394676a. Display reason_code/exit_kind are absent.
    old_buy_event = "evt_b34477642569eac0e8346e872bc0310b341e1965e49f0c025264cc86b8ed5d49"
    old_sell_event = "evt_6c68d0daa29258ed943f14ba20b83e9c592188fa6d3b13cf3ff77c2755fbb572"
    old_pending_event = "evt_e0bb64ddecefb66f463bf6143c37b00d5901415cc54eea011d97ff3aa24b8f00"
    common_identity = {
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    buy_identity = {
        **common_identity,
        "event_id": old_buy_event,
        "origin_subsystem": domain.OriginSubsystem.LEADER.value,
        "mechanism": domain.AttributionMechanism.LEADER_SELECTION.value,
    }
    sell_identity = {
        **common_identity,
        "event_id": old_sell_event,
        "origin_subsystem": domain.OriginSubsystem.RISK.value,
        "mechanism": domain.AttributionMechanism.RISK_OFF.value,
    }
    pending_identity = {
        **common_identity,
        "event_id": old_pending_event,
        "origin_subsystem": domain.OriginSubsystem.LEADER.value,
        "mechanism": domain.AttributionMechanism.LEADER_SELECTION.value,
    }
    buy_order = domain.AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="historical v4 BUY prose",
        lifecycle=domain.Lifecycle.CORE.value,
        status=domain.OrderStatus.FILLED.value,
        requested_shares=200,
        filled_shares=200,
        remaining_shares=0,
        last_update_date="2026-01-06",
        reason_code="strategy_target",
        exit_kind="strategy",
        **buy_identity,
    )
    sell_order = domain.AccountOrder(
        order_id="O000000002",
        signal_date="2026-01-06",
        submitted_date="2026-01-06",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="historical v4 SELL prose",
        lifecycle=domain.Lifecycle.CORE.value,
        status=domain.OrderStatus.FILLED.value,
        requested_shares=100,
        filled_shares=100,
        remaining_shares=0,
        last_update_date="2026-01-07",
        reason_code="risk_off",
        exit_kind="risk_off",
        **sell_identity,
    )
    pending = domain.PendingOrder(
        signal_date="2026-01-07",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.10,
        reason="historical v4 pending prose",
        lifecycle=domain.Lifecycle.CORE.value,
        remaining_shares=100,
        order_id="O000000003",
        **pending_identity,
    )
    pending_order = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.OPEN.value,
        requested_shares=100,
        remaining_shares=100,
        **pending_identity,
    )
    buy_fill = domain.Fill(
        signal_date=buy_order.signal_date,
        fill_date="2026-01-06",
        symbol=buy_order.symbol,
        side=buy_order.side,
        shares=200,
        price=10.0,
        gross_value=2_000.0,
        commission=5.0,
        stamp_duty=0.0,
        transfer_fee=0.2,
        slippage_cost=0.4,
        reason="different historical BUY fill prose",
        lifecycle=buy_order.lifecycle,
        order_id=buy_order.order_id,
        reason_code=buy_order.reason_code,
        exit_kind=buy_order.exit_kind,
        **buy_identity,
    )
    sell_fill = domain.Fill(
        signal_date=sell_order.signal_date,
        fill_date="2026-01-07",
        symbol=sell_order.symbol,
        side=sell_order.side,
        shares=100,
        price=11.0,
        gross_value=1_100.0,
        commission=5.0,
        stamp_duty=1.1,
        transfer_fee=0.11,
        slippage_cost=0.2,
        reason="different historical SELL fill prose",
        lifecycle=sell_order.lifecycle,
        order_id=sell_order.order_id,
        reason_code=sell_order.reason_code,
        exit_kind=sell_order.exit_kind,
        sold_tranches=[
            {
                "tranche_id": "T000000001-sold",
                "lifecycle": domain.Lifecycle.CORE.value,
                "shares": 100,
                "entry_date": "2026-01-06",
                **buy_identity,
            }
        ],
        **sell_identity,
    )
    live_tranche = domain.Tranche(
        tranche_id="T000000001-live",
        lifecycle=domain.Lifecycle.CORE.value,
        shares=100,
        avg_cost=10.026,
        entry_date="2026-01-06",
        sellable_date="2026-01-07",
        highest_close=11.0,
        lowest_close=10.0,
        **buy_identity,
    )
    old_state = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=1_999_089.39,
        schema_version=4,
        positions={
            "sz300502": domain.Position(
                symbol="sz300502",
                shares=100,
                avg_cost=10.026,
                entry_date="2026-01-06",
                highest_close=11.0,
                lifecycle=domain.Lifecycle.CORE.value,
                tranches=[live_tranche],
            )
        },
        pending_orders=[pending],
        order_ledger=[buy_order, sell_order, pending_order],
        next_order_sequence=4,
        fills=[buy_fill, sell_fill],
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
        data_hash="data",
        code_hash="v4-code",
    )
    payload = old_state.to_dict()
    source = tmp_path / "valid-v4.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    tampered_payload = json.loads(json.dumps(payload))
    tampered_payload["order_ledger"][0]["event_id"] = "evt_" + "f" * 64
    tampered_source = tmp_path / "tampered-v4.json"
    tampered_source.write_text(json.dumps(tampered_payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="v4 event_id differs from canonical derivation"):
        migrate_account(
            tampered_source,
            tmp_path / "tampered-v5.json",
            new_code_hash="v5-code",
            acknowledge_code_change=True,
        )

    with pytest.raises(RuntimeError, match="explicit migration"):
        load_account(source)
    first = migrate_account(
        source,
        tmp_path / "first-v5.json",
        new_code_hash="v5-code",
        acknowledge_code_change=True,
    )
    second = migrate_account(
        source,
        tmp_path / "second-v5.json",
        new_code_hash="v5-code",
        acknowledge_code_change=True,
    )

    assert domain.ACCOUNT_SCHEMA_VERSION == first.schema_version == second.schema_version == 5
    assert first.cash == second.cash == 1_999_089.39
    assert first.positions["sz300502"].shares == second.positions["sz300502"].shares == 100
    assert first.order_ledger[0].filled_shares == second.order_ledger[0].filled_shares == 200
    assert first.fills[0].gross_value == second.fills[0].gross_value == 2_000.0
    assert first.order_ledger[0].event_id == first.fills[0].event_id
    assert first.order_ledger[0].event_id == first.positions["sz300502"].tranches[0].event_id
    assert first.order_ledger[0].event_id == first.fills[1].sold_tranches[0]["event_id"]
    assert first.pending_orders[0].event_id == first.order_ledger[2].event_id
    assert first.order_ledger[0].event_id != old_buy_event
    assert first.order_ledger[0].event_id == second.order_ledger[0].event_id
    first_provenance = first.account_migrations[-1]["attribution_event_id_migration"]
    second_provenance = second.account_migrations[-1]["attribution_event_id_migration"]
    assert first_provenance == second_provenance
    assert first_provenance["policy"] == "validated_v4_to_v5_machine_identity"
    migrated_ids = {
        item["from_event_id"]: item["to_event_id"]
        for item in first_provenance["event_id_map"]
    }
    assert migrated_ids == {
        old_buy_event: first.order_ledger[0].event_id,
        old_sell_event: first.order_ledger[1].event_id,
        old_pending_event: first.order_ledger[2].event_id,
    }
    assert "reason" not in first_provenance
    assert load_account(tmp_path / "first-v5.json").to_dict() == first.to_dict()

def test_real_v4_and_v5_event_mapping_cannot_split_on_display_fields() -> None:
    machine = {
        "signal_date": "2026-01-05",
        "symbol": "sz300502",
        "target_weight": 0.05,
        "lifecycle": domain.Lifecycle.CORE.value,
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "origin_subsystem": domain.OriginSubsystem.LEADER.value,
        "mechanism": domain.AttributionMechanism.LEADER_SELECTION.value,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
        "reduction_policy": domain.ReductionPolicy.FIFO.value,
    }
    old_first = account_module._derive_v4_attribution_event_id(
        **machine,
        reason_code="first_display_code",
        exit_kind="first_display_exit",
    )
    old_second = account_module._derive_v4_attribution_event_id(
        **machine,
        reason_code="mutated_display_code",
        exit_kind="mutated_display_exit",
    )
    new_first = domain.derive_attribution_event_id(
        **machine,
        reason_code="first_display_code",
        exit_kind="first_display_exit",
    )
    new_second = domain.derive_attribution_event_id(
        **machine,
        reason_code="mutated_display_code",
        exit_kind="mutated_display_exit",
    )

    assert old_first == old_second == (
        "evt_b34477642569eac0e8346e872bc0310b341e1965e49f0c025264cc86b8ed5d49"
    )
    assert new_first == new_second
    assert old_first != new_first

def test_v4_to_v5_rejects_reverse_event_id_collision_before_writing(
    tmp_path,
    monkeypatch,
) -> None:
    common = {
        "symbol": "sz300502",
        "side": domain.Side.BUY.value,
        "reason": "historical display prose",
        "lifecycle": domain.Lifecycle.CORE.value,
        "status": domain.OrderStatus.OPEN.value,
        "requested_shares": 100,
        "remaining_shares": 100,
        "reason_code": "strategy_target",
        "exit_kind": "strategy",
        "origin_subsystem": domain.OriginSubsystem.LEADER.value,
        "mechanism": domain.AttributionMechanism.LEADER_SELECTION.value,
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    orders = [
        domain.AccountOrder(
            order_id="O000000001",
            signal_date="2026-01-05",
            submitted_date="2026-01-05",
            target_weight=0.05,
            event_id="evt_b34477642569eac0e8346e872bc0310b341e1965e49f0c025264cc86b8ed5d49",
            **common,
        ),
        domain.AccountOrder(
            order_id="O000000002",
            signal_date="2026-01-07",
            submitted_date="2026-01-07",
            target_weight=0.10,
            event_id="evt_e0bb64ddecefb66f463bf6143c37b00d5901415cc54eea011d97ff3aa24b8f00",
            **common,
        ),
    ]
    state = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        schema_version=4,
        order_ledger=orders,
        next_order_sequence=3,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
        data_hash="data",
        code_hash="v4-code",
    )
    source = tmp_path / "two-valid-v4-events.json"
    destination = tmp_path / "must-not-exist-v5.json"
    source.write_text(json.dumps(state.to_dict()), encoding="utf-8")
    source_bytes = source.read_bytes()

    # Fault-inject a collision in the current derivation. Historical v4 hashes
    # are locked literals above, so this exercises only the migration reverse
    # mapping invariant and cannot turn an invalid v4 object into a fixture.
    monkeypatch.setattr(
        account_migrations_module,
        "derive_attribution_event_id",
        lambda **_kwargs: "evt_" + "a" * 64,
    )

    with pytest.raises(RuntimeError, match="collision"):
        migrate_account(
            source,
            destination,
            new_code_hash="v5-code",
            acknowledge_code_change=True,
        )

    assert source.read_bytes() == source_bytes
    assert not destination.exists()

def test_schema_v3_unlinked_fill_migration_uses_structured_identity_not_prose(
    tmp_path,
) -> None:
    payload = _schema_v3_payload(reason="ledger prose")
    payload["fills"][0]["order_id"] = ""
    payload["fills"][0]["reason"] = "unrelated fill prose"
    source = tmp_path / "unlinked-v3.json"
    destination = tmp_path / "unlinked-v5.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        source,
        destination,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )

    assert migrated.fills[0].order_id == ""
    assert migrated.fills[0].event_id == migrated.order_ledger[0].event_id

def test_schema_v3_unlinked_fill_migration_fails_closed_on_structured_ambiguity(
    tmp_path,
) -> None:
    payload = _schema_v3_payload(reason="same prose")
    payload["fills"][0]["order_id"] = ""
    duplicate = dict(payload["order_ledger"][0])
    duplicate["order_id"] = "O000000002"
    payload["order_ledger"].append(duplicate)
    payload["next_order_sequence"] = 3
    source = tmp_path / "ambiguous-unlinked-v3.json"
    destination = tmp_path / "ambiguous-unlinked-v5.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="ambiguous"):
        migrate_account(
            source,
            destination,
            new_code_hash="new-code",
            acknowledge_code_change=True,
        )

@pytest.mark.parametrize(
    ("section", "field", "value", "match"),
    [
        ("order_ledger", "origin_subsystem", "UNREGISTERED", "origin_subsystem"),
        ("order_ledger", "mechanism", "UNREGISTERED", "mechanism"),
        ("order_ledger", "event_id", "uuid-like", "event_id"),
        ("fills", "industry_manifest_sha256", "0" * 64, "industry manifest"),
    ],
)
def test_native_schema_rejects_unknown_or_malformed_identity(
    tmp_path,
    section: str,
    field: str,
    value: str,
    match: str,
) -> None:
    source = tmp_path / "legacy.json"
    migrated_path = tmp_path / "native.json"
    source.write_text(json.dumps(_schema_v3_payload(reason="entry")), encoding="utf-8")
    migrate_account(
        source,
        migrated_path,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )
    payload = json.loads(migrated_path.read_text(encoding="utf-8"))
    payload[section][0][field] = value
    malformed = tmp_path / f"malformed-{section}-{field}.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=match):
        load_account(malformed)

def test_repeated_production_decisions_include_byte_identical_causal_metadata(
    data_dir,
) -> None:
    symbols = ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"]
    engine = ProductionEngine(data_dir)
    initial = domain.AccountState.empty(2_000_000.0)

    first, first_state = engine.deterministic_decision(
        symbols=symbols,
        as_of="2025-04-03",
        account=initial,
    )
    second, second_state = engine.deterministic_decision(
        symbols=list(reversed(symbols)),
        as_of="2025-04-03",
        account=initial,
    )

    canonical = lambda value: json.dumps(  # noqa: E731 - immutable test unit
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert canonical(asdict(first)) == canonical(asdict(second))
    assert canonical(first_state.to_dict()) == canonical(second_state.to_dict())
    assert first.targets
    for target in first.targets:
        assert target.event_id.startswith("evt_")
        assert domain.OriginSubsystem(target.origin_subsystem)
        assert domain.AttributionMechanism(target.mechanism)
        assert target.origin_lifecycle in {item.value for item in domain.Lifecycle}
        assert target.industry_at_entry != ""
        assert target.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256
    by_event = {target.event_id: target for target in first.targets}
    assert len(by_event) == len(first.targets)
    for order in first.pending_orders:
        target = by_event[order.event_id]
        assert order.origin_subsystem == target.origin_subsystem
        assert order.mechanism == target.mechanism
        assert order.origin_lifecycle == target.origin_lifecycle
        assert order.replaces_symbol == target.replaces_symbol
        assert order.industry_at_entry == target.industry_at_entry
