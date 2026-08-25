# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

import json
from dataclasses import asdict

import pandas as pd
import pytest

from uquant import types as domain
from uquant.account import load_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.engine import _attach_target_attribution
from uquant.execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.portfolio import PortfolioAllocator
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


def _multilot_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": date,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 3_000_000.0 if date == "2026-01-08" else 100_000_000.0,
                "amount": 30_000_000.0 if date == "2026-01-08" else 1_000_000_000.0,
            }
            for date in (
                "2026-01-05",
                "2026-01-06",
                "2026-01-07",
                "2026-01-08",
            )
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _native_multilot_partial_sell_account() -> domain.AccountState:
    account = domain.AccountState.empty(2_000_000.0)
    panel = {"sz300502": _multilot_frame()}
    for signal_date, fill_date, weight in (
        ("2026-01-05", "2026-01-06", 0.060),
        ("2026-01-06", "2026-01-07", 0.120),
    ):
        identity = _identity(signal_date=signal_date, target_weight=weight)
        target = domain.Target(
            symbol="sz300502",
            weight=weight,
            lifecycle=domain.Lifecycle.CORE.value,
            alpha_score=0.8,
            confidence=0.9,
            reason="structured BUY",
            **identity,
        )
        planned = plan_orders(
            signal_date=signal_date,
            targets=(target,),
            account=account,
            prices={"sz300502": 10.0},
            cfg=DEFAULT_CONFIG,
        )
        planned = reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=planned,
            submitted_date=signal_date,
        )
        account.pending_orders = list(planned)
        ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=pd.Timestamp(fill_date),
            account=account,
            panel=panel,
        )

    exit_identity = _identity(
        signal_date="2026-01-07",
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    exit_target = domain.Target(
        symbol="sz300502",
        weight=0.0,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.0,
        confidence=0.0,
        reason="structured SELL",
        **exit_identity,
    )
    planned_exit = plan_orders(
        signal_date="2026-01-07",
        targets=(exit_target,),
        account=account,
        prices={"sz300502": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    planned_exit = reconcile_account_orders(
        account=account,
        previous=list(account.pending_orders),
        current=planned_exit,
        submitted_date="2026-01-07",
    )
    account.pending_orders = list(planned_exit)
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-08"),
        account=account,
        panel=panel,
    )
    account.data_hash = "data"
    account.code_hash = "code"
    return account


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

    assert event_id == "evt_621c49e7a4f991dd517ccb1fd1dfd17f285c04b413eacb3f1a66192d04b46278"
    assert domain.derive_attribution_event_id(**fields) == event_id
    assert (
        domain.derive_attribution_event_id(
            **{
                **fields,
                "reason_code": "arbitrary_display_code",
                "exit_kind": "arbitrary_display_exit",
            }
        )
        == event_id
    )
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


def test_prose_mutation_cannot_change_target_order_or_fill_identity() -> None:
    leader = domain.LeaderScore(
        symbol="sz300502",
        score=0.8,
        confidence=0.9,
        mature=True,
        emerging=False,
        industry="optical",
        components={},
    )

    def execute(reason: str) -> tuple[domain.Target, domain.PendingOrder, domain.Fill]:
        account = domain.AccountState.empty(2_000_000.0)
        target = PortfolioAllocator(DEFAULT_CONFIG)._targets(
            proposed={"sz300502": 0.05},
            leaders={"sz300502": leader},
            account=account,
            lifecycle=domain.Lifecycle.CORE,
            reason=reason,
            origin_subsystem=domain.OriginSubsystem.LEADER,
            mechanism=domain.AttributionMechanism.LEADER_SELECTION,
        )[0]
        target = _attach_target_attribution(
            signal_date="2026-01-05",
            targets=(target,),
        )[0]
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
        fill = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=pd.Timestamp("2026-01-06"),
            account=account,
            panel={"sz300502": _frame()},
        )[0]
        return target, pending[0], fill

    plain = execute("confirmed leader selection")
    mutated = execute("confirmed leader selection; prose says replaces nothing")

    assert plain[0].reason_code != mutated[0].reason_code
    for left, right in zip(plain, mutated, strict=True):
        assert tuple(
            getattr(left, field) for field in domain.ATTRIBUTION_IDENTITY_FIELDS
        ) == tuple(
            getattr(right, field) for field in domain.ATTRIBUTION_IDENTITY_FIELDS
        )


def test_native_unlinked_fill_reconciles_by_machine_identity_after_prose_change(
    tmp_path,
) -> None:
    identity = _identity()
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="original display prose",
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
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300502": _frame()},
    )
    account.fills[0].order_id = ""
    account.fills[0].reason = "different display prose"
    account.fills[0].reason_code = "different_display_code"
    account.data_hash = "data"
    account.code_hash = "code"
    destination = tmp_path / "native-unlinked-prose-change.json"

    save_account(account, destination)

    restored = load_account(destination)
    assert restored.fills[0].event_id == restored.order_ledger[0].event_id
    assert restored.fills[0].order_id == ""


def test_native_unlinked_fill_requires_exactly_one_structured_ledger_match(
    tmp_path,
) -> None:
    identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    fill = domain.Fill(
        signal_date="2026-01-05",
        fill_date="2026-01-06",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=1.0,
        transfer_fee=0.1,
        slippage_cost=0.2,
        reason="orphan native fill",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="",
        reason_code="risk_off",
        exit_kind="risk_off",
        **identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.fills = [fill]
    account.data_hash = "data"
    account.code_hash = "code"
    destination = tmp_path / "native-unlinked-without-ledger.json"

    with pytest.raises(RuntimeError, match="exactly one structured account order"):
        save_account(account, destination)

    destination.write_text(json.dumps(account.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly one structured account order"):
        load_account(destination)


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


def test_native_partial_multilot_chain_round_trips(tmp_path) -> None:
    account = _native_multilot_partial_sell_account()
    sell_fill = next(fill for fill in account.fills if fill.side == domain.Side.SELL.value)

    assert len(sell_fill.sold_tranches) == 2
    assert account.positions["sz300502"].tranches
    destination = tmp_path / "valid-native-multilot.json"
    save_account(account, destination)

    assert load_account(destination).to_dict() == account.to_dict()


def test_native_live_tranche_event_must_chain_to_originating_buy(tmp_path) -> None:
    account = _native_multilot_partial_sell_account()
    account.positions["sz300502"].tranches[0].event_id = "evt_" + "f" * 64
    destination = tmp_path / "tampered-native-live-lot.json"

    with pytest.raises(RuntimeError, match="tranche does not chain to an originating BUY"):
        save_account(account, destination)

    destination.write_text(json.dumps(account.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="tranche does not chain to an originating BUY"):
        load_account(destination)


def test_native_sold_lot_event_must_chain_to_originating_buy(tmp_path) -> None:
    account = _native_multilot_partial_sell_account()
    sell_fill = next(fill for fill in account.fills if fill.side == domain.Side.SELL.value)
    sell_fill.sold_tranches[0]["event_id"] = "evt_" + "f" * 64
    destination = tmp_path / "tampered-native-sold-lot.json"

    with pytest.raises(RuntimeError, match="sold lot does not chain to an originating BUY"):
        save_account(account, destination)

    destination.write_text(json.dumps(account.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="sold lot does not chain to an originating BUY"):
        load_account(destination)


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








def _assert_reconcile_rejection_is_byte_atomic(
    *,
    account: domain.AccountState,
    previous: list[domain.PendingOrder],
    current: tuple[domain.PendingOrder, ...],
    error: str,
) -> None:
    """Assert a rejected reconciliation cannot change durable or caller state."""

    state_before = account.to_dict()
    canonical_before = json.dumps(
        state_before,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    ledger_before = json.dumps(
        [asdict(item) for item in account.order_ledger],
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    sequence_before = account.next_order_sequence
    callers_before = json.dumps(
        {
            "previous": [asdict(item) for item in previous],
            "current": [asdict(item) for item in current],
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(RuntimeError, match=error):
        reconcile_account_orders(
            account=account,
            previous=previous,
            current=current,
            submitted_date="2026-01-05",
        )

    assert account.to_dict() == state_before
    assert json.dumps(
        account.to_dict(),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") == canonical_before
    assert json.dumps(
        [asdict(item) for item in account.order_ledger],
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") == ledger_before
    assert account.next_order_sequence == sequence_before
    assert json.dumps(
        {
            "previous": [asdict(item) for item in previous],
            "current": [asdict(item) for item in current],
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") == callers_before



from _attribution_identity_retention_cases import (
    test_full_exit_retention_requires_identical_causal_identity,
    test_unchanged_full_exit_retains_the_same_broker_order,
    test_production_full_exit_retains_originating_event_for_residual_shares,
    test_partial_buy_keeps_originating_event_across_daily_mechanism_reclassification,
    test_blocked_recovery_replacement_retains_event_and_link_next_session,
    test_no_trade_band_equivalent_target_drift_inherits_the_active_event,
    test_new_buy_without_pit_universe_membership_fails_closed,
    test_new_buy_without_any_attribution_cannot_bypass_planning_validation,
    test_risk_off_identity_cannot_fabricate_a_native_buy_at_any_boundary,
    test_native_schema_legacy_identity_cannot_fabricate_a_new_buy,
    test_broker_rejects_a_planned_buy_without_canonical_attribution,
    test_legacy_schema_cannot_bypass_migration_through_save,
    test_sell_of_migrated_unmapped_lot_preserves_explicit_legacy_industry,
    test_unmatched_broker_inventory_fails_closed_without_a_planned_buy,
)

from _attribution_identity_reconciliation_cases import (
    test_schema_v3_identity_migration_is_explicit_deterministic_and_prose_free,
    test_schema_v3_unknown_buy_code_remains_explicitly_unattributed_and_not_a_leader,
    test_reconcile_account_orders_batch_is_atomic_when_a_later_buy_is_invalid,
    test_reconcile_rejects_same_side_duplicate_current_symbol_atomically,
    test_reconcile_rejects_opposing_current_sides_for_one_symbol_atomically,
    test_reconcile_rejects_duplicate_nonidentical_current_order_ids_atomically,
    test_reconcile_rejects_duplicate_previous_symbol_atomically,
    test_reconcile_rejects_conflicting_previous_current_order_id_atomically,
)

from _attribution_identity_schema_cases import (
    test_pre_fix_v4_identity_requires_validated_deterministic_v5_migration,
    test_real_v4_and_v5_event_mapping_cannot_split_on_display_fields,
    test_v4_to_v5_rejects_reverse_event_id_collision_before_writing,
    test_schema_v3_unlinked_fill_migration_uses_structured_identity_not_prose,
    test_schema_v3_unlinked_fill_migration_fails_closed_on_structured_ambiguity,
    test_native_schema_rejects_unknown_or_malformed_identity,
    test_repeated_production_decisions_include_byte_identical_causal_metadata,
)
