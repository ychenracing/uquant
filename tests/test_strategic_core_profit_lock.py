"""Bounded CORE profit protection uses actual entry and the existing MFE controls."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_strategic_probe_holding import OWNER, _decide_and_submit, _entry_deteriorated, _filled_probe
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.application.decision import mark_account_positions
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio_core import strategic_dominant_symbol


def _marked_core(*, partial=False, crosses=True):
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=partial)
    threshold = DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe
    price = account.positions[OWNER].avg_cost * (1 + threshold + (.10 if crosses else -.10))
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    runtime = SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    )
    mark_account_positions(runtime, account, dates[0])
    assert account.strategic_epochs[0].realized_status == "CORE"
    assert strategic_dominant_symbol(account) is None
    return allocator, account, dates, panel, _entry_deteriorated(leaders), roles


def _bounded_cap(account):
    epoch = account.strategic_epochs[0]
    assert 0 < epoch.target_weight < epoch.full_weight
    return DEFAULT_CONFIG.strategic_dominant_retained_gross * epoch.target_weight / epoch.full_weight


def test_completed_core_locks_once_at_scaled_cap_without_becoming_active():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    cap = _bounded_cap(account)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(cap)
    assert account.strategic_epochs[0].realized_status == "CORE"
    assert strategic_dominant_symbol(account) is None
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    shares = account.positions[OWNER].shares
    # A further healthy price rise must not turn this one-time action into
    # repeated weight rebalancing, either live or after account round-trip.
    for column in ("open", "close", "high", "low", "ma20", "ma60"):
        panel[OWNER].loc[dates[2]:, column] *= 1.20
    assert not _decide_and_submit(allocator, account, dates[2], panel, leaders, roles)
    assert not _decide_and_submit(allocator, account, dates[2], panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[3], panel, leaders, roles)
    assert restored.positions[OWNER].shares == shares
    assert restored.strategic_epochs[0].realized_status == "CORE"


def test_completed_core_below_existing_mfe_threshold_keeps_healthy_holding():
    allocator, account, dates, panel, leaders, roles = _marked_core(crosses=False)
    shares = account.positions[OWNER].shares
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert account.positions[OWNER].shares == shares


def test_independent_risk_cap_precedes_new_core_profit_lock():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    lower_cap = _bounded_cap(account) / 2
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles,
                               risk=replace(_risk(), target_gross_cap=lower_cap, freeze_new_risk=True))
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= lower_cap


@pytest.mark.parametrize("incomplete", ("partial", "in-flight"))
def test_incomplete_core_entry_cannot_arm_profit_lock(incomplete):
    allocator, account, dates, panel, leaders, roles = _marked_core(partial=incomplete == "partial")
    if incomplete == "in-flight":
        unresolved = deepcopy(account.order_ledger[0])
        unresolved.order_id = "in-flight-core-entry"
        unresolved.status = "OPEN"
        unresolved.remaining_shares = 100
        account.order_ledger.append(unresolved)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert not orders


def test_ordinary_core_without_strategic_epoch_does_not_gain_profit_lock():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    account.strategic_epochs.clear()
    account.strategic_grant = None
    account.positions[OWNER].epoch_id = ""
    account.positions[OWNER].grant_id = ""
    for tranche in account.positions[OWNER].tranches:
        tranche.epoch_id = ""
        tranche.grant_id = ""
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)


def test_revoked_core_grant_retains_a_lower_existing_target():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    assert account.strategic_grant is not None
    account.strategic_grant.status = "EXPIRED"
    lower_target = _bounded_cap(account) / 2
    account.strategic_cohort_targets[OWNER] = lower_target
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= lower_target


@pytest.mark.parametrize("cancelled", (False, True), ids=("unfilled", "broker-cancelled"))
def test_unexecuted_core_profit_lock_retries_after_restart(cancelled):
    from uquant.broker import sync_broker_snapshot

    allocator, account, dates, panel, leaders, roles = _marked_core()
    cap = _bounded_cap(account)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    old_order_id = orders[0].order_id
    if cancelled:
        position = account.positions[OWNER]
        sync_broker_snapshot(account, {
            "as_of": str(dates[1].date()), "cash": account.cash, "fills": [],
            "orders": [{"order_id": old_order_id, "status": "CANCELLED", "remaining_shares": 0}],
            "positions": [{"symbol": OWNER, "shares": position.shares,
                           "sellable_shares": position.shares, "avg_cost": position.avg_cost}],
        }, cfg=DEFAULT_CONFIG)
        assert not account.pending_orders
    restored = account_from_dict(asdict(account))
    orders = _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(cap)
    assert (orders[0].order_id != old_order_id) == cancelled
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[3], account=restored, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    assert not _decide_and_submit(allocator, restored, dates[4], panel, leaders, roles)


@pytest.mark.parametrize("fresh_atr", (False, True), ids=("settled-atr", "fresh-atr"))
def test_core_profit_lock_preserves_settled_and_new_atr_instructions(fresh_atr):
    from test_strategic_exit_band_settlement import _band_sale

    allocator, account, dates, panel, leaders, roles = _band_sale()
    old_bands = list(account.strategic_exit_bands[OWNER])
    price = account.positions[OWNER].avg_cost * (1 + DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe + 1.0)
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    runtime = SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    )
    mark_account_positions(runtime, account, dates[0])
    if fresh_atr:
        panel[OWNER].loc[dates[1]:, ["open", "close", "high", "low"]] = price - 1
        panel[OWNER].loc[dates[1]:, "ma20"] = price
        panel[OWNER].loc[dates[1]:, "ret20"] = -.05
    orders = _decide_and_submit(allocator, account, dates[1], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    if fresh_atr:
        assert sum(account.strategic_exit_bands[OWNER]) < sum(old_bands)
        assert orders[0].target_weight == pytest.approx(sum(account.strategic_exit_bands[OWNER]))
        assert orders[0].mechanism == "STRATEGIC_TRAILING_EXIT"
    else:
        assert account.strategic_exit_bands[OWNER] == old_bands
        assert orders[0].target_weight == pytest.approx(_bounded_cap(account))
        assert orders[0].mechanism == "STRATEGIC_PROFIT_LOCK"
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[2], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    if fresh_atr:
        healthy = (price - 1) * 1.80
        for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                               ("ma20", .95), ("ma60", .85)):
            panel[OWNER].loc[dates[3]:, column] = healthy * factor
        panel[OWNER].loc[dates[3]:, "ret20"] = .20
        assert not _decide_and_submit(allocator, account, dates[3], panel, leaders, roles)


def test_historical_mfe_below_current_cap_preserves_fresh_native_promotion():
    from test_lifecycle_and_risk import _leader

    allocator, account, dates, panel, leaders, roles = _marked_core()
    # The high closing mark is real fixture history, but current price recovered
    # into a healthy trend below the existing staged profit-protection cap.
    price = account.positions[OWNER].avg_cost * 1.20
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    shares = account.positions[OWNER].shares
    assert shares * price / (account.cash + shares * price) < _bounded_cap(account)
    assert account.positions[OWNER].highest_close / account.positions[OWNER].avg_cost - 1 \
        > DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    leaders = {symbol: _leader(symbol, .95, industry="optical") for symbol in leaders}
    for index, date in enumerate(dates[1:DEFAULT_CONFIG.strategic_one_name_confirm_days + 2], start=1):
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if not orders:
            continue
        assert len(orders) == 1 and orders[0].side == "BUY"
        assert orders[0].mechanism == "STRATEGIC_COHORT"
        assert account.strategic_epochs[0].realized_status == "CORE"
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=dates[index + 1], account=account, panel={OWNER: panel[OWNER]},
        )
        assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
        assert fills[0].mechanism == "STRATEGIC_COHORT"
        assert account.strategic_epochs[0].realized_status == "ACTIVE"
        break
    else:
        pytest.fail("fresh original qualification never authorized a native promotion BUY")
