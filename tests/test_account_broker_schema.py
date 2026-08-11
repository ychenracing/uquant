from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest

from uquant.account import load_account, migrate_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.types import AccountOrder, AccountState, Fill, OrderStatus, PendingOrder


def _state_with_open_order() -> AccountState:
    pending = PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.5,
        reason="entry",
        lifecycle="CORE",
        order_id="O000000001",
        entry_score=0.8,
        entry_confidence=0.9,
        entry_regime="TREND",
        entry_industry_strength=0.7,
    )
    ledger = AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date="2026-01-06",
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=OrderStatus.OPEN.value,
        last_update_date="2026-01-06",
        entry_score=pending.entry_score,
        entry_confidence=pending.entry_confidence,
        entry_regime=pending.entry_regime,
        entry_industry_strength=pending.entry_industry_strength,
    )
    return AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        data_hash="data",
        code_hash="code",
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )


def _write_account(tmp_path, payload: dict[str, Any], name: str = "account.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_account_loader_rejects_nonstandard_json_numbers(tmp_path, constant):
    path = tmp_path / "nonstandard.json"
    path.write_text(
        '{"schema_version": 3, "initial_cash": 1, "cash": '
        + constant
        + ', "data_hash": "data", "code_hash": "code"}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="missing or corrupt"):
        load_account(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signal_date", "2026-99-01"),
        ("side", "HOLD"),
        ("target_weight", -0.01),
        ("target_weight", 1.01),
        ("lifecycle", "UNKNOWN"),
        ("remaining_shares", -1),
        ("attempts", 1.5),
        ("reason", ""),
        ("reason_code", ""),
        ("exit_kind", ""),
        ("entry_confidence", 1.01),
        ("entry_regime", "UNKNOWN"),
    ],
)
def test_account_loader_strictly_validates_pending_order_fields(tmp_path, field, value):
    state = _state_with_open_order()
    state.pending_orders[0].order_id = ""
    state.order_ledger = []
    payload = state.to_dict()
    payload["pending_orders"][0][field] = value

    with pytest.raises(RuntimeError, match="pending order"):
        load_account(_write_account(tmp_path, payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda order: order.update(submitted_date="2026-01-04"),
        lambda order: order.update(last_update_date="2026-01-04"),
        lambda order: order.update(requested_shares=99, filled_shares=100),
        lambda order: order.update(
            requested_shares=100,
            filled_shares=50,
            remaining_shares=0,
            status="PARTIALLY_FILLED",
        ),
        lambda order: order.update(attempts=-1),
    ],
)
def test_account_loader_rejects_impossible_account_order_lifecycle(
    tmp_path,
    mutate: Callable[[dict[str, Any]], None],
):
    state = _state_with_open_order()
    payload = state.to_dict()
    mutate(payload["order_ledger"][0])

    with pytest.raises(RuntimeError, match=r"account order|order shares|partially filled"):
        load_account(_write_account(tmp_path, payload))


def _state_with_fill() -> AccountState:
    state = _state_with_open_order()
    state.pending_orders = []
    order = state.order_ledger[0]
    order.requested_shares = 100
    order.filled_shares = 100
    order.status = OrderStatus.FILLED.value
    order.last_update_date = "2026-01-07"
    state.fills = [
        Fill(
            signal_date=order.signal_date,
            fill_date="2026-01-07",
            symbol=order.symbol,
            side=order.side,
            shares=100,
            price=10.0,
            gross_value=1_000.0,
            commission=5.0,
            stamp_duty=0.0,
            transfer_fee=0.1,
            slippage_cost=0.2,
            reason=order.reason,
            lifecycle=order.lifecycle,
            order_id=order.order_id,
            fill_id="fill-1",
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )
    ]
    return state


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fill_date", "2026-01-06"),
        ("side", "HOLD"),
        ("lifecycle", "UNKNOWN"),
        ("shares", 0),
        ("shares", 1.5),
        ("price", -1.0),
        ("gross_value", 999.0),
        ("commission", -0.01),
        ("slippage_cost", "0.2"),
        ("reason_code", ""),
    ],
)
def test_account_loader_strictly_validates_fill_economics(tmp_path, field, value):
    payload = _state_with_fill().to_dict()
    payload["fills"][0][field] = value

    with pytest.raises(RuntimeError, match="fill"):
        load_account(_write_account(tmp_path, payload))


def test_schema_v3_linked_sell_fill_requires_exact_lot_attribution(tmp_path):
    payload = _state_with_fill().to_dict()
    payload["order_ledger"][0]["side"] = "SELL"
    payload["fills"][0]["side"] = "SELL"
    payload["fills"][0]["stamp_duty"] = 1.0
    payload["fills"][0]["sold_tranches"] = []

    with pytest.raises(RuntimeError, match="linked sell fill sold-lot attribution"):
        load_account(_write_account(tmp_path, payload))


def _valid_broker_snapshot() -> dict[str, Any]:
    return {
        "as_of": "2026-01-07",
        "cash": 1_998_994.9,
        "positions": [
            {
                "symbol": "300308",
                "shares": 100,
                "sellable_shares": 0,
                "avg_cost": 10.051,
            }
        ],
        "fills": [
            {
                "fill_id": "broker-fill-1",
                "order_id": "O000000001",
                "fill_date": "2026-01-07",
                "symbol": "300308",
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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda fill, snapshot: fill.update(fill_date="2026-01-06"),
        lambda fill, snapshot: fill.update(fill_date="2026-01-08"),
        lambda fill, snapshot: fill.update(gross_value=999.0),
        lambda fill, snapshot: fill.update(shares=100.5),
        lambda fill, snapshot: fill.update(remaining_shares=-1),
        lambda fill, snapshot: fill.update(final="yes"),
    ],
)
def test_broker_rejects_noncausal_or_inconsistent_fill(
    mutate: Callable[[dict[str, Any], dict[str, Any]], None],
):
    account = _state_with_open_order()
    before = copy.deepcopy(account.to_dict())
    snapshot = _valid_broker_snapshot()
    mutate(snapshot["fills"][0], snapshot)

    with pytest.raises(ValueError, match=r"broker fill|broker field"):
        sync_broker_snapshot(account, snapshot)
    assert account.to_dict() == before


def test_broker_accepts_cent_level_gross_rounding_and_rejects_fill_id_reuse():
    account = _state_with_open_order()
    snapshot = _valid_broker_snapshot()
    snapshot["fills"][0]["gross_value"] = 1_000.005
    sync_broker_snapshot(account, snapshot)

    changed = copy.deepcopy(snapshot)
    changed["fills"][0]["price"] = 10.01
    changed["fills"][0]["gross_value"] = 1_001.0
    with pytest.raises(ValueError, match="fill_id was reused"):
        sync_broker_snapshot(account, changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shares", 100.5),
        ("shares", True),
        ("sellable_shares", 0.5),
        ("avg_cost", "10.051"),
        ("avg_cost", True),
        ("highest_close", "10.50"),
        ("entry_date", 20260107),
    ],
)
def test_broker_strictly_validates_authoritative_position_fields(field, value):
    account = _state_with_open_order()
    before = copy.deepcopy(account.to_dict())
    snapshot = _valid_broker_snapshot()
    snapshot["positions"][0][field] = value

    with pytest.raises(ValueError, match=r"broker field|broker position"):
        sync_broker_snapshot(account, snapshot)
    assert account.to_dict() == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(protected_weights={"sz300308": -0.01}),
        lambda payload: payload.update(operating_peak="NaN"),
        lambda payload: payload.update(risk_signal_state={"transition_damage": "NaN"}),
        lambda payload: payload.update(risk="UNKNOWN"),
        lambda payload: payload.update(shock_state="UNKNOWN"),
        lambda payload: payload.update(capital_budget_level=-1),
    ],
)
def test_account_loader_rejects_invalid_strategy_risk_state(
    tmp_path,
    mutate: Callable[[dict[str, Any]], None],
):
    payload = AccountState.empty(2_000_000.0).to_dict()
    payload.update(data_hash="data", code_hash="code")
    mutate(payload)

    with pytest.raises(RuntimeError):
        load_account(_write_account(tmp_path, payload))


@pytest.mark.parametrize(
    ("exit_bands", "active_bands"),
    [
        ({"sz300308": [0.1, 0.1]}, {}),
        ({"sz300308": [0.1, 0.1]}, {"sz300308": [False]}),
        ({"sz300308": [0.1]}, {"sz300308": [0]}),
    ],
)
def test_account_loader_rejects_inconsistent_strategic_bands(
    tmp_path,
    exit_bands,
    active_bands,
):
    payload = AccountState.empty(2_000_000.0).to_dict()
    payload.update(
        data_hash="data",
        code_hash="code",
        strategic_cohort_symbols=["sz300308"],
        strategic_exit_bands=exit_bands,
        strategic_active_bands=active_bands,
    )

    with pytest.raises(RuntimeError, match="strategic"):
        load_account(_write_account(tmp_path, payload))


def test_save_rejects_nan_state_without_replacing_existing_file(tmp_path):
    destination = tmp_path / "durable.json"
    destination.write_text("durable-before", encoding="utf-8")
    state = AccountState.empty(2_000_000.0)
    state.operating_peak = float("nan")

    with pytest.raises(RuntimeError, match="operating_peak"):
        save_account(state, destination)
    assert destination.read_text(encoding="utf-8") == "durable-before"

    state.operating_peak = state.initial_cash
    state.risk_events = [{"date": "2026-01-01", "unvalidated_metric": float("nan")}]
    with pytest.raises(ValueError, match="Out of range float"):
        save_account(state, destination)
    assert destination.read_text(encoding="utf-8") == "durable-before"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["order_ledger"][0].update(
            requested_shares=99,
            filled_shares=99,
        ),
        lambda payload: payload.update(fills=[]),
        lambda payload: payload["order_ledger"][0].update(
            requested_shares=90,
            filled_shares=100,
        ),
    ],
)
def test_account_loader_closes_ledger_against_fills(
    tmp_path,
    mutate: Callable[[dict[str, Any]], None],
):
    payload = _state_with_fill().to_dict()
    mutate(payload)

    with pytest.raises(RuntimeError, match=r"requested shares|reconcile to fills|requires"):
        load_account(_write_account(tmp_path, payload))


def test_broker_rejects_new_fill_for_terminal_order_and_requested_overfill():
    account = _state_with_open_order()
    snapshot = _valid_broker_snapshot()
    sync_broker_snapshot(account, snapshot)

    terminal = copy.deepcopy(snapshot)
    terminal["fills"][0]["fill_id"] = "broker-fill-2"
    with pytest.raises(ValueError, match="terminal account order"):
        sync_broker_snapshot(account, terminal)

    account = _state_with_open_order()
    account.order_ledger[0].requested_shares = 50
    account.order_ledger[0].remaining_shares = 50
    account.pending_orders[0].remaining_shares = 50
    before = copy.deepcopy(account.to_dict())
    with pytest.raises(ValueError, match="requested order shares"):
        sync_broker_snapshot(account, _valid_broker_snapshot())
    assert account.to_dict() == before


def test_broker_sync_reuses_strategy_risk_state_validation():
    account = AccountState.empty(2_000_000.0)
    account.protected_weights = {"sz300308": -0.01}
    before = copy.deepcopy(account.to_dict())

    with pytest.raises(RuntimeError, match="protected_weights"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-07",
                "cash": 2_000_000.0,
                "positions": [],
                "fills": [],
            },
        )
    assert account.to_dict() == before


def test_schema_v2_order_and_fill_metadata_remain_migratable(tmp_path):
    state = _state_with_fill()
    state.schema_version = 2
    state.next_order_sequence = 2
    payload = state.to_dict()
    for order in payload["order_ledger"]:
        for field in (
            "reduction_policy",
            "reason_code",
            "exit_kind",
            "entry_score",
            "entry_confidence",
            "entry_regime",
            "entry_industry_strength",
        ):
            order.pop(field)
    for fill in payload["fills"]:
        for field in ("reduction_policy", "reason_code", "exit_kind", "sold_tranches"):
            fill.pop(field)
        # Old simulated fills legitimately predate broker-visible order IDs.
        fill["order_id"] = ""
    path = _write_account(tmp_path, payload, "legacy-v2.json")

    migrated = migrate_account(
        path,
        path,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )

    assert migrated.schema_version == 3
    assert load_account(path).fills[0].order_id == ""


@pytest.fixture
def schema_v2_linked_sell_payload() -> dict[str, Any]:
    """Match schema-v2 execution output: linked SELL, without v3 lot attribution."""
    state = _state_with_fill()
    state.schema_version = 2
    state.order_ledger[0].side = "SELL"
    state.fills[0].side = "SELL"
    state.fills[0].fill_id = ""
    state.fills[0].stamp_duty = 1.0
    payload = state.to_dict()
    for order in payload["order_ledger"]:
        for field in (
            "reduction_policy",
            "reason_code",
            "exit_kind",
            "entry_score",
            "entry_confidence",
            "entry_regime",
            "entry_industry_strength",
        ):
            order.pop(field)
    for fill in payload["fills"]:
        for field in ("reduction_policy", "reason_code", "exit_kind", "sold_tranches"):
            fill.pop(field)
        assert fill["order_id"]
    return payload


def test_schema_v2_linked_sell_gets_auditable_degraded_attribution(
    tmp_path,
    schema_v2_linked_sell_payload,
):
    path = _write_account(tmp_path, schema_v2_linked_sell_payload, "linked-sell-v2.json")

    with pytest.raises(RuntimeError, match="requires explicit migration"):
        load_account(path)

    legacy = load_account(path, allow_legacy_schema=True)
    assert legacy.fills[0].sold_tranches == []

    migrated = migrate_account(
        path,
        path,
        new_code_hash="schema-v3-code",
        acknowledge_code_change=True,
    )

    assert migrated.schema_version == 3
    assert migrated.fills[0].sold_tranches == [
        {
            "tranche_id": "legacy-v2-unattributed:O000000001:2026-01-07:1",
            "lifecycle": "CORE",
            "shares": 100,
            "attribution_quality": "degraded_schema_v2_missing_sold_tranches",
            "source_schema": 2,
        }
    ]
    audit = migrated.account_migrations[-1]["degraded_sell_attribution"]
    assert audit["policy"] == "synthetic_single_lot_exact_share_backfill"
    assert audit["fills"] == [
        {
            "fill_id": "",
            "order_id": "O000000001",
            "symbol": "sz300308",
            "fill_date": "2026-01-07",
            "shares": 100,
        }
    ]

    reloaded = load_account(path)
    assert reloaded.schema_version == 3
    assert reloaded.fills[0].sold_tranches == migrated.fills[0].sold_tranches
