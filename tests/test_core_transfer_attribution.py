"""Rotation attribution requires its own settled sell, not observation tenure."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest
from test_core_transfer_feasibility import CHALLENGER, INCUMBENT, WEAK, _scenario

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.types import AccountState, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _execution_scenario(monkeypatch, obstruction):
    policy, arguments = _scenario(monkeypatch, obstruction)
    original = arguments["account"]
    dates = arguments["user_panel"][WEAK].index
    account = AccountState.empty(original.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    arguments["account"] = account
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    panel = {
        symbol: pd.DataFrame(
            {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
             "volume": 100_000_000.0, "amount": 1_000_000_000.0},
            index=dates[-13:],
        )
        for symbol in arguments["user_panel"]
    }

    def submit(day, targets, *, retain=()):
        signal = str(day.date())
        attributed = attach_target_attribution(
            "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
            targets=targets, retained_orders=account.pending_orders,
        )
        orders = plan_orders(
            signal_date=signal, targets=attributed, account=account,
            prices=arguments["prices"], cfg=DEFAULT_CONFIG,
        )
        account.pending_orders = list(reconcile_account_orders(
            account=account, previous=account.pending_orders,
            current=(*retain, *orders), submitted_date=signal,
        ))
        return orders

    submit(dates[-13], tuple(
        Target(symbol, position.shares / 100_000.0, "CORE", 0.9, 0.9, "prior core entry",
               origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE")
        for symbol, position in original.positions.items()
    ))
    entry_fills = planner.execute_open(date=dates[-12], account=account, panel=panel)
    assert {fill.symbol for fill in entry_fills} == set(original.positions)
    assert not account.pending_orders
    account.replacement_tenure.update(original.replacement_tenure)
    account.candidate_tenure.update(original.candidate_tenure)
    account.protected_weights.update(original.protected_weights)
    return policy, arguments, dates, planner, panel, submit


@pytest.mark.parametrize("transfer_state", ("rejected", "intent_only", "open_unfilled", "cancelled"))
def test_unsettled_rotation_cannot_label_a_buy_funded_by_another_sell(monkeypatch, transfer_state):
    policy, arguments, dates, planner, panel, submit = _execution_scenario(
        monkeypatch, "cash_shortfall" if transfer_state == "rejected" else "ready",
    )
    account = arguments["account"]
    targets = policy.allocate(**arguments)
    assert CHALLENGER not in {target.symbol for target in targets}
    transfer_key = f"core_transfer:{WEAK}->{CHALLENGER}"
    assert account.replacement_tenure[transfer_key] >= DEFAULT_CONFIG.replacement_confirm_days
    if transfer_state == "rejected":
        assert not account.rotation_dates
        assert not any(target.mechanism == "LEADER_ROTATION" for target in targets)
    else:
        assert next(target for target in targets if target.symbol == WEAK).mechanism == "LEADER_ROTATION"
        if transfer_state in {"open_unfilled", "cancelled"}:
            orders = submit(dates[-3], targets)
            assert [(order.symbol, order.side) for order in orders] == [(WEAK, "SELL")]
        if transfer_state == "cancelled":
            account.pending_orders = list(reconcile_account_orders(
                account=account, previous=account.pending_orders, current=(),
                submitted_date=str(dates[-3].date()),
            ))
            cancelled = account.order_ledger[-1]
            assert cancelled.status == "CANCELLED" and cancelled.filled_shares == 0

    equity = account.cash + sum(position.shares * 10.0 for position in account.positions.values())
    other_source = Target(
        INCUMBENT, account.positions[INCUMBENT].shares * 10.0 / equity - 0.30,
        "CORE", 0.9, 0.9, "independent lifecycle reduction",
        origin_subsystem="LEADER", mechanism="LEADER_LIFECYCLE_EXIT", origin_lifecycle="CORE",
    )
    submit(dates[-3], (other_source,), retain=tuple(account.pending_orders))
    if transfer_state == "open_unfilled":
        panel[WEAK].loc[dates[-2], "volume"] = 0.0
    other_fills = planner.execute_open(date=dates[-2], account=account, panel=panel)
    assert [(fill.symbol, fill.side, fill.mechanism) for fill in other_fills] == [
        (INCUMBENT, "SELL", "LEADER_LIFECYCLE_EXIT"),
    ]
    assert not any(fill.side == "SELL" and fill.mechanism == "LEADER_ROTATION" for fill in account.fills)
    arguments["date"] = dates[-2]
    admitted = policy.allocate(**arguments)
    challenger = next(target for target in admitted if target.symbol == CHALLENGER)
    assert challenger.mechanism == "LEADER_SELECTION"
    assert not challenger.replaces_symbol
    submit(dates[-2], admitted)
    bought = planner.execute_open(date=dates[-1], account=account, panel=panel)
    challenger_fill = next(fill for fill in bought if fill.symbol == CHALLENGER and fill.side == "BUY")
    assert challenger_fill.mechanism == "LEADER_SELECTION"
    assert not challenger_fill.replaces_symbol


def test_settled_rotation_sell_preserves_matching_buy_attribution(monkeypatch):
    policy, arguments, dates, planner, panel, submit = _execution_scenario(monkeypatch, "ready")
    account = arguments["account"]
    targets = policy.allocate(**arguments)
    orders = submit(dates[-3], targets)
    assert [(order.symbol, order.side, order.mechanism) for order in orders] == [
        (WEAK, "SELL", "LEADER_ROTATION"),
    ]
    sold = planner.execute_open(date=dates[-2], account=account, panel=panel)
    assert len(sold) == 1 and sold[0].symbol == WEAK and sold[0].side == "SELL"
    assert sold[0].mechanism == "LEADER_ROTATION" and sold[0].shares > 0
    arguments["date"] = dates[-2]
    admitted = policy.allocate(**arguments)
    challenger = next(target for target in admitted if target.symbol == CHALLENGER)
    assert challenger.mechanism == "LEADER_ROTATION" and challenger.replaces_symbol == WEAK
    submit(dates[-2], admitted)
    bought = planner.execute_open(date=dates[-1], account=account, panel=panel)
    assert len(bought) == 1 and bought[0].symbol == CHALLENGER and bought[0].side == "BUY"
    assert bought[0].mechanism == "LEADER_ROTATION" and bought[0].replaces_symbol == WEAK


@pytest.mark.parametrize("weak_repaired, delayed_cap", ((False, 0.6), (True, 0.7)))
def test_settled_rotation_survives_a_later_rejected_transfer_observation(monkeypatch, weak_repaired, delayed_cap):
    policy, arguments, dates, planner, panel, submit = _execution_scenario(monkeypatch, "ready")
    account = arguments["account"]
    original_risk = arguments["risk"]
    orders = submit(dates[-3], policy.allocate(**arguments))
    assert [(order.symbol, order.side, order.mechanism) for order in orders] == [
        (WEAK, "SELL", "LEADER_ROTATION"),
    ]
    sold = planner.execute_open(date=dates[-2], account=account, panel=panel)
    assert len(sold) == 1 and sold[0].symbol == WEAK and sold[0].side == "SELL"
    assert sold[0].mechanism == "LEADER_ROTATION" and sold[0].shares == 30_000
    cash_after_sale = account.cash
    shares_after_sale = {symbol: position.shares for symbol, position in account.positions.items()}
    transfer_clock = f"core_transfer_session:{WEAK}->{CHALLENGER}"
    original_clock = account.candidate_tenure[transfer_clock]
    assert original_clock == dates[-3].toordinal()

    # The settled sale has not yet funded its challenger. A lower current risk
    # cap blocks admission and another proposed transfer, without undoing the fill.
    weak_frame = arguments["user_panel"][WEAK]
    if weak_repaired:
        weak_frame.loc[dates[-2]:, ["ma20", "ret20"]] = [9.0, 0.10]
    arguments.update(date=dates[-2], risk=replace(original_risk, target_gross_cap=delayed_cap))
    delayed = policy.allocate(**arguments)
    assert CHALLENGER not in {target.symbol for target in delayed}
    evidence = arguments["risk"].evidence["core_allocation"]["symbols"][CHALLENGER]
    assert evidence["budget_checks"][-1]["funded_increment"] < DEFAULT_CONFIG.core_admission_weight
    assert evidence["budget_checks"][-1]["accepted"] is False
    assert evidence["transfer_budget"]["block"] == "TRANSFER_SETTLED_AWAIT_ADMISSION"
    assert submit(dates[-2], delayed) == ()
    assert account.cash == cash_after_sale
    assert {symbol: position.shares for symbol, position in account.positions.items()} == shares_after_sale
    assert account.rotation_dates == [str(dates[-3].date())]
    assert account.candidate_tenure[transfer_clock] == original_clock

    # Recover structure before admitting the challenger so an unrelated third
    # damaged session cannot independently trigger the incumbent lifecycle exit.
    weak_frame.loc[dates[-1], ["ma20", "ret20"]] = [9.0, 0.10]
    arguments.update(date=dates[-1], risk=original_risk)
    admitted = policy.allocate(**arguments)
    challenger = next(target for target in admitted if target.symbol == CHALLENGER)
    assert challenger.mechanism == "LEADER_ROTATION" and challenger.replaces_symbol == WEAK
    assert account.rotation_dates == [str(dates[-3].date())]
    assert account.candidate_tenure[transfer_clock] == original_clock
    submit(dates[-1], admitted)
    next_session = dates[-1] + pd.offsets.BDay()
    for frame in panel.values():
        frame.loc[next_session] = frame.iloc[-1]
    bought = planner.execute_open(date=next_session, account=account, panel=panel)
    assert len(bought) == 1 and bought[0].symbol == CHALLENGER and bought[0].side == "BUY"
    assert bought[0].mechanism == "LEADER_ROTATION" and bought[0].replaces_symbol == WEAK
    assert [fill for fill in account.fills if fill.side == "SELL"] == sold
