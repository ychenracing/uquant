from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_attribution_identity import (
    _assert_reconcile_rejection_is_byte_atomic,
    _identity,
)

from uquant import types as domain
from uquant.execution import (
    reconcile_account_orders,
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
