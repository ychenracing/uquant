"""Entry permission and the exit policy remain distinct after actual probe fills."""
from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.models.strategic_grant import MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

OWNER = "sz300308"


def _allocate(allocator, account, date, panel, leaders, roles, *, risk=None):
    return allocator.allocate(
        date=date, opportunity=Opportunity.TREND, risk=risk or _risk(),
        user_panel={OWNER: panel[OWNER]}, leaders={OWNER: leaders[OWNER]},
        account=account, prices={OWNER: float(panel[OWNER].loc[date, "close"])},
        qualification_panel=panel, qualification_leaders=leaders,
        strategic_universe=roles,
    )


def _submit(account, targets, date, panel, cfg=DEFAULT_CONFIG, *, previous=None):
    previous = list(account.pending_orders) if previous is None else previous
    attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=previous,
    )
    planned = plan_orders(
        signal_date=str(date.date()), targets=attributed, account=account,
        prices={OWNER: float(panel[OWNER].loc[date, "close"])}, cfg=cfg,
    )
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=cfg)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged,
        submitted_date=str(date.date()),
    ))
    return merged


def _decide_and_submit(allocator, account, date, panel, leaders, roles, *, risk=None):
    # Application decision captures this before allocation cancels invalid BUYs.
    previous = list(account.pending_orders)
    targets = _allocate(allocator, account, date, panel, leaders, roles, risk=risk)
    return _submit(account, targets, date, panel, previous=previous)


def _filled_probe(*, partial=False):
    dates = pd.bdate_range("2023-01-02", periods=280)
    symbols = (OWNER, "sz300502", "sz300394", "sh688008")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    for frame in panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * .99
        frame["volume"] = 100_000_000.0
    leaders = {symbol: _leader(symbol, .95, industry="optical") for symbol in symbols}
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=(OWNER,),
        qualification_reference_symbols=symbols,
        risk_reference_symbols=("sh000300", "sh000682"),
        industries=dict.fromkeys(symbols, "optical"),
        available_symbols=(*symbols, "sh000300", "sh000682"),
    )
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    account.data_hash = "data:fixture"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for index in range(240, 252):
        targets = _allocate(allocator, account, dates[index], panel, leaders, roles)
        if account.strategic_grant is not None:
            break
    else:
        pytest.fail("real qualification never produced a probe")
    assert account.strategic_epochs[0].qualification_quorum == "ABSOLUTE_SINGLE"
    assert account.strategic_epochs[0].realized_status == "PROBE"
    _submit(account, targets, dates[index], panel)
    fill_date = dates[index + 1]
    cfg = DEFAULT_CONFIG.override(max_volume_participation=.002) if partial else DEFAULT_CONFIG
    if partial:
        panel[OWNER].loc[fill_date, "volume"] = 100_000.0
    fills = ExecutionPlanner(cfg).execute_open(
        date=fill_date, account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.strategic_epochs[0].realized_status == "CORE"
    assert account.order_ledger[0].status == ("PARTIALLY_FILLED" if partial else "FILLED")
    return allocator, account, dates[index + 2:], panel, leaders, roles


def _entry_deteriorated(leaders):
    return {symbol: replace(leader, score=.50, components={
        **leader.components, "breakout_quality": 0.0,
    }) for symbol, leader in leaders.items()}


@pytest.mark.parametrize("partial", (False, True), ids=("complete", "partial"))
def test_entry_quality_loss_retains_actual_probe_holding(partial):
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=partial)
    shares = account.positions[OWNER].shares
    orders = _decide_and_submit(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)

    assert not orders, "entry quality loss alone must neither liquidate nor increase a real holding"
    assert account.positions[OWNER].shares == shares
    assert not account.pending_orders
    assert account.strategic_grant is not None
    if partial:
        assert account.strategic_grant.status == "EXPIRED"
        assert account.order_ledger[0].status == "CANCELLED"
    else:
        assert account.strategic_grant.status not in {"EXPIRED", "CANCELLED"}
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[1], panel, _entry_deteriorated(leaders), roles)
    assert restored.positions[OWNER].shares == shares


def test_completed_probe_outlives_entry_retry_window():
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    assert account.strategic_grant is not None
    account.strategic_grant.healthy_retry_sessions = MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
    shares = account.positions[OWNER].shares
    frozen = replace(_risk(), freeze_new_risk=True)
    for date in dates[:2]:
        assert not _decide_and_submit(allocator, account, date, panel, leaders, roles, risk=frozen)
        assert account.strategic_grant.status not in {"EXPIRED", "CANCELLED"}
    assert account.positions[OWNER].shares == shares


def test_completed_probe_requires_fresh_confirmation_before_promotion():
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    initial_weight = account.strategic_epochs[0].target_weight
    _allocate(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)
    for index, date in enumerate(dates[1:1 + DEFAULT_CONFIG.strategic_one_name_confirm_days]):
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if index < DEFAULT_CONFIG.strategic_one_name_confirm_days - 1:
            assert not orders, "old pre-fill qualification must not authorize a fresh increase"
            assert account.strategic_epochs[0].target_weight <= initial_weight
        else:
            assert len(orders) == 1 and orders[0].side == "BUY"
            assert orders[0].target_weight > initial_weight


def test_cancelled_partial_never_rearms_after_restart_and_risk_recovery():
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=True)
    shares = account.positions[OWNER].shares
    _decide_and_submit(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)
    assert account.strategic_grant is not None and account.strategic_grant.status == "EXPIRED"
    restored = account_from_dict(asdict(account))
    frozen = replace(_risk(), target_gross_cap=.60, freeze_new_risk=True)
    for index, date in enumerate(dates[1:7]):
        assert not _decide_and_submit(allocator, restored, date, panel, leaders, roles,
                                      risk=frozen if index == 0 else _risk())
        assert restored.strategic_grant is not None
        assert restored.strategic_grant.status == "EXPIRED"
        assert restored.positions[OWNER].shares == shares


@pytest.mark.parametrize("exit_basis", ("disaster_stop", "hard_risk"))
def test_real_probe_holding_still_executes_formal_exit(exit_basis):
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    risk = _risk()
    if exit_basis == "disaster_stop":
        panel[OWNER].loc[dates[0]:, ["open", "close", "high", "low"]] = account.positions[OWNER].avg_cost * .50
    else:
        risk = replace(risk, target_gross_cap=0.0, freeze_new_risk=True)
    orders = _decide_and_submit(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles, risk=risk)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == 0.0
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert OWNER not in account.positions or account.positions[OWNER].shares == 0


@pytest.mark.parametrize("cancellation_committed", (False, True), ids=("in-flight-cancel", "cancel-completed"))
def test_late_fill_accounts_for_shares_without_reviving_expired_grant(cancellation_committed):
    from uquant.broker import sync_broker_snapshot

    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=True)
    original = account.order_ledger[0]
    shares_before = account.positions[OWNER].shares
    previous = list(account.pending_orders)
    targets = _allocate(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)
    assert account.strategic_grant is not None and account.strategic_grant.status == "EXPIRED"
    # Broker delivery may race cancellation bookkeeping; it cannot revive an
    # already acknowledged terminal order without an existing late-fill allowance.
    if cancellation_committed:
        _submit(account, targets, dates[0], panel, previous=previous)
        assert original.status == "CANCELLED"
    else:
        assert original.status == "PARTIALLY_FILLED"
    original_reason = account.strategic_grant.expiry_reason
    late_shares = original.remaining_shares
    price = float(panel[OWNER].loc[dates[1], "close"])
    snapshot = {
        "as_of": str(dates[1].date()), "cash": account.cash - late_shares * price,
        "fills": [{"fill_id": "late-cancelled-probe", "order_id": original.order_id,
                   "fill_date": str(dates[1].date()), "symbol": OWNER, "side": "BUY",
                   "shares": late_shares, "price": price, "final": True, "remaining_shares": 0}],
        "positions": [{"symbol": OWNER, "shares": shares_before + late_shares,
                       "sellable_shares": shares_before, "avg_cost": price}],
    }
    if cancellation_committed:
        before = asdict(account)
        with pytest.raises(ValueError, match="broker cannot append a fill to a terminal account order"):
            sync_broker_snapshot(account, snapshot, cfg=DEFAULT_CONFIG)
        assert asdict(account) == before
        return
    sync_broker_snapshot(account, snapshot, cfg=DEFAULT_CONFIG)
    assert account.positions[OWNER].shares == shares_before + late_shares
    settled = next(order for order in account.order_ledger if order.order_id == original.order_id)
    assert settled.filled_shares == shares_before + late_shares
    assert account.strategic_grant.status == "EXPIRED"
    assert account.strategic_grant.expiry_reason == original_reason
    targets = _allocate(allocator, account, dates[1], panel, leaders, roles)
    assert all(order.side != "BUY" for order in
               _submit(account, targets, dates[1], panel, previous=previous))
    assert account.strategic_grant.status == "EXPIRED"
    restored = account_from_dict(asdict(account))
    assert all(order.side != "BUY" for order in
               _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles))


@pytest.mark.parametrize("risk_recovers", (False, True), ids=("risk-stays-hard", "risk-recovers"))
def test_qualification_loss_preserves_already_submitted_risk_sell(risk_recovers):
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    hard_risk = replace(_risk(), target_gross_cap=0.0, freeze_new_risk=True)
    _decide_and_submit(allocator, account, dates[0], panel, leaders, roles, risk=hard_risk)
    sell = account.pending_orders[0]
    assert sell.side == "SELL"
    identity_fields = ("order_id", "event_id", "signal_date", "reduction_policy",
                       "mechanism", "origin_subsystem", "reason_code", "exit_kind",
                       "grant_id", "epoch_id")
    original_identity = tuple(getattr(sell, field) for field in identity_fields)
    _decide_and_submit(allocator, account, dates[1], panel, _entry_deteriorated(leaders), roles,
                       risk=_risk() if risk_recovers else hard_risk)
    assert len(account.pending_orders) == 1
    assert tuple(getattr(account.pending_orders[0], field) for field in identity_fields) == original_identity
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[2], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert fills[0].order_id == sell.order_id


def test_flat_core_epoch_cannot_promote_from_old_qualification():
    from uquant.portfolio.strategic.lifecycle import _promote_filled_strategic_epoch

    allocator, account, dates, panel, leaders, roles = _filled_probe()
    epoch = account.strategic_epochs[0]
    initial_weight = epoch.target_weight
    hard_risk = replace(_risk(), target_gross_cap=0.0, freeze_new_risk=True)
    _decide_and_submit(allocator, account, dates[0], panel, leaders, roles, risk=hard_risk)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert OWNER not in account.positions
    assert epoch.realized_status == "CORE"
    # The executor has settled the position; the next allocator has not yet closed
    # its persisted epoch. Stale readiness cannot authorize re-entry in that gap.
    account.strategic_qualification.qualification_ready = True
    account.strategic_qualification.deployment_blocked = False
    _promote_filled_strategic_epoch(allocator, date=dates[2], account=account, risk=_risk())
    assert epoch.target_weight == initial_weight
    assert account.strategic_cohort_targets.get(OWNER, 0.0) <= initial_weight


def test_partial_qualification_expiry_preserves_prior_lower_holding_target():
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=True)
    price = float(panel[OWNER].loc[dates[0], "close"])
    shares = account.positions[OWNER].shares
    current_weight = shares * price / (account.cash + shares * price)
    prior_target = current_weight / 2
    account.strategic_cohort_targets[OWNER] = prior_target
    assert not account.strategic_exit_bands
    assert all(order.side == "BUY" for order in account.pending_orders)

    targets = _allocate(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)

    assert account.strategic_grant is not None and account.strategic_grant.status == "EXPIRED"
    assert account.positions[OWNER].shares == shares
    assert len(targets) == 1 and targets[0].symbol == OWNER
    assert 0 < targets[0].weight <= prior_target
    assert account.strategic_cohort_targets[OWNER] <= prior_target
