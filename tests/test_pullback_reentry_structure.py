"""A new pullback policy requires repaired trend structure after a real lifecycle exit."""
from dataclasses import replace

import pandas as pd
import pytest
from test_ordinary_pullback_execution import _submitted
from test_ordinary_pullback_lifecycle import _bar, _ordinary_target, _plan

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.ordinary_pullback import current_pullback_proof
from uquant.risk.pullback import authorize_pullback_entry


def _closed(*, mechanism="LEADER_LIFECYCLE_EXIT",
            reason="leader lifecycle exit: confirmed structural deterioration"):
    _, account, _, day, panel, leaders, risk = _submitted()
    symbol = next(iter(leaders))
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    fills = executor.execute_open(date=day, account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY"
    assert not account.pending_orders
    frame = panel[symbol]
    price = float(frame.loc[day, "close"])
    target = replace(_ordinary_target(symbol, 0.), mechanism=mechanism,
                     reason=reason, reason_code="lifecycle_exit")
    orders = _plan(account, day, panel, (target,))
    assert len(orders) == 1
    sale_day = day + pd.offsets.BDay(1)
    _bar(frame, sale_day, price)
    frame.loc[sale_day, ["ma20", "ma60"]] = price * 1.1
    sells = executor.execute_open(date=sale_day, account=account, panel=panel)
    assert len(sells) == 1 and sells[0].side == "SELL"
    assert sells[0].mechanism == mechanism
    assert sells[0].event_id == orders[0].event_id
    assert sells[0].order_id == orders[0].order_id
    assert not account.positions and not account.pending_orders
    return account, sale_day, panel, leaders, risk


def _permission(account, day, panel, leaders, risk):
    symbol = next(iter(leaders))
    proof = current_pullback_proof(symbol=symbol, date=day, frame=panel[symbol],
                                   leader=leaders[symbol], cfg=DEFAULT_CONFIG)
    assert proof["block"] == "READY"
    return authorize_pullback_entry(date=day, risk=risk, account=account,
                                    user_panel=panel, leaders=leaders, cfg=DEFAULT_CONFIG)


def test_real_lifecycle_exit_blocks_immediate_weak_pullback_reentry():
    account, day, panel, leaders, risk = _closed()
    assert "ordinary_pullback_permission" not in _permission(account, day, panel, leaders, risk).evidence


@pytest.mark.parametrize("repair", ("before-exit", "future", "nan-ma20", "missing-ma60", "nonpositive-ma60"))
def test_missing_or_noncausal_repair_cannot_reauthorize_pullback(repair):
    account, day, panel, leaders, risk = _closed()
    frame = panel[next(iter(leaders))]
    price = float(frame.loc[day, "close"])
    if repair == "before-exit":
        frame.loc[frame.index < day, ["ma20", "ma60"]] = price * .9
    elif repair == "future":
        later = day + pd.offsets.BDay(1)
        _bar(frame, later, price)
        frame.loc[later, ["ma20", "ma60"]] = price * .9
    elif repair == "nan-ma20":
        frame.loc[day, ["ma20", "ma60"]] = [float("nan"), price * .9]
    elif repair == "missing-ma60":
        frame.drop(columns="ma60", inplace=True)
    else:
        frame.loc[day, ["ma20", "ma60"]] = [price * .9, 0.]
    assert "ordinary_pullback_permission" not in _permission(account, day, panel, leaders, risk).evidence


@pytest.mark.parametrize("historical", (False, True))
def test_real_exit_session_or_later_repair_can_support_a_new_pullback(historical):
    account, day, panel, leaders, risk = _closed()
    frame = panel[next(iter(leaders))]
    price = float(frame.loc[day, "close"])
    frame.loc[day, ["ma20", "ma60"]] = price
    if historical:
        later = day + pd.offsets.BDay(1)
        _bar(frame, later, price)
        frame.loc[later, ["ma20", "ma60"]] = price * 1.1
        day = later
    assert "ordinary_pullback_permission" in _permission(account, day, panel, leaders, risk).evidence


def test_other_exit_mechanism_does_not_acquire_lifecycle_reentry_gate():
    account, day, panel, leaders, risk = _closed(mechanism="LEADER_SELECTION")
    assert "ordinary_pullback_permission" in _permission(account, day, panel, leaders, risk).evidence


@pytest.mark.parametrize("reason", (
    "ordinary long-pullback confirmed MA120 deterioration",
    "ordinary long-pullback disaster loss against actual average cost",
))
def test_other_lifecycle_reasons_do_not_acquire_short_structure_reentry_gate(reason):
    account, day, panel, leaders, risk = _closed(reason=reason)
    assert "ordinary_pullback_permission" in _permission(account, day, panel, leaders, risk).evidence


def test_latest_real_exit_requires_new_repair_after_its_own_fill():
    account, day, panel, leaders, risk = _closed()
    symbol = next(iter(leaders))
    frame = panel[symbol]
    price = float(frame.loc[day, "close"])
    frame.loc[day, ["ma20", "ma60"]] = price
    assert "ordinary_pullback_permission" in _permission(account, day, panel, leaders, risk).evidence
    # Native execution-only round trip establishes a later exit boundary.
    _plan(account, day, panel, (_ordinary_target(symbol, .2),))
    bought_day = day + pd.offsets.BDay(1)
    _bar(frame, bought_day, price)
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    assert executor.execute_open(date=bought_day, account=account, panel=panel)[0].side == "BUY"
    target = replace(_ordinary_target(symbol, 0.), mechanism="LEADER_LIFECYCLE_EXIT",
                     reason="leader lifecycle exit: confirmed structural deterioration",
                     reason_code="lifecycle_exit")
    _plan(account, bought_day, panel, (target,))
    sold_day = bought_day + pd.offsets.BDay(1)
    _bar(frame, sold_day, price)
    frame.loc[sold_day, ["ma20", "ma60"]] = price * 1.1
    assert executor.execute_open(date=sold_day, account=account, panel=panel)[0].side == "SELL"
    assert not account.positions
    assert "ordinary_pullback_permission" not in _permission(account, sold_day, panel, leaders, risk).evidence


@pytest.mark.parametrize("corruption", (
    "duplicate-order-other-event", "empty-order", "empty-event",
    "event", "symbol", "side", "mechanism", "reason-code", "reason",
))
def test_real_sell_identity_conflicts_fail_closed_before_reason_classification(corruption):
    account, day, panel, leaders, risk = _closed()
    # Repair is present: these negatives isolate identity, not the price gate.
    frame = panel[next(iter(leaders))]
    frame.loc[day, ["ma20", "ma60"]] = float(frame.loc[day, "close"])
    fill = account.fills[-1]
    order = next(order for order in account.order_ledger if order.order_id == fill.order_id)
    if corruption == "duplicate-order-other-event":
        account.order_ledger.append(replace(order, event_id="conflicting-event"))
    elif corruption == "empty-order":
        account.fills[-1] = replace(fill, order_id="")
    elif corruption == "empty-event":
        account.fills[-1] = replace(fill, event_id="")
    elif corruption == "event":
        account.fills[-1] = replace(fill, event_id="conflicting-event")
    elif corruption == "symbol":
        account.order_ledger[account.order_ledger.index(order)] = replace(order, symbol="other-symbol")
    elif corruption == "side":
        account.order_ledger[account.order_ledger.index(order)] = replace(order, side="BUY")
    elif corruption == "mechanism":
        account.fills[-1] = replace(fill, mechanism="LEADER_SELECTION")
    elif corruption == "reason-code":
        account.fills[-1] = replace(fill, reason_code="strategy_target")
    else:
        account.fills[-1] = replace(fill, reason="ordinary long-pullback confirmed MA120 deterioration")
    assert "ordinary_pullback_permission" not in _permission(account, day, panel, leaders, risk).evidence
