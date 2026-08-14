from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from uquant.account import load_account, migrate_account
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Fill,
    Lifecycle,
    OrderStatus,
    OriginSubsystem,
    Position,
    ReductionPolicy,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOL = "sz300308"


def _identity(
    *,
    signal_date: str = "2026-01-05",
    symbol: str = SYMBOL,
    target_weight: float = 0.0,
    lifecycle: str = Lifecycle.CORE.value,
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
    reduction_policy: str = ReductionPolicy.FIFO.value,
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "origin_subsystem": OriginSubsystem.LEADER.value,
        "mechanism": AttributionMechanism.LEADER_SELECTION.value,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    fields["event_id"] = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=OriginSubsystem.LEADER.value,
        mechanism=AttributionMechanism.LEADER_SELECTION.value,
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy=reduction_policy,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    return fields


def _write_payload(tmp_path, payload: dict[str, Any], name: str = "account.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _position_state() -> AccountState:
    state = AccountState.empty(2_000_000.0)
    state.data_hash = "data"
    state.code_hash = "code"
    state.positions[SYMBOL] = Position(
        symbol=SYMBOL,
        shares=100,
        avg_cost=10.0,
        entry_date="2026-01-05",
        highest_close=11.0,
        lifecycle="CORE",
        tranches=[
            Tranche(
                tranche_id="lot-1",
                lifecycle="CORE",
                shares=100,
                avg_cost=10.0,
                entry_date="2026-01-05",
                sellable_date="2026-01-06",
                highest_close=11.0,
                lowest_close=9.0,
                mfe=0.10,
                mae=-0.10,
                entry_score=0.8,
                entry_confidence=0.9,
                entry_regime="TREND",
                entry_industry_strength=0.7,
                **_identity(),
            )
        ],
    )
    return state


def test_sector_guard_cohort_round_trips_in_native_schema(tmp_path) -> None:
    state = AccountState.empty(2_000_000.0)
    state.data_hash = "data"
    state.code_hash = "code"
    state.sector_guard_active = True
    state.sector_guard_started = "2026-01-05"
    state.sector_guard_symbols = ["sh688008", "sz300308", "sz300502"]

    loaded = load_account(_write_payload(tmp_path, state.to_dict()))

    assert loaded.sector_guard_symbols == state.sector_guard_symbols
    assert loaded.to_dict() == state.to_dict()


def test_recovery_conviction_owner_round_trips_in_native_schema(tmp_path) -> None:
    state = AccountState.empty(2_000_000.0)
    state.data_hash = "data"
    state.code_hash = "code"
    state.recovery_conviction_symbol = SYMBOL

    loaded = load_account(_write_payload(tmp_path, state.to_dict()))

    assert loaded.recovery_conviction_symbol == SYMBOL
    assert loaded.to_dict() == state.to_dict()


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("sellable_date", "2026-01-04", "sellable date predates"),
        ("entry_confidence", -0.01, "entry_confidence"),
        ("entry_confidence", 1.01, "entry_confidence"),
        ("entry_regime", "UNKNOWN", "entry_regime"),
        ("mfe", -0.01, "tranche mfe"),
        ("mae", 0.01, "tranche mae"),
    ],
)
def test_native_v3_rejects_invalid_tranche_economic_metadata(
    tmp_path,
    field: str,
    value: Any,
    match: str,
):
    payload = _position_state().to_dict()
    payload["positions"][SYMBOL]["tranches"][0][field] = value

    with pytest.raises(RuntimeError, match=match):
        load_account(_write_payload(tmp_path, payload))


def _sell_fill_state() -> AccountState:
    identity = _identity(
        reason_code="risk_gross_cap",
        exit_kind="risk",
        reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
    )
    order = AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-06",
        symbol=SYMBOL,
        side="SELL",
        target_weight=0.0,
        reason="risk exit",
        lifecycle="CORE",
        status=OrderStatus.FILLED.value,
        requested_shares=100,
        filled_shares=100,
        remaining_shares=0,
        last_update_date="2026-01-07",
        last_event="FILLED",
        reduction_policy="RISK_PRIORITY",
        reason_code="risk_gross_cap",
        exit_kind="risk",
        **identity,
    )
    allocation = {
        "tranche_id": "lot-1",
        "lifecycle": "CORE",
        "shares": 100,
        "cost": 10.0,
        "unit_cost": 10.0,
        "avg_cost": 10.0,
        "cost_basis": 1_000.0,
        "entry_date": "2026-01-05",
        "mfe": 0.10,
        "mae": -0.10,
        "commission": 5.0,
        "stamp_duty": 1.0,
        "transfer_fee": 0.1,
        "slippage_cost": 0.2,
        "fees": 6.1,
        "transaction_costs": 6.3,
        **identity,
    }
    fill = Fill(
        signal_date=order.signal_date,
        fill_date="2026-01-07",
        symbol=SYMBOL,
        side="SELL",
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=1.0,
        transfer_fee=0.1,
        slippage_cost=0.2,
        reason=order.reason,
        lifecycle=order.lifecycle,
        order_id=order.order_id,
        fill_id="fill-1",
        reduction_policy=order.reduction_policy,
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        sold_tranches=[allocation],
        **identity,
    )
    state = AccountState.empty(2_000_000.0)
    state.order_ledger = [order]
    state.next_order_sequence = 2
    state.fills = [fill]
    state.data_hash = "data"
    state.code_hash = "code"
    return state


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (
            lambda allocation: allocation.update(unit_cost=11.0),
            "unit-cost aliases differ",
        ),
        (
            lambda allocation: allocation.update(cost_basis=999.0),
            "cost_basis does not reconcile",
        ),
        (
            lambda allocation: allocation.update(
                commission=4.0,
                fees=5.1,
                transaction_costs=5.3,
            ),
            "commission does not reconcile to fill",
        ),
        (
            lambda allocation: allocation.pop("slippage_cost"),
            "fee detail is incomplete",
        ),
        (
            lambda allocation: allocation.update(mfe=-0.01),
            "sold-lot mfe",
        ),
        (
            lambda allocation: allocation.update(mae=0.01),
            "sold-lot mae",
        ),
    ],
)
def test_native_v3_rejects_inconsistent_sell_lot_attribution(
    tmp_path,
    mutate: Callable[[dict[str, Any]], Any],
    match: str,
):
    payload = _sell_fill_state().to_dict()
    mutate(payload["fills"][0]["sold_tranches"][0])

    with pytest.raises(RuntimeError, match=match):
        load_account(_write_payload(tmp_path, payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(initial_cash="2000000"),
        lambda payload: payload["positions"][SYMBOL].update(shares="100"),
        lambda payload: payload["positions"][SYMBOL]["tranches"][0].update(avg_cost="10.0"),
        lambda payload: payload.update(dynamic_k="1"),
        lambda payload: payload.update(schema_version="3"),
    ],
)
def test_native_v3_rejects_numeric_strings(tmp_path, mutate):
    payload = _position_state().to_dict()
    mutate(payload)

    with pytest.raises(RuntimeError):
        load_account(_write_payload(tmp_path, payload))


def test_explicit_schema_v2_migration_can_coerce_legacy_numeric_fields(tmp_path):
    payload = _position_state().to_dict()
    payload["schema_version"] = "2"
    payload["initial_cash"] = "2000000"
    payload["cash"] = "2000000"
    payload["positions"][SYMBOL]["shares"] = "100"
    payload["positions"][SYMBOL]["tranches"][0]["avg_cost"] = "10.0"
    path = _write_payload(tmp_path, payload, "legacy-v2.json")

    legacy = load_account(path, allow_legacy_schema=True)
    assert legacy.initial_cash == 2_000_000.0
    assert legacy.positions[SYMBOL].shares == 100
    assert legacy.positions[SYMBOL].tranches[0].avg_cost == 10.0

    migrated = migrate_account(
        path,
        path,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )
    assert load_account(path).to_dict() == migrated.to_dict()


@pytest.mark.parametrize(
    "field",
    ["active_leaders", "data_hash_symbols"],
)
def test_native_v3_symbol_lists_reject_values_that_were_previously_coerced(
    tmp_path,
    field: str,
):
    payload = _position_state().to_dict()
    payload[field] = [300308]

    with pytest.raises(RuntimeError, match=field):
        load_account(_write_payload(tmp_path, payload))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("replacement_events", ["bad"], "contain objects"),
        (
            "replacement_events",
            [
                {
                    "signal_date": "not-a-date",
                    "old_symbol": SYMBOL,
                    "new_symbol": "sz300750",
                    "old_close": 10.0,
                    "new_close": 11.0,
                    "edge": 0.1,
                }
            ],
            "ISO date",
        ),
        (
            "lifecycle_events",
            [
                {
                    "date": "2026-01-07",
                    "symbol": SYMBOL,
                    "from": "UNKNOWN",
                    "to": "CORE",
                    "shares": 100,
                    "reason": "promotion",
                }
            ],
            "from lifecycle",
        ),
        (
            "risk_events",
            [{"date": "2026-01-07", "from": "NORMAL", "to": "UNKNOWN"}],
            "risk transition",
        ),
        (
            "reconciliation_events",
            [{"date": "2026-01-07", "symbol": SYMBOL, "event": "UNKNOWN"}],
            "event type",
        ),
    ],
)
def test_native_v3_strictly_validates_audit_event_shapes_and_enums(
    tmp_path,
    field: str,
    value: Any,
    match: str,
):
    payload = _position_state().to_dict()
    payload[field] = value

    with pytest.raises(RuntimeError, match=match):
        load_account(_write_payload(tmp_path, payload))


def test_native_v3_accepts_all_current_audit_event_variants(tmp_path):
    payload = _position_state().to_dict()
    payload["replacement_events"] = [
        {
            "signal_date": "2026-01-07",
            "old_symbol": SYMBOL,
            "new_symbol": "sz300750",
            "old_close": 10.0,
            "new_close": 11.0,
            "edge": 0.1,
            "industry_handoff": True,
        }
    ]
    payload["lifecycle_events"] = [
        {
            "date": "2026-01-07",
            "symbol": SYMBOL,
            "from": "SATELLITE",
            "to": "CORE",
            "shares": 100,
            "reason": "confirmed",
        }
    ]
    payload["risk_events"] = [
        {
            "date": "2026-01-07",
            "event": "sector_guard_on",
            "shock_count": 2,
            "leadership_divergence": 0.1,
            "equal_weight_return": None,
            "exposure_weighted_return": -0.05,
        },
        {
            "date": "2026-01-08",
            "from": "CRISIS",
            "to": "CAUTION",
            "votes": 1,
            "reasons": ["repair"],
            "severity": "MARKET",
            "route": "risk_state",
        },
    ]
    payload["reconciliation_events"] = [
        {
            "date": "2026-01-07",
            "symbol": SYMBOL,
            "event": "economic_lot_degraded",
            "unmatched_shares": 100,
            "reason": "broker snapshot exceeded known lot inventory",
        }
    ]

    loaded = load_account(_write_payload(tmp_path, payload))
    assert loaded.replacement_events == payload["replacement_events"]
    assert loaded.lifecycle_events == payload["lifecycle_events"]
    assert loaded.risk_events == payload["risk_events"]
    assert loaded.reconciliation_events == payload["reconciliation_events"]
