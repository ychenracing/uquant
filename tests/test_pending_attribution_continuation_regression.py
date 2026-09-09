"""A partial SELL's event can survive only with its original native intent."""
from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
import pytest

from uquant.account.codec import account_from_dict
from uquant.account.validation_orders import validate_pending_order_for_account_write
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.types import AccountState, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOL = "sh688008"


def _submit(account, date, target):
    previous = list(account.pending_orders)
    targets = attach_target_attribution(
        "compute", REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=(target,), retained_orders=previous)
    planned = plan_orders(signal_date=str(date.date()), targets=targets, account=account,
                          prices={SYMBOL: 10.}, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned,
                                  targets=targets, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date())))
    return targets


def _partial_sell(reduction_policy):
    dates = pd.bdate_range("2024-01-02", periods=7)
    frame = pd.DataFrame({"open": 10., "close": 10., "high": 10.1, "low": 9.9,
                          "volume": 100_000_000.}, index=dates)
    panel = {SYMBOL: frame}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "source:native-regression", "data:native-regression"
    entry = Target(SYMBOL, .60, "CORE", .9, .9, "native initial CORE",
                   origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE")
    _submit(account, dates[0], entry)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
    risk = reduction_policy == "RISK_PRIORITY"
    exit_target = Target(
        SYMBOL, .30, "CORE", .9, .9, "native partial exit", reduction_policy=reduction_policy,
        reason_code="risk_reduction" if risk else "strategy_target", exit_kind="risk" if risk else "strategy",
        origin_subsystem="RISK" if risk else "LEADER",
        mechanism="RISK_GROSS_CAP" if risk else "LEADER_LIFECYCLE_EXIT", origin_lifecycle="CORE")
    _submit(account, dates[2], exit_target)
    frame.loc[dates[3], "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    assert account.order_ledger[-1].status == "PARTIALLY_FILLED"
    assert account.pending_orders[0].remaining_shares > 0
    return account_from_dict(asdict(account)), dates[4:], panel, exit_target


@pytest.mark.parametrize("policy,weight,carried", (("FIFO", .29, False), ("FIFO", .31, False),
                                                   ("FIFO", 0., True), ("RISK_PRIORITY", .31, False)))
def test_changed_partial_sell_gets_a_new_canonical_event(policy, weight, carried):
    account, dates, panel, target = _partial_sell(policy)
    previous = asdict(account.pending_orders[0])
    if carried:
        target = replace(target, event_id=previous["event_id"],
                         industry_at_entry=previous["industry_at_entry"],
                         industry_manifest_sha256=previous["industry_manifest_sha256"])
    _submit(account, dates[0], replace(target, weight=weight))
    assert len(account.pending_orders) == 1
    order = account.pending_orders[0]
    assert order.order_id != previous["order_id"]
    assert order.event_id != previous["event_id"]
    assert order.signal_date == str(dates[0].date()) and order.target_weight == weight
    validate_pending_order_for_account_write(account, order)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0


@pytest.mark.parametrize("policy,weight", (("FIFO", .30), ("RISK_PRIORITY", .29)))
def test_supported_partial_sell_continuation_retains_the_exact_native_intent(policy, weight):
    account, dates, panel, target = _partial_sell(policy)
    previous = asdict(account.pending_orders[0])
    _submit(account, dates[0], replace(target, weight=weight))
    assert len(account.pending_orders) == 1 and asdict(account.pending_orders[0]) == previous
    validate_pending_order_for_account_write(account, account.pending_orders[0])
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].order_id == previous["order_id"]


def test_qualified_partial_buy_neighbour_keeps_native_order_and_fill_identity():
    dates = pd.bdate_range("2024-01-02", periods=4)
    frame = pd.DataFrame({"open": 10., "close": 10., "high": 10.1, "low": 9.9,
                          "volume": 100_000_000.}, index=dates)
    frame.loc[dates[1], "volume"] = 100_000.
    panel = {SYMBOL: frame}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "source:native-regression", "data:native-regression"
    target = Target(SYMBOL, .60, "CORE", .9, .9, "qualified native buy",
                    origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE")
    _submit(account, dates[0], target)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and account.order_ledger[0].status == "PARTIALLY_FILLED"
    previous = asdict(account.pending_orders[0])
    account = account_from_dict(asdict(account))
    _submit(account, dates[2], replace(target, weight=.61))
    assert len(account.pending_orders) == 1 and asdict(account.pending_orders[0]) == previous
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].order_id == previous["order_id"]
