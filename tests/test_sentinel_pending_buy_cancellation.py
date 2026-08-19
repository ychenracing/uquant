from __future__ import annotations

import pandas as pd
import pytest

from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, reconcile_account_orders
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _identity(*, side: str, target_weight: float | None = None) -> dict[str, str | None]:
    origin = (
        OriginSubsystem.LEADER.value
        if side == "BUY"
        else OriginSubsystem.RISK.value
    )
    mechanism = (
        AttributionMechanism.LEADER_SELECTION.value
        if side == "BUY"
        else AttributionMechanism.RISK_OFF.value
    )
    fields: dict[str, str | None] = {
        "origin_subsystem": origin,
        "mechanism": mechanism,
        "origin_lifecycle": "CORE",
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    fields["event_id"] = derive_attribution_event_id(
        signal_date="2026-08-18",
        symbol="sz300308",
        target_weight=(0.50 if target_weight is None else target_weight) if side == "BUY" else 0.0,
        lifecycle="CORE",
        origin_lifecycle="CORE",
        origin_subsystem=origin,
        mechanism=mechanism,
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy="FIFO",
        reason_code="strategy_target",
        exit_kind="strategy",
    )
    return fields


def _pending(*, order_id: str = "", remaining: int = 0) -> PendingOrder:
    return PendingOrder(
        signal_date="2026-08-18",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="confirmed entry",
        lifecycle="CORE",
        remaining_shares=remaining,
        order_id=order_id,
        **_identity(side="BUY"),
    )


def _ledger(*, status: str, filled: int = 0, remaining: int = 0) -> AccountOrder:
    return AccountOrder(
        order_id="O000000001",
        signal_date="2026-08-18",
        submitted_date="2026-08-18",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="confirmed entry",
        lifecycle="CORE",
        status=status,
        requested_shares=filled + remaining,
        filled_shares=filled,
        remaining_shares=remaining,
        **_identity(side="BUY"),
    )


def test_unsubmitted_buy_is_cancelled_with_stable_sentinel_reason() -> None:
    account = AccountState.empty(2_000_000.0)
    previous = [_pending()]

    current = reconcile_account_orders(
        account=account,
        previous=previous,
        current=(),
        submitted_date="2026-08-19",
        removed_buy_reason="sentinel_freeze_new_risk",
    )

    assert current == ()
    assert account.order_ledger[0].status == OrderStatus.CANCELLED.value
    assert account.order_ledger[0].cancel_reason == "sentinel_freeze_new_risk"
    assert account.order_ledger[0].last_event == OrderStatus.CANCELLED.value


@pytest.mark.parametrize(
    ("status", "filled", "remaining"),
    (
        (OrderStatus.SUBMITTED.value, 0, 500),
        (OrderStatus.OPEN.value, 0, 500),
        (OrderStatus.PARTIALLY_FILLED.value, 200, 300),
    ),
)
def test_submitted_buy_records_cancel_request_without_faking_broker_confirmation(
    status: str,
    filled: int,
    remaining: int,
) -> None:
    ledger = _ledger(status=status, filled=filled, remaining=remaining)
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = [ledger]
    account.next_order_sequence = 2
    previous = [_pending(order_id=ledger.order_id, remaining=remaining)]

    current = reconcile_account_orders(
        account=account,
        previous=previous,
        current=(),
        submitted_date="2026-08-19",
        removed_buy_reason="sentinel_freeze_new_risk",
    )

    assert current == ()
    assert ledger.status == status
    assert ledger.filled_shares == filled
    assert ledger.remaining_shares == remaining
    assert ledger.cancel_reason == "sentinel_freeze_new_risk"
    assert ledger.last_event == "CANCEL_REQUESTED"

    account.pending_orders = list(current)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-08-20"),
        account=account,
        panel={},
    )
    assert fills == []
    assert account.pending_orders == []


def test_legacy_submitted_cancel_requested_buy_is_never_executed() -> None:
    ledger = _ledger(status=OrderStatus.SUBMITTED.value, remaining=500)
    ledger.cancel_reason = "sentinel_freeze_new_risk"
    ledger.last_event = "CANCEL_REQUESTED"
    account = AccountState.empty(2_000_000.0)
    pending = _pending(order_id=ledger.order_id, remaining=500)
    account.order_ledger = [ledger]
    account.pending_orders = [pending]
    account.next_order_sequence = 2

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-08-20"),
        account=account,
        panel={"sz300308": pd.DataFrame()},
    )

    assert fills == []
    assert account.pending_orders == [pending]
    assert ledger.status == OrderStatus.SUBMITTED.value


def test_broker_cancel_confirmation_retires_cancel_requested_buy() -> None:
    ledger = _ledger(status=OrderStatus.OPEN.value, remaining=500)
    ledger.cancel_reason = "sentinel_freeze_new_risk"
    ledger.last_event = "CANCEL_REQUESTED"
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = [ledger]
    account.pending_orders = []
    account.next_order_sequence = 2

    result = sync_broker_snapshot(
        account,
        {
            "as_of": "2026-08-20",
            "cash": 2_000_000.0,
            "positions": [],
            "fills": [],
            "orders": [
                {
                    "order_id": ledger.order_id,
                    "status": OrderStatus.CANCELLED.value,
                    "remaining_shares": 0,
                }
            ],
        },
    )

    assert account.pending_orders == []
    assert account.order_ledger[0].status == OrderStatus.CANCELLED.value
    assert account.order_ledger[0].last_event == "BROKER_CANCELLED"
    assert result["pending_orders"] == 0


def test_cancel_pending_buy_blocks_replacement_until_broker_terminal_status() -> None:
    ledger = _ledger(status=OrderStatus.OPEN.value, remaining=500)
    ledger.cancel_reason = "sentinel_freeze_new_risk"
    ledger.last_event = "CANCEL_REQUESTED"
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = [ledger]
    account.next_order_sequence = 2
    previous = [_pending(order_id=ledger.order_id, remaining=500)]
    replacement = PendingOrder(
        signal_date="2026-08-18",
        symbol="sz300308",
        side="BUY",
        target_weight=0.60,
        reason="new post-freeze target",
        lifecycle="CORE",
        remaining_shares=500,
        **_identity(side="BUY", target_weight=0.60),
    )

    current = reconcile_account_orders(
        account=account,
        previous=previous,
        current=(replacement,),
        submitted_date="2026-08-21",
    )

    assert current == ()
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].last_event == "CANCEL_REQUESTED"


def test_cancel_pending_buy_does_not_block_same_symbol_independent_sell() -> None:
    ledger = _ledger(status=OrderStatus.PARTIALLY_FILLED.value, filled=200, remaining=300)
    ledger.cancel_reason = "sentinel_freeze_new_risk"
    ledger.last_event = "CANCEL_REQUESTED"
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = [ledger]
    account.next_order_sequence = 2
    previous = [_pending(order_id=ledger.order_id, remaining=300)]
    sell = PendingOrder(
        signal_date="2026-08-18",
        symbol="sz300308",
        side="SELL",
        target_weight=0.0,
        reason="independent lifecycle exit",
        lifecycle="CORE",
        **_identity(side="SELL"),
    )

    current = reconcile_account_orders(
        account=account,
        previous=previous,
        current=(sell,),
        submitted_date="2026-08-21",
        removed_buy_reason="sentinel_freeze_new_risk",
    )

    assert current == (sell,)
    assert account.order_ledger[0].status == OrderStatus.PARTIALLY_FILLED.value
    assert account.order_ledger[0].last_event == "CANCEL_REQUESTED"
    assert account.order_ledger[1].side == "SELL"


def test_sentinel_cancellation_policy_never_blocks_a_sell() -> None:
    sell = PendingOrder(
        signal_date="2026-08-18",
        symbol="sz300308",
        side="SELL",
        target_weight=0.0,
        reason="independent exit",
        lifecycle="CORE",
        **_identity(side="SELL"),
    )
    account = AccountState.empty(2_000_000.0)

    current = reconcile_account_orders(
        account=account,
        previous=[sell],
        current=(sell,),
        submitted_date="2026-08-19",
        removed_buy_reason="sentinel_freeze_new_risk",
    )

    assert current == (sell,)
    assert account.order_ledger[0].side == "SELL"
    assert account.order_ledger[0].cancel_reason == ""
