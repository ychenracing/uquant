"""Actual flatness ends deployed strategic rights; continuous holdings retain them."""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from test_lifecycle_and_risk import _leader
from test_shared_core_qualification import _decide
from test_strategic_cohort_deployment_settlement import SYMBOLS, _native_full
from test_strategic_grant_observation import _risk
from test_strategic_probe_holding import _entry_deteriorated

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.types import Risk


def _deployed(*, mixed=False):
    policy, account, dates, panel, leaders, roles = _native_full()
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    assert not account.pending_orders
    if mixed:
        # Bootstrap a genuinely supported same-industry formation and real fills
        # above. Later current classifications diversify the held book; this is
        # not evidence that a cross-industry established grant was authorized.
        leaders = {symbol: replace(leaders[symbol], industry=industry)
                   for symbol, industry in zip(SYMBOLS, ("optical", "foundry", "equipment"), strict=True)}
        roles = build_strategic_universe_roles(
            as_of=str(dates[1].date()), tradable_symbols=SYMBOLS,
            qualification_reference_symbols=SYMBOLS,
            risk_reference_symbols=("sh000300", "sh000682"),
            industries={symbol: leader.industry for symbol, leader in leaders.items()},
            available_symbols=(*SYMBOLS, "sh000300", "sh000682"),
        )
    return policy, account, dates[1:], panel, _entry_deteriorated(leaders), roles


def _compress(fixture, cap):
    policy, account, dates, panel, leaders, roles = fixture
    before = {symbol: position.shares for symbol, position in account.positions.items()}
    risk = replace(_risk(frozen=True), state=Risk.RISK_OFF if cap == 0 else Risk.CAUTION, target_gross_cap=cap,
                   votes=3, reduction_level=2)
    _decide(policy, account, dates[0], panel, leaders, roles, risk=risk)
    assert account.pending_orders and all(order.side == "SELL" for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "SELL" and fill.shares > 0 for fill in fills)
    assert account.strategic_restore_weights
    if cap == 0:
        assert not account.positions
    else:
        assert account.positions
        assert all(0 < position.shares <= before[symbol]
                   for symbol, position in account.positions.items())
    return policy, account, dates[2:], panel, leaders, roles


def test_native_risk_liquidation_retires_deployed_old_rights_across_restart():
    policy, account, dates, panel, leaders, roles = _compress(_deployed(), 0.)
    old_epoch = account.active_strategic_epoch_id
    assert old_epoch and not account.positions
    for date in dates[:3]:
        account = account_from_dict(asdict(account))
        _decide(policy, account, date, panel, leaders, roles, risk=_risk(frozen=False))
        assert not any(order.side == "BUY" for order in account.pending_orders)
        assert not account.strategic_cohort_targets
        assert not account.strategic_restore_weights
        assert not any(symbol in account.protected_weights for symbol in SYMBOLS)
        assert not account.positions


def test_continuous_native_risk_compression_restores_without_fresh_entry_quality():
    policy, account, dates, panel, leaders, roles = _compress(_deployed(), .60)
    before = {symbol: position.shares for symbol, position in account.positions.items()}
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.pending_orders and all(order.side == "BUY" for order in account.pending_orders)
    assert all(order.mechanism == "STRATEGIC_RESTORATION" for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "BUY" and fill.shares > 0 for fill in fills)
    assert all(account.positions[symbol].shares >= shares for symbol, shares in before.items())
    assert account.candidate_tenure["strategic_cohort_started"] == 1


@pytest.mark.parametrize("execution", ("pending", "partial"))
def test_initial_native_buy_is_not_retired_before_deployment(execution):
    policy, account, dates, panel, leaders, roles = _native_full(execution=execution)
    original = {order.order_id for order in account.pending_orders}
    assert original and account.candidate_tenure.get("strategic_cohort_started", 0) == 0
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert set(account.strategic_cohort_targets) == set(SYMBOLS)
    assert account.pending_orders and all(order.side == "BUY" for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "BUY" and fill.shares > 0 for fill in fills)
    assert set(account.positions) == set(SYMBOLS)


def test_oldest_initial_lot_can_disappear_without_retiring_survivor():
    policy, account, dates, panel, leaders, roles = _native_full(execution="partial")
    for column in ("open", "close", "high", "low", "ma20", "ma60"):
        panel[SYMBOLS[0]].loc[dates[0]:, column] /= 1.5
    original_lots = {symbol: {lot.tranche_id for lot in position.tranches}
                     for symbol, position in account.positions.items()}
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "BUY" for fill in fills)
    _decide(policy, account, dates[1], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    leaders = _entry_deteriorated(leaders)
    leaders[SYMBOLS[-1]] = replace(leaders[SYMBOLS[-1]], score=.60)
    fixture = policy, account, dates[2:], panel, leaders, roles
    policy, account, dates, panel, leaders, roles = _compress(fixture, .10)
    survivors = {symbol for symbol, position in account.positions.items()
                 if not original_lots[symbol] & {lot.tranche_id for lot in position.tranches}}
    assert survivors, "native reduction must consume the old lot while newer shares remain"
    assert any(fill.side == "SELL" and fill.sold_tranches
               for fill in account.fills)
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    restores = {order.symbol for order in account.pending_orders
                if order.side == "BUY" and order.mechanism == "STRATEGIC_RESTORATION"}
    assert restores & survivors
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert any(fill.side == "BUY" and fill.symbol in survivors for fill in fills)


def _reentered_member():

    policy, account, dates, panel, leaders, roles = _compress(_deployed(mixed=True), .60)
    retired = set(SYMBOLS) - set(account.positions)
    assert len(retired) == 1
    symbol = next(iter(retired))
    survivors = {name: position.shares for name, position in account.positions.items()}
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert symbol not in account.strategic_cohort_targets
    # Restore continuously held members first; the former member must compete
    # on new qualification and actual remaining cash, not the old cohort label.
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    leaders[symbol] = _leader(symbol, .99, industry=leaders[symbol].industry)
    for index, date in enumerate(dates[2:14], 2):
        account = account_from_dict(asdict(account))
        risk = _risk(frozen=False)
        risk.evidence.update(broad_ret120=.04, tech_ret120=.04)
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
        orders = [order for order in account.pending_orders if order.symbol == symbol and order.side == "BUY"]
        if orders:
            assert orders[0].mechanism != "STRATEGIC_RESTORATION"
            fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[index + 1], account=account, panel=panel)
            assert any(fill.symbol == symbol and fill.side == "BUY" for fill in fills)
            assert all(account.positions[name].shares >= shares for name, shares in survivors.items())
            return policy, account, dates[index + 2:], panel, leaders, roles, symbol
    pytest.fail("fresh qualified retired member never received a native CORE order")


def test_retired_member_with_current_qualification_can_reenter_beside_survivors():
    _reentered_member()


def _submit_exit_targets(account, date, panel, *, keep, retained_weights=None):
    from uquant.application.target_attribution import attach_target_attribution
    from uquant.execution import merge_pending_orders, plan_orders, reconcile_account_orders
    from uquant.types import Target
    from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    equity = account.cash + sum(position.shares * prices[symbol]
                                for symbol, position in account.positions.items())
    retained_weights = retained_weights or {}
    targets = tuple(Target(
        symbol, retained_weights.get(symbol, position.shares * prices[symbol] / equity if symbol in keep else 0.),
        "CORE", .9, .9, "native FIFO exit instruction",
        origin_subsystem="LEADER", mechanism="LEADER_LIFECYCLE_EXIT", origin_lifecycle="CORE",
    ) for symbol, position in account.positions.items())
    previous = list(account.pending_orders)
    targets = attach_target_attribution("optical", REQUIRED_AI_UNIVERSE_SHA256,
                                        signal_date=str(date.date()), targets=targets, retained_orders=previous)
    planned = plan_orders(signal_date=str(date.date()), targets=targets, account=account,
                          prices=prices, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=targets, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date())))


def test_last_old_member_exit_settles_epoch_without_touching_reentered_core_protection():
    from uquant.models.strategic_epoch import bind_account_strategic_ownership
    from uquant.risk.protected_recovery import capture_protected_holdings

    policy, account, dates, panel, leaders, roles, ordinary = _reentered_member()
    old_epoch = account.active_strategic_epoch_id
    assert old_epoch and account.positions[ordinary].epoch_id == ""
    shares = account.positions[ordinary].shares
    equity = account.cash + sum(position.shares * float(panel[symbol].loc[dates[0], "close"])
                                for symbol, position in account.positions.items())
    capture_protected_holdings(account=account, date=dates[0], user_panel=panel,
                               equity=equity, use_anchors=False)
    bind_account_strategic_ownership(account)
    protection = account.protected_weights[ordinary]
    assert ordinary not in account.protected_weight_epoch_ids
    _submit_exit_targets(account, dates[0], panel, keep={ordinary})
    assert account.pending_orders and all(order.side == "SELL" for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "SELL" and fill.reduction_policy == "FIFO" for fill in fills)
    assert set(account.positions) == {ordinary}
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[2], panel, _entry_deteriorated(leaders), roles, risk=_risk(frozen=False))
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[3], panel, _entry_deteriorated(leaders), roles, risk=_risk(frozen=False))
    assert account.active_strategic_epoch_id != old_epoch
    assert next(epoch for epoch in account.strategic_epochs if epoch.epoch_id == old_epoch).terminal
    assert account.positions[ordinary].shares == shares
    assert account.protected_weights[ordinary] == protection
    assert ordinary not in account.protected_weight_epoch_ids


@pytest.mark.parametrize("imported_liability", ("in-flight", "legacy-late"))
def test_retired_restoration_remainder_never_revives_but_real_liability_survives(imported_liability):
    from copy import deepcopy

    from uquant.models.strategic_epoch import settle_account_strategic_epoch
    from uquant.models.trading import late_strategic_fill_allowed

    policy, account, dates, panel, leaders, roles = _compress(_deployed(), .30)
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert len(account.pending_orders) == 1
    intent = account.pending_orders[0]
    assert intent.mechanism == "STRATEGIC_RESTORATION" and intent.grant_id
    panel[intent.symbol].loc[dates[1], "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
    pending = deepcopy(account.pending_orders[0])
    old_epoch = account.active_strategic_epoch_id
    _submit_exit_targets(account, dates[2], panel, keep=set())
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert fills and all(fill.side == "SELL" for fill in fills)
    assert not account.positions
    prior = next(order for order in account.order_ledger if order.order_id == intent.order_id)
    assert prior.filled_shares > 0 and prior.remaining_shares > 0
    # Explicit imported broker delivery / historical cancel-replace state after
    # actual native partial BUY and complete SELL. The current executor does not
    # manufacture legacy late-fill rights; this is a safety input, not economics.
    if imported_liability == "legacy-late":
        prior.status = "CANCELLED"
        prior.cancel_reason = "strategic partial remainder replaced"
        prior.last_event = "PARTIAL_REMAINDER_RELEASED"
        assert late_strategic_fill_allowed(prior)
    else:
        prior.status = "PARTIALLY_FILLED"
        prior.cancel_reason = ""
        prior.last_event = "PARTIALLY_FILLED"
        account.pending_orders = [pending]
    assert not settle_account_strategic_epoch(account, epoch_id=old_epoch,
                                             closed_session=str(dates[4].date()), close_reason="owner_exit")
    if imported_liability == "legacy-late":
        leaders = {symbol: _leader(symbol, .99, industry=leader.industry)
                   for symbol, leader in leaders.items()}
    ready_but_blocked = False
    for date in dates[4:10]:
        account = account_from_dict(asdict(account))
        risk = _risk(frozen=False)
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
        assert not any(order.side == "BUY" for order in account.pending_orders)
        assert intent.symbol not in account.strategic_cohort_targets
        assert intent.symbol not in account.strategic_restore_weights
        assert intent.symbol not in account.protected_weights
        assert not account.positions
        if imported_liability == "legacy-late":
            trace = risk.evidence["core_allocation"]
            assert intent.order_id in trace["late_fill_order_ids"]
            ready_but_blocked |= any(row.get("entry", {}).get("block") == "READY"
                                     for row in trace["symbols"].values())
            assert not next(epoch for epoch in account.strategic_epochs if epoch.epoch_id == old_epoch).terminal

    if imported_liability == "legacy-late":
        assert ready_but_blocked, "actual unresolved broker remainder must block even a current READY candidate"


def test_native_fifo_consumes_oldest_lot_without_retiring_continuous_member():
    policy, account, dates, panel, leaders, roles = _native_full(execution="partial")
    for column in ("open", "close", "high", "low", "ma20", "ma60"):
        panel[SYMBOLS[0]].loc[dates[0]:, column] /= 1.5
    symbol = SYMBOLS[-1]
    oldest = {lot.tranche_id for lot in account.positions[symbol].tranches}
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    _decide(policy, account, dates[1], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    _submit_exit_targets(account, dates[2], panel, keep=set(SYMBOLS) - {symbol},
                         retained_weights={symbol: .10})
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == symbol and fills[0].reduction_policy == "FIFO"
    assert oldest <= {lot["tranche_id"] for lot in fills[0].sold_tranches}
    assert not oldest & {lot.tranche_id for lot in account.positions[symbol].tranches}
    shares = account.positions[symbol].shares
    assert shares > 0
    target = account.strategic_cohort_targets[symbol]
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[4], panel, _entry_deteriorated(leaders), roles, risk=_risk(frozen=False))
    assert account.positions[symbol].shares == shares
    assert account.strategic_cohort_targets[symbol] == target
