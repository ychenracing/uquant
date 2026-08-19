from __future__ import annotations

import pytest

from uquant.execution import reconcile_account_orders
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


def _identity(*, side: str) -> dict[str, str | None]:
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
        target_weight=0.50 if side == "BUY" else 0.0,
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
