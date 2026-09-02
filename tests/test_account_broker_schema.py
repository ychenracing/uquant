from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest

import uquant.account.store as account_store_module
import uquant.infrastructure.atomic_files as atomic_files_module
from uquant.account import load_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Fill,
    Lifecycle,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    ReductionPolicy,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _identity(
    *,
    signal_date: str = "2026-01-05",
    symbol: str = "sz300308",
    target_weight: float = 0.5,
    lifecycle: str = Lifecycle.CORE.value,
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
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
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    return fields


def _state_with_open_order() -> AccountState:
    identity = _identity()
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
        **identity,
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
        **identity,
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
    state.next_order_sequence = 1
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


@pytest.mark.parametrize(
    ("release_session", "release_shares"),
    (
        (123, 1),
        ("2026-01-07", 0),
        ("", 1),
        ("2026-01-04", 1),
        ("2026-01-07", -1),
    ),
)
def test_account_loader_rejects_malformed_remainder_release_evidence(
    tmp_path,
    release_session: object,
    release_shares: int,
) -> None:
    state = _state_with_open_order()
    payload = state.to_dict()
    payload["order_ledger"][0].update(
        remainder_release_session=release_session,
        remainder_release_shares=release_shares,
    )

    with pytest.raises(RuntimeError, match="account order remainder release"):
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
            **_identity(),
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


def test_current_schema_linked_sell_fill_requires_exact_lot_attribution(tmp_path):
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


def test_save_syncs_parent_directory_after_atomic_replace(tmp_path, monkeypatch):
    destination = tmp_path / "durable.json"
    events: list[tuple[str, object]] = []
    original_replace = atomic_files_module.os.replace

    def observed_replace(source, target):
        original_replace(source, target)
        events.append(("replace", target))

    monkeypatch.setattr(atomic_files_module.os, "replace", observed_replace)
    monkeypatch.setattr(
        atomic_files_module,
        "_fsync_directory",
        lambda directory: events.append(("sync-directory", directory)),
        raising=False,
    )

    assert save_account(AccountState.empty(2_000_000.0), destination) is None

    assert events == [
        ("replace", destination),
        ("sync-directory", destination.parent),
    ]


def test_save_preserves_write_error_when_temporary_cleanup_races(tmp_path, monkeypatch):
    state = AccountState.empty(2_000_000.0)
    state.risk_events = [{"date": "2026-01-01", "unvalidated_metric": float("nan")}]
    destination = tmp_path / "nested" / "durable.json"
    cleanup_calls: list[object] = []
    original_unlink = atomic_files_module.os.unlink

    def disappearing_unlink(path):
        cleanup_calls.append(path)
        original_unlink(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(atomic_files_module.os, "unlink", disappearing_unlink)

    with pytest.raises(ValueError, match="Out of range float"):
        save_account(state, destination)

    assert destination.parent.is_dir()
    assert len(cleanup_calls) == 1
    assert not destination.exists()


def test_save_serializes_inside_the_atomic_writer(tmp_path, monkeypatch):
    state = AccountState.empty(2_000_000.0)
    state.risk_events = [{"date": "2026-01-01", "unvalidated_metric": float("nan")}]
    monkeypatch.setattr(
        account_store_module,
        "atomic_write_json_with_mode",
        lambda *args, **kwargs: pytest.fail("invalid JSON reached the atomic writer"),
        raising=False,
    )

    with pytest.raises(pytest.fail.Exception, match="invalid JSON reached"):
        save_account(state, tmp_path / "durable.json")


def test_save_allocates_and_cleans_temporary_before_to_dict_descriptor_failure(
    tmp_path,
    monkeypatch,
):
    events: list[str] = []

    class DescriptorFailureState(AccountState):
        @property
        def to_dict(self):
            events.append("to_dict")
            raise LookupError("descriptor failure")

    original_mkstemp = atomic_files_module.tempfile.mkstemp
    original_unlink = atomic_files_module.os.unlink

    def observed_mkstemp(*args, **kwargs):
        events.append("temporary")
        return original_mkstemp(*args, **kwargs)

    def observed_unlink(path):
        events.append("cleanup")
        return original_unlink(path)

    monkeypatch.setattr(atomic_files_module.tempfile, "mkstemp", observed_mkstemp)
    monkeypatch.setattr(atomic_files_module.os, "unlink", observed_unlink)
    destination = tmp_path / "nested" / "durable.json"

    with pytest.raises(LookupError, match="descriptor failure"):
        save_account(DescriptorFailureState.empty(2_000_000.0), destination)

    assert events == ["temporary", "to_dict", "cleanup"]
    assert destination.parent.is_dir()
    assert not destination.exists()


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
