"""A carried exit event must keep its registered order's canonical identity."""

from __future__ import annotations

from dataclasses import replace

import pytest

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.types import AccountState, Position, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _registered_exit(*, grant_id=""):
    symbol = "sh688200"
    epoch = "epoch_0f87220827c0bb867965118f9938a261e3924ad80042ff70c9ea81707f3382ae"
    account = AccountState.empty(1_000_000.0)
    account.cash = 640_000.0
    account.positions[symbol] = Position(
        symbol, 3600, 100.0, "2023-04-27", 100.0, grant_id=grant_id, epoch_id=epoch,
    )
    original = Target(
        symbol, 0.0, "CORE", 0.15629853448884778, 1.0, "portfolio crisis gross cap",
        reduction_policy="RISK_PRIORITY", reason_code="crisis", exit_kind="crisis",
        origin_subsystem="RISK", mechanism="CRISIS", origin_lifecycle="CORE", grant_id=grant_id, epoch_id=epoch,
    )
    original = attach_target_attribution(
        "semicap", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2023-05-05", targets=(original,),
    )[0]
    prices = {symbol: 100.0}
    planned = plan_orders(
        signal_date="2023-05-05", targets=(original,), account=account, prices=prices, cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=planned, submitted_date="2023-05-05",
    ))
    retained = account.pending_orders[0]
    assert retained.side == "SELL" and retained.epoch_id == epoch
    assert retained.event_id == "evt_04bd5552806003cd8cf50189195909ebea5e8ea15d452d3404cbbcd0f682efcd"
    return account, original, prices


def test_carried_full_exit_event_retains_registered_epoch_and_signal_date():
    # Minimized from the immutable remove-sz300502 Ownership replay failure on
    # 2023-05-08: the pending sh688200 exit retains a 2023-05-05 event and epoch,
    # but the rebuilt target carries that same event with an empty epoch.
    account, original, prices = _registered_exit()
    retained = account.pending_orders[0]

    rebuilt = replace(original, epoch_id="")
    targets = attach_target_attribution(
        "semicap", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2023-05-08",
        targets=(rebuilt,), retained_orders=account.pending_orders,
    )
    planned = plan_orders(
        signal_date="2023-05-08", targets=targets, account=account, prices=prices, cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders, planned=planned, targets=targets, cfg=DEFAULT_CONFIG,
    )
    continued = reconcile_account_orders(
        account=account, previous=account.pending_orders, current=merged, submitted_date="2023-05-08",
    )

    assert len(continued) == len(account.order_ledger) == 1
    assert continued[0].order_id == retained.order_id
    assert continued[0].signal_date == retained.signal_date == "2023-05-05"
    assert continued[0].event_id == retained.event_id == original.event_id
    assert continued[0].epoch_id == retained.epoch_id == original.epoch_id
    assert account.positions[original.symbol].shares == 3600 and account.cash == 640_000.0
    assert account.fills == []


@pytest.mark.parametrize("field", ("epoch_id", "grant_id"))
def test_same_exit_event_rejects_conflicting_nonempty_ownership(field):
    account, original, _ = _registered_exit(grant_id="grant_" + "a" * 64)
    conflicting = replace(original, **{field: field.removesuffix("_id") + "_" + "f" * 64})
    before = account.to_dict()

    with pytest.raises(RuntimeError, match=f"retained event has conflicting {field}"):
        attach_target_attribution(
            "semicap", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2023-05-08",
            targets=(conflicting,), retained_orders=account.pending_orders,
        )

    assert account.to_dict() == before


def test_independently_attributed_new_exit_cause_supersedes_retained_order():
    account, original, prices = _registered_exit()
    retained = account.pending_orders[0]
    newly_attributed = attach_target_attribution(
        "semicap", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2023-05-08",
        targets=(replace(
            original, event_id="", mechanism="RISK_OFF", reason_code="risk_off", exit_kind="risk_off",
            reason="independently issued risk-off exit",
        ),),
    )[0]
    assert newly_attributed.event_id != retained.event_id
    targets = attach_target_attribution(
        "semicap", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2023-05-08",
        targets=(newly_attributed,), retained_orders=account.pending_orders,
    )
    assert targets == (newly_attributed,)
    planned = plan_orders(
        signal_date="2023-05-08", targets=targets, account=account, prices=prices, cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders, planned=planned, targets=targets, cfg=DEFAULT_CONFIG,
    )
    continued = reconcile_account_orders(
        account=account, previous=account.pending_orders, current=merged, submitted_date="2023-05-08",
    )

    assert len(continued) == 1 and len(account.order_ledger) == 2
    assert continued[0].order_id != retained.order_id
    assert continued[0].signal_date == "2023-05-08"
    assert continued[0].event_id == newly_attributed.event_id
    assert continued[0].mechanism == "RISK_OFF"
    assert account.order_ledger[0].status == "REPLACED"
    assert account.order_ledger[0].replaced_by == continued[0].order_id
    assert account.positions[original.symbol].shares == 3600 and account.cash == 640_000.0
    assert account.fills == []
