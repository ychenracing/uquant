"""A completed native FIFO reduction is a receipt, not a perpetual weight cap."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_lifecycle_and_risk import _leader
from test_strategic_probe_holding import (
    OWNER,
    _decide_and_submit,
    _entry_deteriorated,
    _filled_probe,
)
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.application.decision import mark_account_positions
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


def _band_sale(*, broker=False, split=False):
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=broker)
    if broker:
        original = account.order_ledger[0]
        shares = original.remaining_shares
        total = account.positions[OWNER].shares + shares
        price = float(panel[OWNER].loc[dates[0], "close"])
        sync_broker_snapshot(account, {
            "as_of": str(dates[0].date()), "cash": account.cash - shares * price,
            "fills": [{"fill_id": "native-broker-probe", "order_id": original.order_id,
                       "fill_date": str(dates[0].date()), "symbol": OWNER, "side": "BUY",
                       "shares": shares, "price": price, "final": True, "remaining_shares": 0}],
            "positions": [{"symbol": OWNER, "shares": total,
                           "sellable_shares": 30000 if split else total, "avg_cost": price}],
        }, cfg=DEFAULT_CONFIG)
        if split:
            # A second real availability reconciliation may split an already
            # split economic lot; both suffix generations retain BUY origin.
            sync_broker_snapshot(account, {
                "as_of": str(dates[0].date()), "cash": account.cash, "fills": [],
                "positions": [{"symbol": OWNER, "shares": total,
                               "sellable_shares": 25000, "avg_cost": price}],
            }, cfg=DEFAULT_CONFIG)
        assert account.fills[-1].fill_id == "native-broker-probe"
        settled_buy = next(order for order in account.order_ledger
                           if order.order_id == original.order_id)
        assert settled_buy.status == "FILLED" and settled_buy.remaining_shares == 0
        assert any(lot.tranche_id.startswith("broker-fill:")
                   for lot in account.positions[OWNER].tranches)
        dates = dates[1:]
    # A persisted accumulated ATR instruction still needs its first real sale,
    # even on a session with no fresh trigger. It must not be skipped merely
    # because today's price is healthy.
    count = DEFAULT_CONFIG.strategic_cohort_trail_bands
    account.strategic_exit_bands[OWNER] = [.12 / count] * count
    account.strategic_active_bands[OWNER] = [True] * count
    leaders = _entry_deteriorated(leaders)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].mechanism == "STRATEGIC_TRAILING_EXIT"
    assert orders[0].target_weight == pytest.approx(.12)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].shares > 0
    assert fills[0].sold_tranches and fills[0].reduction_policy == "FIFO"
    sale = next(order for order in account.order_ledger if order.order_id == fills[0].order_id)
    assert sale.status == "FILLED" and sale.remaining_shares == 0
    assert account.positions[OWNER].shares > 0
    # Price drift raises the real holding above the already executed instruction.
    for column in ("open", "close", "high", "low", "ma20", "ma60"):
        panel[OWNER].loc[dates[2]:, column] *= 1.60
    return allocator, account, dates[2:], panel, leaders, roles


def _settled(account):
    from uquant.portfolio.strategic.lifecycle import _settled_strategic_exit_target
    return _settled_strategic_exit_target(account, OWNER, sum(account.strategic_exit_bands[OWNER]))


def test_completed_fifo_band_sale_does_not_rebalance_drift_or_restart():
    allocator, account, dates, panel, leaders, roles = _band_sale()
    shares = account.positions[OWNER].shares
    bands = list(account.strategic_exit_bands[OWNER])
    price = float(panel[OWNER].loc[dates[0], "close"])
    actual_weight = shares * price / (account.cash + shares * price)
    assert actual_weight > sum(bands) + DEFAULT_CONFIG.min_trade_weight
    for date in (dates[0], dates[0]):
        assert not _decide_and_submit(allocator, account, date, panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[1], panel, leaders, roles)
    assert restored.positions[OWNER].shares == shares
    assert restored.strategic_exit_bands[OWNER] == bands


def test_fresh_atr_instruction_still_reduces_a_settled_holding():
    allocator, account, dates, panel, leaders, roles = _band_sale()
    old_bands = list(account.strategic_exit_bands[OWNER])
    close = float(panel[OWNER].loc[dates[0], "close"])
    peak_date = panel[OWNER].index[panel[OWNER].index.get_loc(dates[0]) - 1]
    panel[OWNER].loc[peak_date, "close"] = close + 1.0
    runtime = SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    )
    mark_account_positions(runtime, account, peak_date)
    panel[OWNER].loc[dates[0], "ma20"] = close * 1.1
    panel[OWNER].loc[dates[0], "ret20"] = -.05
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    epoch = account.strategic_epochs[0]
    step = DEFAULT_CONFIG.strategic_cohort_exit_step * epoch.target_weight / epoch.full_weight
    assert account.strategic_exit_bands[OWNER] == pytest.approx(
        [band - step / len(old_bands) for band in old_bands]
    )
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(sum(account.strategic_exit_bands[OWNER]))


def test_risk_cap_still_reduces_a_settled_holding():
    allocator, account, dates, panel, leaders, roles = _band_sale()
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles,
                               risk=replace(_risk(), target_gross_cap=.06, freeze_new_risk=True))
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= .06


@pytest.mark.parametrize("damage", (
    "partial", "pending", "cancelled", "event", "epoch", "no-fill",
    "missing-identity", "wrong-tranche-epoch", "wrong-tranche-grant",
    "negative-tranche", "unknown-tranche", "late-broker-remainder",
))
def test_incomplete_or_unproven_receipt_cannot_settle_band(damage):
    _, account, _, _, _, _ = _band_sale()
    sale = account.order_ledger[-1]
    fill = account.fills[-1]
    assert _settled(account)
    if damage == "partial":
        sale.status = "PARTIALLY_FILLED"
        sale.remaining_shares = 100
    elif damage == "pending":
        account.pending_orders.append(deepcopy(sale))
    elif damage == "cancelled":
        sale.status = "CANCELLED"
        sale.filled_shares = 0
    elif damage == "event":
        fill.event_id = "other-event"
    elif damage == "epoch":
        sale.epoch_id = "other-epoch"
    elif damage == "no-fill":
        account.fills.pop()
    elif damage == "missing-identity":
        fill.sold_tranches = [{"shares": fill.shares}]
    elif damage == "wrong-tranche-epoch":
        fill.sold_tranches[0]["epoch_id"] = "other-epoch"
    elif damage == "wrong-tranche-grant":
        fill.sold_tranches[0]["grant_id"] = "other-grant"
    elif damage == "negative-tranche":
        original = deepcopy(fill.sold_tranches[0])
        fill.sold_tranches = [{**original, "shares": -100},
                             {**original, "shares": fill.shares + 100}]
    elif damage == "unknown-tranche":
        fill.sold_tranches[0]["tranche_id"] = "no-native-buy-origin"
    else:
        buy = deepcopy(account.order_ledger[0])
        buy.order_id = "late-remainder"
        buy.status = "CANCELLED"
        buy.cancel_reason = "strategic partial remainder replaced"
        buy.last_event = "CANCELLED"
        buy.remaining_shares = 100
        account.order_ledger.append(buy)
    assert not _settled(account)


def test_fresh_native_buy_invalidates_an_old_band_receipt():
    allocator, account, dates, panel, leaders, roles = _band_sale()
    old_bands = account.strategic_exit_bands.pop(OWNER)
    old_armed = account.strategic_active_bands.pop(OWNER)
    leaders = {symbol: _leader(symbol, .95, industry="optical") for symbol in leaders}
    for index, date in enumerate(dates[:DEFAULT_CONFIG.strategic_one_name_confirm_days + 2]):
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if orders:
            assert len(orders) == 1 and orders[0].side == "BUY"
            fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
                date=dates[index + 1], account=account, panel={OWNER: panel[OWNER]},
            )
            assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
            break
    else:
        pytest.fail("fresh original qualification never authorized a native BUY")
    account.strategic_exit_bands[OWNER] = old_bands
    account.strategic_active_bands[OWNER] = old_armed
    assert not _settled(account)


@pytest.mark.parametrize("split", (False, True), ids=("broker-lot", "broker-split-lot"))
def test_native_broker_buy_origin_settles_fifo_sale(split):
    allocator, account, dates, panel, leaders, roles = _band_sale(broker=True, split=split)
    lots = account.fills[-1].sold_tranches
    broker_lots = [lot for lot in lots if lot["tranche_id"].startswith("broker-fill:")]
    assert broker_lots
    if split:
        assert any(lot["tranche_id"].endswith(":sellable") for lot in broker_lots)
        assert any(lot["tranche_id"].endswith(":t1") for lot in broker_lots)
        assert any(":sellable:sellable" in lot["tranche_id"] for lot in broker_lots)
    assert _settled(account)
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[1], panel, leaders, roles)
    # Same physical sale with a different broker fill identity has no BUY origin.
    broker_lots[0]["tranche_id"] = "broker-fill:unobserved-buy"
    assert not _settled(account)


def test_revoked_grant_keeps_prior_lower_cap_after_band_settlement():
    allocator, account, dates, panel, leaders, roles = _band_sale()
    assert _settled(account)
    assert account.strategic_grant is not None
    account.strategic_grant.status = "EXPIRED"
    account.strategic_cohort_targets[OWNER] = .06
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= .06
