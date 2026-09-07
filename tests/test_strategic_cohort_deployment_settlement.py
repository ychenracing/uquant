"""Actual completed FULL deployment is distinct from close-to-target weights."""
from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_shared_core_qualification import _decide
from test_strategic_grant_observation import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState

SYMBOLS = ("sz300308", "sz300502", "sz300394")


def _native_full(*, execution="complete"):
    dates = pd.bdate_range("2023-01-02", periods=280)
    panel = {symbol: _strategic_frame(dates) for symbol in SYMBOLS}
    for frame in panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * .99
        frame["volume"] = 100_000_000.
    leaders = {symbol: _leader(symbol, .96 - index * .01, industry="optical")
               for index, symbol in enumerate(SYMBOLS)}
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=SYMBOLS,
        qualification_reference_symbols=SYMBOLS,
        risk_reference_symbols=("sh000300", "sh000682"),
        industries=dict.fromkeys(SYMBOLS, "optical"),
        available_symbols=(*SYMBOLS, "sh000300", "sh000682"),
    )
    account = AccountState.empty(2_000_000.)
    account.account_identity = "account:full-native-settlement"
    account.code_hash, account.data_hash = "source:regression", "data:regression"
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for index in range(240, 250):
        _decide(policy, account, dates[index], panel, leaders, roles, risk=_risk(frozen=False))
        if account.pending_orders:
            break
    assert len(account.pending_orders) == 3
    assert account.strategic_grant.qualification_quorum == "FULL_COHORT"
    assert account.candidate_tenure.get("strategic_cohort_started", 0) == 0
    fill_day = dates[index + 1]
    if execution != "pending":
        if execution == "partial":
            panel[SYMBOLS[-1]].loc[fill_day, "volume"] = 100_000.
        cfg = DEFAULT_CONFIG.override(max_volume_participation=.002) if execution == "partial" else DEFAULT_CONFIG
        fills = ExecutionPlanner(cfg).execute_open(date=fill_day, account=account, panel=panel)
        assert len(fills) == 3 and all(fill.shares > 0 for fill in fills)
        if execution == "complete":
            assert all(order.status == "FILLED" and order.remaining_shares == 0 for order in account.order_ledger)
            assert not account.pending_orders
        else:
            assert any(order.status == "PARTIALLY_FILLED" for order in account.order_ledger)
    # A healthy price rise in one member makes the other two close weights fall
    # below .95*target even when every requested initial share really settled.
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        price = float(panel[SYMBOLS[0]].loc[fill_day, "close"]) * 1.5
        panel[SYMBOLS[0]].loc[fill_day:, column] = price * factor
    return policy, account, dates[index + 1:], panel, leaders, roles


def test_all_actual_initial_fills_settle_deployment_despite_unequal_close_weights():
    policy, account, dates, panel, leaders, roles = _native_full()
    original_shares = {symbol: position.shares for symbol, position in account.positions.items()}
    original_ids = [order.order_id for order in account.order_ledger]
    targets = _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    assert not account.pending_orders
    assert len(targets) == 3
    account = account_from_dict(asdict(account))
    for date in dates[1:4]:
        _decide(policy, account, date, panel, leaders, roles, risk=_risk(frozen=False))
        assert not account.pending_orders
    assert [order.order_id for order in account.order_ledger] == original_ids
    assert {symbol: position.shares for symbol, position in account.positions.items()} == original_shares


@pytest.mark.parametrize("unsettled", ("pending", "partial", "cancelled", "fake_filled"))
def test_missing_actual_full_deployment_cannot_gain_settlement_marker(unsettled):
    execution = unsettled if unsettled in {"pending", "partial"} else "pending"
    policy, account, dates, panel, leaders, roles = _native_full(execution=execution)
    if unsettled == "cancelled":
        from uquant.broker import sync_broker_snapshot

        sync_broker_snapshot(account, {
            "as_of": str(dates[0].date()), "cash": account.cash, "fills": [], "positions": [],
            "orders": [{"order_id": order.order_id, "status": "CANCELLED", "remaining_shares": 0}
                       for order in account.pending_orders],
        }, cfg=DEFAULT_CONFIG)
        assert not account.fills and not account.positions
    elif unsettled == "fake_filled":
        account.pending_orders.clear()
        for order in account.order_ledger:
            order.status = "FILLED"
            order.remaining_shares = 0
        assert not account.fills
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=True))
    assert account.candidate_tenure.get("strategic_cohort_started", 0) == 0


def test_completed_deployment_still_obeys_lower_current_risk_cap():
    policy, account, dates, panel, leaders, roles = _native_full()
    risk = replace(_risk(frozen=True), target_gross_cap=.3)
    targets = _decide(policy, account, dates[0], panel, leaders, roles, risk=risk)
    assert sum(target.weight for target in targets) <= .3 + 1e-12
    assert any(order.side == "SELL" for order in account.pending_orders)
    assert not any(order.side == "BUY" for order in account.pending_orders)


def test_completed_deployment_still_executes_new_atr_exit():
    from types import SimpleNamespace

    from uquant.application.decision import mark_account_positions

    policy, account, dates, panel, leaders, roles = _native_full()
    runtime = SimpleNamespace(_raw=panel,
                              _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]))
    mark_account_positions(runtime, account, dates[0])
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    assert not account.pending_orders
    symbol = SYMBOLS[0]
    peak = account.positions[symbol].highest_close
    price = peak - 1.
    for column in ("open", "close", "high", "low"):
        panel[symbol].loc[dates[1]:, column] = price
    panel[symbol].loc[dates[1]:, "ma20"] = peak
    panel[symbol].loc[dates[1]:, "ret20"] = -.10
    # Existing .01 daily ATR steps accumulate before the unchanged .05 order
    # threshold permits a native trade; deployment settlement must not stop it.
    for index, date in enumerate(dates[1:12], 1):
        _decide(policy, account, date, panel, leaders, roles, risk=_risk(frozen=False))
        sells = [order for order in account.pending_orders if order.side == "SELL" and order.symbol == symbol]
        if sells:
            assert len(sells) == 1 and sells[0].mechanism == "STRATEGIC_TRAILING_EXIT"
            fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[index + 1], account=account, panel=panel)
            assert any(fill.symbol == symbol and fill.side == "SELL" and fill.shares > 0 for fill in fills)
            break
    else:
        pytest.fail("the unchanged ATR instruction never produced its native SELL")


@pytest.mark.parametrize("corruption", ("missing_peer_fill", "peer_grant", "peer_epoch", "wrong_fill_event"))
def test_deployment_proof_requires_every_members_actual_identity_and_fill(corruption):
    from uquant.portfolio.strategic.grant_lifecycle import completed_strategic_cohort_entry

    _, account, _, _, _, _ = _native_full()
    peer = next(symbol for symbol in SYMBOLS if symbol != account.strategic_grant.candidate_symbol)
    if corruption == "missing_peer_fill":
        account.fills = [fill for fill in account.fills if fill.symbol != peer]
    elif corruption == "peer_grant":
        account.positions[peer].grant_id = account.strategic_grant.grant_id
    elif corruption == "peer_epoch":
        account.positions[peer].epoch_id = "epoch_" + "0" * 64
    else:
        fill = next(fill for fill in account.fills if fill.symbol == peer)
        account.fills[account.fills.index(fill)] = replace(fill, event_id="evt_" + "0" * 64)
    assert not completed_strategic_cohort_entry(account, set(SYMBOLS))


def test_restoration_fill_cannot_substitute_for_initial_cohort_deployment():
    from uquant.portfolio.strategic.grant_lifecycle import completed_strategic_cohort_entry

    _, account, _, _, _, _ = _native_full()
    peer = next(symbol for symbol in SYMBOLS if symbol != account.strategic_grant.candidate_symbol)
    order = next(order for order in account.order_ledger if order.symbol == peer)
    order.mechanism = "STRATEGIC_RESTORATION"
    account.fills = [replace(fill, mechanism="STRATEGIC_RESTORATION") if fill.order_id == order.order_id else fill
                     for fill in account.fills]
    assert not completed_strategic_cohort_entry(account, set(SYMBOLS))


@pytest.mark.parametrize("liability", ("live", "late"))
def test_other_owner_peer_buy_liability_blocks_native_deployment_proof(liability):
    from copy import deepcopy

    from uquant.models.trading import late_strategic_fill_allowed
    from uquant.portfolio.strategic.grant_lifecycle import completed_strategic_cohort_entry

    _, account, _, _, _, _ = _native_full()
    peer = next(symbol for symbol in SYMBOLS if symbol != account.strategic_grant.candidate_symbol)
    old = deepcopy(next(order for order in account.order_ledger if order.symbol == peer))
    old.order_id = "O999999999"
    old.grant_id = "grant_old_distinct_owner"
    old.epoch_id = "epoch_old_distinct_owner"
    old.remaining_shares = 100
    old.status = "OPEN" if liability == "live" else "CANCELLED"
    if liability == "late":
        old.cancel_reason = "strategic partial remainder replaced"
        old.last_event = "CANCELLED"
        assert late_strategic_fill_allowed(old)
    account.order_ledger.append(old)
    # Explicit malformed/mixed-history safety input; not claimed as a fresh
    # native transaction or economic evidence. Its broker responsibility survives.
    assert not completed_strategic_cohort_entry(account, set(SYMBOLS))
