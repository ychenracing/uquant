from __future__ import annotations

import json
from dataclasses import asdict

import pandas as pd
import pytest

from uquant import types as domain
from uquant.account import load_account, migrate_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine, _attach_target_attribution
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

    assert event_id == "evt_0317b9c66fa7011405a95cc5d174e8f5d3c724f1ea3d3563867c8aeedf76716b"
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


@pytest.mark.parametrize(
    "identity_change",
    [
        {"mechanism": domain.AttributionMechanism.CRISIS.value},
        {
            "origin_subsystem": domain.OriginSubsystem.RECOVERY.value,
            "mechanism": domain.AttributionMechanism.RECOVERY_CAP.value,
        },
        {"replaces_symbol": "sz300308"},
        {"industry_at_entry": "semiconductor"},
        {"event_id": "evt_" + "f" * 64},
    ],
)
def test_full_exit_retention_requires_identical_causal_identity(
    identity_change: dict[str, str],
) -> None:
    retained_identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="full exit",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **retained_identity,
    )
    changed_identity = {**retained_identity, **identity_change}
    if "event_id" not in identity_change:
        changed_identity["event_id"] = domain.derive_attribution_event_id(
            signal_date="2026-01-06",
            symbol=retained.symbol,
            target_weight=0.0,
            lifecycle=retained.lifecycle,
            origin_lifecycle=str(changed_identity["origin_lifecycle"]),
            origin_subsystem=str(changed_identity["origin_subsystem"]),
            mechanism=str(changed_identity["mechanism"]),
            replaces_symbol=changed_identity.get("replaces_symbol"),
            industry_at_entry=str(changed_identity["industry_at_entry"]),
            industry_manifest_sha256=str(
                changed_identity["industry_manifest_sha256"]
            ),
            reduction_policy=domain.ReductionPolicy.FIFO.value,
            reason_code="strategy_target",
            exit_kind="strategy",
        )
    planned = domain.PendingOrder(
        signal_date="2026-01-06",
        symbol=retained.symbol,
        side=retained.side,
        target_weight=0.0,
        reason=retained.reason,
        lifecycle=retained.lifecycle,
        **changed_identity,
    )
    target = domain.Target(
        symbol=retained.symbol,
        weight=0.0,
        lifecycle=retained.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason=retained.reason,
        **changed_identity,
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert merged == (planned,)


def test_unchanged_full_exit_retains_the_same_broker_order() -> None:
    identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="full exit",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **identity,
    )
    planned = domain.PendingOrder(
        signal_date="2026-01-06",
        symbol=retained.symbol,
        side=retained.side,
        target_weight=retained.target_weight,
        reason="different display prose",
        lifecycle=retained.lifecycle,
        **identity,
    )
    target = domain.Target(
        symbol=retained.symbol,
        weight=retained.target_weight,
        lifecycle=retained.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason=planned.reason,
        **identity,
    )

    assert merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    ) == (retained,)


def test_blocked_recovery_replacement_retains_event_and_link_next_session() -> None:
    identity = _identity(
        signal_date="2026-01-05",
        symbol="sz300502",
        target_weight=0.30,
        origin_subsystem=domain.OriginSubsystem.RECOVERY.value,
        mechanism=domain.AttributionMechanism.RECOVERY_SUBSTITUTION.value,
        replaces_symbol="sh688008",
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.30,
        reason="recovery anchor entry: replaces sh688008",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        attempts=1,
        **identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.pending_orders = [retained]
    account.anchor_weights = {"sz300308": 0.30, "sz300502": 0.30}
    account.candidate_tenure["recovery_substitution_pending"] = 1
    account.replacement_events.append(
        {
            "signal_date": "2026-01-05",
            "old_symbol": "sh688008",
            "new_symbol": "sz300502",
            "route": "recovery_anchor_substitution",
        }
    )
    leaders = {
        symbol: domain.LeaderScore(
            symbol=symbol,
            score=0.8,
            confidence=0.9,
            mature=True,
            emerging=False,
            industry=industry,
            components={},
        )
        for symbol, industry in {
            "sz300308": "film",
            "sz300502": "optical",
        }.items()
    }
    targets = PortfolioAllocator(DEFAULT_CONFIG)._recovery_anchor_substitution(
        date=pd.Timestamp("2026-01-06"),
        risk=domain.RiskAssessment(
            state=domain.Risk.NORMAL,
            target_gross_cap=1.0,
            votes=0,
            evidence={},
            reasons=(),
            shock_state="NONE",
        ),
        user_panel={},
        leaders=leaders,
        account=account,
        weights_now={"sz300308": 0.30, "sz300502": 0.0},
        anchor_elapsed=DEFAULT_CONFIG.recovery_add_window_days + 1,
    )
    assert targets is not None
    targets = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=targets,
        retained_orders=account.pending_orders,
    )
    replacement = next(target for target in targets if target.symbol == retained.symbol)
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=targets,
        account=account,
        prices={"sz300308": 10.0, "sz300502": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders,
        planned=planned,
        targets=targets,
        cfg=DEFAULT_CONFIG,
    )
    merged_replacement = next(order for order in merged if order.symbol == retained.symbol)

    assert replacement.replaces_symbol == "sh688008"
    assert replacement.event_id == retained.event_id
    assert merged_replacement is retained
    assert merged_replacement.replaces_symbol == "sh688008"


def test_no_trade_band_equivalent_target_drift_inherits_the_active_event() -> None:
    retained_identity = _identity(target_weight=0.95)
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.95,
        reason="strategic cohort",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=1_900,
        attempts=3,
        **retained_identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.pending_orders = [retained]
    target = domain.Target(
        symbol=retained.symbol,
        weight=0.931,
        lifecycle=retained.lifecycle,
        alpha_score=0.8,
        confidence=0.9,
        reason="retain strategic price drift",
        origin_subsystem=domain.OriginSubsystem.LEADER.value,
        mechanism=domain.AttributionMechanism.LEADER_SELECTION.value,
        origin_lifecycle=domain.Lifecycle.CORE.value,
    )
    target = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=(target,),
        retained_orders=account.pending_orders,
    )[0]

    planned = plan_orders(
        signal_date="2026-01-06",
        targets=(target,),
        account=account,
        prices={retained.symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders,
        planned=planned,
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert target.event_id == retained.event_id
    assert planned == ()
    assert merged == (retained,)


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


def test_new_buy_without_any_attribution_cannot_bypass_planning_validation() -> None:
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="empty machine attribution must not create a BUY",
    )

    with pytest.raises(RuntimeError, match="event_id"):
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={"sz300502": 10.0},
            cfg=DEFAULT_CONFIG,
        )


def test_native_schema_legacy_identity_cannot_fabricate_a_new_buy(tmp_path) -> None:
    signal_date = "2026-01-05"
    identity = {
        "event_id": domain.derive_attribution_event_id(
            signal_date=signal_date,
            symbol="sz300502",
            target_weight=0.05,
            lifecycle=domain.Lifecycle.CORE.value,
            origin_lifecycle=domain.Lifecycle.CORE.value,
            origin_subsystem=domain.OriginSubsystem.LEGACY_MIGRATION.value,
            mechanism=domain.AttributionMechanism.LEGACY_MIGRATION.value,
            replaces_symbol=None,
            industry_at_entry="legacy_unmapped",
            industry_manifest_sha256="0" * 64,
            reduction_policy=domain.ReductionPolicy.FIFO.value,
            reason_code="strategy_target",
            exit_kind="strategy",
        ),
        "origin_subsystem": domain.OriginSubsystem.LEGACY_MIGRATION.value,
        "mechanism": domain.AttributionMechanism.LEGACY_MIGRATION.value,
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "industry_at_entry": "legacy_unmapped",
        "industry_manifest_sha256": "0" * 64,
    }
    pending = domain.PendingOrder(
        signal_date=signal_date,
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="fabricated native legacy BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **identity,
    )
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=signal_date,
        submitted_date=signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.SUBMITTED.value,
        requested_shares=100,
        remaining_shares=100,
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
        data_hash="data",
        code_hash="code",
    )
    destination = tmp_path / "fabricated-native-legacy-buy.json"

    with pytest.raises(RuntimeError, match="legacy migration identity cannot create a BUY"):
        save_account(account, destination)

    destination.write_text(json.dumps(account.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="legacy migration identity cannot create a BUY"):
        load_account(destination)


def test_broker_rejects_a_planned_buy_without_canonical_attribution() -> None:
    pending = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="missing attribution",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
    )
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.SUBMITTED.value,
        requested_shares=100,
        remaining_shares=100,
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
    before = account.to_dict()

    with pytest.raises(RuntimeError, match="invalid event_id"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-06",
                "cash": 2_000_000.0,
                "positions": [],
                "fills": [],
            },
        )

    assert account.to_dict() == before


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


def test_unmatched_broker_inventory_fails_closed_without_a_planned_buy() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    before = account.to_dict()

    with pytest.raises(ValueError, match="exceeds known BUY lot inventory"):
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

    assert account.to_dict() == before


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


def test_schema_v3_unlinked_fill_migration_uses_structured_identity_not_prose(
    tmp_path,
) -> None:
    payload = _schema_v3_payload(reason="ledger prose")
    payload["fills"][0]["order_id"] = ""
    payload["fills"][0]["reason"] = "unrelated fill prose"
    source = tmp_path / "unlinked-v3.json"
    destination = tmp_path / "unlinked-v4.json"
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
    destination = tmp_path / "ambiguous-unlinked-v4.json"
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
