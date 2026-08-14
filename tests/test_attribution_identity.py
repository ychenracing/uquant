from __future__ import annotations

import json
from dataclasses import asdict

import pandas as pd
import pytest

from uquant import types as domain
from uquant.account import load_account, migrate_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _identity(
    *,
    signal_date: str = "2026-01-05",
    symbol: str = "sz300502",
    target_weight: float = 0.05,
    lifecycle: str = "CORE",
    origin_subsystem: str = "LEADER",
    mechanism: str = "LEADER_SELECTION",
    replaces_symbol: str | None = None,
    industry_at_entry: str = "optical",
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
) -> dict[str, str | None]:
    event_id = domain.derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=origin_subsystem,
        mechanism=mechanism,
        replaces_symbol=replaces_symbol,
        industry_at_entry=industry_at_entry,
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy=domain.ReductionPolicy.FIFO.value,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    return {
        "event_id": event_id,
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": replaces_symbol,
        "industry_at_entry": industry_at_entry,
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }


def _frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-01-05",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 100_000_000.0,
                "amount": 1_000_000_000.0,
            },
            {
                "date": "2026-01-06",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 100_000_000.0,
                "amount": 1_000_000_000.0,
            },
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _partial_sell_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": date,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 20_000.0,
                "amount": 200_000.0,
            }
            for date in ("2026-01-05", "2026-01-06", "2026-01-07")
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def test_event_id_has_a_frozen_canonical_derivation_and_collision_dimensions() -> None:
    fields = {
        "signal_date": "2026-01-05",
        "symbol": "sz300502",
        "target_weight": 0.5,
        "lifecycle": domain.Lifecycle.CORE.value,
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "origin_subsystem": domain.OriginSubsystem.LEADER.value,
        "mechanism": domain.AttributionMechanism.LEADER_ROTATION.value,
        "replaces_symbol": "sz300308",
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
        "reduction_policy": domain.ReductionPolicy.FIFO.value,
        "reason_code": "rotation",
        "exit_kind": "strategy",
    }

    event_id = domain.derive_attribution_event_id(**fields)

    assert event_id == "evt_bc0ddc2776fd4317f3231e7c55c86e22707c7621b0859dfc0349d73cd9951589"
    assert domain.derive_attribution_event_id(**fields) == event_id
    assert (
        domain.derive_attribution_event_id(**{**fields, "symbol": "sz300394"})
        != event_id
    )
    assert (
        domain.derive_attribution_event_id(**{**fields, "target_weight": 0.4})
        != event_id
    )
    assert (
        domain.derive_attribution_event_id(
            **{
                **fields,
                "mechanism": domain.AttributionMechanism.LEADER_SELECTION.value,
            }
        )
        != event_id
    )


def test_buy_identity_round_trips_target_order_fill_and_tranche() -> None:
    identity = _identity()
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="human prose can change without defining attribution",
        **identity,
    )
    account = domain.AccountState.empty(2_000_000.0)

    pending = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={"sz300502": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    pending = reconcile_account_orders(
        account=account,
        previous=[],
        current=pending,
        submitted_date="2026-01-05",
    )
    account.pending_orders = list(pending)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300502": _frame()},
    )

    assert len(fills) == 1
    ledger = account.order_ledger[0]
    tranche = account.positions["sz300502"].tranches[0]
    for item in (pending[0], ledger, fills[0], tranche):
        assert item.event_id == identity["event_id"]
        assert item.origin_subsystem == domain.OriginSubsystem.LEADER.value
        assert item.mechanism == domain.AttributionMechanism.LEADER_SELECTION.value
        assert item.origin_lifecycle == domain.Lifecycle.CORE.value
        assert item.lifecycle == domain.Lifecycle.CORE.value
        assert item.replaces_symbol is None
        assert item.industry_at_entry == "optical"
        assert item.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256


def test_partial_multitranche_sell_keeps_each_lot_origin_after_promotion() -> None:
    first_identity = _identity(
        signal_date="2025-12-01",
        target_weight=0.10,
        lifecycle=domain.Lifecycle.ADD1.value,
        mechanism=domain.AttributionMechanism.LEADER_PYRAMID.value,
    )
    second_identity = _identity(
        signal_date="2025-12-02",
        target_weight=0.10,
        lifecycle=domain.Lifecycle.SATELLITE.value,
        mechanism=domain.AttributionMechanism.CHALLENGER_SCOUT.value,
    )
    tranches = [
        domain.Tranche(
            tranche_id="lot-add1",
            lifecycle=domain.Lifecycle.CORE.value,
            shares=100,
            avg_cost=9.0,
            entry_date="2025-12-02",
            sellable_date="2025-12-03",
            highest_close=11.0,
            **first_identity,
        ),
        domain.Tranche(
            tranche_id="lot-satellite",
            lifecycle=domain.Lifecycle.CORE.value,
            shares=100,
            avg_cost=10.0,
            entry_date="2025-12-03",
            sellable_date="2025-12-04",
            highest_close=11.0,
            **second_identity,
        ),
    ]
    position = domain.Position(
        symbol="sz300502",
        shares=200,
        avg_cost=9.5,
        entry_date="2025-12-02",
        highest_close=11.0,
        lifecycle=domain.Lifecycle.CORE.value,
        tranches=tranches,
    )
    account = domain.AccountState(
        initial_cash=2_000.0,
        cash=0.0,
        positions={"sz300502": position},
        operating_peak=2_000.0,
        capital_peak=2_000.0,
    )
    exit_identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
        reason_code="risk_off",
        exit_kind="risk_off",
    )
    target = domain.Target(
        symbol="sz300502",
        weight=0.0,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.0,
        confidence=0.0,
        reason="risk prose",
        reason_code="risk_off",
        exit_kind="risk_off",
        **exit_identity,
    )
    pending = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={"sz300502": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    pending = reconcile_account_orders(
        account=account,
        previous=[],
        current=pending,
        submitted_date="2026-01-05",
    )
    account.pending_orders = list(pending)
    planner = ExecutionPlanner(DEFAULT_CONFIG)

    first_fill = planner.execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300502": _partial_sell_frame()},
    )[0]
    second_fill = planner.execute_open(
        date=pd.Timestamp("2026-01-07"),
        account=account,
        panel={"sz300502": _partial_sell_frame()},
    )[0]

    assert first_fill.event_id == second_fill.event_id == exit_identity["event_id"]
    assert [first_fill.sold_tranches[0]["event_id"], second_fill.sold_tranches[0]["event_id"]] == [
        first_identity["event_id"],
        second_identity["event_id"],
    ]
    assert [
        first_fill.sold_tranches[0]["origin_lifecycle"],
        second_fill.sold_tranches[0]["origin_lifecycle"],
    ] == [domain.Lifecycle.ADD1.value, domain.Lifecycle.SATELLITE.value]
    assert [
        first_fill.sold_tranches[0]["lifecycle"],
        second_fill.sold_tranches[0]["lifecycle"],
    ] == [domain.Lifecycle.CORE.value, domain.Lifecycle.CORE.value]
    assert all(
        allocation["industry_at_entry"] == "optical"
        for fill in (first_fill, second_fill)
        for allocation in fill.sold_tranches
    )


def test_broker_fill_import_preserves_planned_order_identity() -> None:
    identity = _identity()
    pending = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="planned entry",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        **identity,
    )
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date="2026-01-05",
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.OPEN.value,
        **identity,
    )
    account = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    snapshot = {
        "as_of": "2026-01-06",
        "cash": 1_998_994.9,
        "positions": [
            {
                "symbol": "300502",
                "shares": 100,
                "sellable_shares": 0,
                "avg_cost": 10.051,
            }
        ],
        "fills": [
            {
                "fill_id": "broker-fill-identity-1",
                "order_id": pending.order_id,
                "fill_date": "2026-01-06",
                "symbol": "300502",
                "side": "BUY",
                "shares": 100,
                "price": 10.0,
                "gross_value": 1_000.0,
                "commission": 5.0,
                "transfer_fee": 0.1,
                "final": True,
                "remaining_shares": 0,
            }
        ],
    }

    sync_broker_snapshot(account, snapshot)

    imported_fill = account.fills[0]
    imported_tranche = account.positions["sz300502"].tranches[0]
    for item in (imported_fill, imported_tranche):
        assert item.event_id == identity["event_id"]
        assert item.origin_subsystem == identity["origin_subsystem"]
        assert item.mechanism == identity["mechanism"]
        assert item.origin_lifecycle == identity["origin_lifecycle"]
        assert item.industry_at_entry == identity["industry_at_entry"]
        assert item.industry_manifest_sha256 == identity["industry_manifest_sha256"]


def test_changed_causal_identity_supersedes_same_weight_retained_order() -> None:
    retained_identity = _identity()
    replacement_identity = _identity(
        mechanism=domain.AttributionMechanism.LEADER_ROTATION.value,
        replaces_symbol="sz300308",
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="old target",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **retained_identity,
    )
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="old target",
        **replacement_identity,
    )
    planned = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="old target",
        lifecycle=target.lifecycle,
        **replacement_identity,
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert merged == (planned,)
    assert merged[0].event_id == replacement_identity["event_id"]


def test_new_buy_without_pit_universe_membership_fails_closed() -> None:
    symbol = "sz000001"
    identity = _identity(symbol=symbol)
    target = domain.Target(
        symbol=symbol,
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="outside reviewed universe",
        **identity,
    )

    with pytest.raises(RuntimeError, match="no point-in-time AI-universe membership"):
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={symbol: 10.0},
            cfg=DEFAULT_CONFIG,
        )


def test_legacy_schema_cannot_bypass_migration_through_save(tmp_path) -> None:
    legacy = domain.AccountState.empty(2_000_000.0)
    legacy.schema_version = 3

    with pytest.raises(RuntimeError, match="explicit migration"):
        save_account(legacy, tmp_path / "legacy-write.json")


def test_sell_of_migrated_unmapped_lot_preserves_explicit_legacy_industry(
    tmp_path,
) -> None:
    symbol = "sz000001"
    legacy = domain.AccountState.empty(2_000_000.0)
    legacy.schema_version = 3
    legacy.data_hash = "data"
    legacy.code_hash = "old-code"
    legacy.positions[symbol] = domain.Position(
        symbol=symbol,
        shares=100,
        avg_cost=10.0,
        entry_date="2025-12-01",
        highest_close=10.0,
        tranches=[
            domain.Tranche(
                tranche_id="legacy-unmapped-lot",
                lifecycle=domain.Lifecycle.CORE.value,
                shares=100,
                avg_cost=10.0,
                entry_date="2025-12-01",
                sellable_date="2025-12-02",
                highest_close=10.0,
            )
        ],
    )
    legacy_path = tmp_path / "legacy-unmapped.json"
    legacy_path.write_text(json.dumps(legacy.to_dict()), encoding="utf-8")
    account = migrate_account(
        legacy_path,
        legacy_path,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )
    event_id = domain.derive_attribution_event_id(
        signal_date="2026-01-05",
        symbol=symbol,
        target_weight=0.0,
        lifecycle=domain.Lifecycle.CORE.value,
        origin_lifecycle=domain.Lifecycle.CORE.value,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
        replaces_symbol=None,
        industry_at_entry="legacy_unmapped",
        industry_manifest_sha256="0" * 64,
        reduction_policy=domain.ReductionPolicy.FIFO.value,
        reason_code="risk_off",
        exit_kind="risk_off",
    )
    target = domain.Target(
        symbol=symbol,
        weight=0.0,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.0,
        confidence=0.0,
        reason="sell migrated holding",
        reason_code="risk_off",
        exit_kind="risk_off",
        event_id=event_id,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
        origin_lifecycle=domain.Lifecycle.CORE.value,
        industry_at_entry="legacy_unmapped",
        industry_manifest_sha256="0" * 64,
    )
    pending = plan_orders(
        signal_date="2026-01-05",
        targets=(target,),
        account=account,
        prices={symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    pending = reconcile_account_orders(
        account=account,
        previous=[],
        current=pending,
        submitted_date="2026-01-05",
    )
    account.pending_orders = list(pending)

    destination = tmp_path / "unmapped-sell.json"
    save_account(account, destination)
    restored = load_account(destination)
    assert restored.pending_orders[0].industry_at_entry == "legacy_unmapped"
    assert restored.positions[symbol].tranches[0].origin_subsystem == (
        domain.OriginSubsystem.LEGACY_MIGRATION.value
    )


def test_unmatched_broker_inventory_gets_explicit_reconciliation_identity(
    tmp_path,
) -> None:
    account = domain.AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-07",
            "cash": 1_999_000.0,
            "positions": [
                {
                    "symbol": "sz300502",
                    "shares": 100,
                    "sellable_shares": 100,
                    "avg_cost": 10.0,
                }
            ],
            "fills": [],
        },
    )

    tranche = account.positions["sz300502"].tranches[0]
    assert tranche.event_id.startswith("evt_")
    assert tranche.origin_subsystem == domain.OriginSubsystem.BROKER_RECONCILIATION.value
    assert tranche.mechanism == domain.AttributionMechanism.BROKER_RECONCILIATION.value
    assert tranche.origin_lifecycle == domain.Lifecycle.CORE.value
    assert tranche.industry_at_entry == "optical"
    assert tranche.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256
    destination = tmp_path / "broker-reconciled.json"
    save_account(account, destination)
    assert load_account(destination).to_dict() == account.to_dict()


def _schema_v3_payload(*, reason: str) -> dict[str, object]:
    order = domain.AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason=reason,
        lifecycle=domain.Lifecycle.CORE.value,
        status=domain.OrderStatus.FILLED.value,
        requested_shares=100,
        filled_shares=100,
        last_update_date="2026-01-06",
        last_event="FILL",
    )
    fill = domain.Fill(
        signal_date=order.signal_date,
        fill_date="2026-01-06",
        symbol=order.symbol,
        side=order.side,
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=0.0,
        transfer_fee=0.1,
        slippage_cost=0.2,
        reason=reason,
        lifecycle=order.lifecycle,
        order_id=order.order_id,
    )
    tranche = domain.Tranche(
        tranche_id="legacy-lot-1",
        lifecycle=domain.Lifecycle.CORE.value,
        shares=100,
        avg_cost=10.051,
        entry_date="2026-01-06",
        sellable_date="2026-01-07",
        highest_close=10.0,
        lowest_close=10.0,
    )
    state = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=1_998_994.9,
        positions={
            "sz300502": domain.Position(
                symbol="sz300502",
                shares=100,
                avg_cost=10.051,
                entry_date="2026-01-06",
                highest_close=10.0,
                lifecycle=domain.Lifecycle.CORE.value,
                tranches=[tranche],
            )
        },
        order_ledger=[order],
        next_order_sequence=2,
        fills=[fill],
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
        data_hash="data",
        code_hash="old-code",
    )
    payload = state.to_dict()
    payload["schema_version"] = 3
    identity_fields = {
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
    }
    for order_payload in payload["order_ledger"]:
        for field in identity_fields:
            order_payload.pop(field)
    for fill_payload in payload["fills"]:
        for field in identity_fields:
            fill_payload.pop(field)
    for position_payload in payload["positions"].values():
        for tranche_payload in position_payload["tranches"]:
            for field in identity_fields:
                tranche_payload.pop(field)
    return payload


def test_schema_v3_identity_migration_is_explicit_deterministic_and_prose_free(tmp_path) -> None:
    first_payload = _schema_v3_payload(reason="first human explanation")
    second_payload = _schema_v3_payload(reason="completely different prose")
    first_source = tmp_path / "first-v3.json"
    second_source = tmp_path / "second-v3.json"
    first_destination = tmp_path / "first-v4.json"
    second_destination = tmp_path / "second-v4.json"
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

    assert first.schema_version == second.schema_version == domain.ACCOUNT_SCHEMA_VERSION == 4
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

    canonical = lambda value: json.dumps(  # noqa: E731
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
