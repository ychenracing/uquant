"""A completed staged CORE may use the existing confirmed structural exit."""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from test_strategic_probe_holding import OWNER, _decide_and_submit, _entry_deteriorated, _filled_probe
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


def _aged_core(*, partial=False):
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=partial)
    leaders = _entry_deteriorated(leaders)
    for date in dates[:DEFAULT_CONFIG.min_hold_days]:
        assert not _decide_and_submit(allocator, account, date, panel, leaders, roles)
    return allocator, account, dates[DEFAULT_CONFIG.min_hold_days:], panel, leaders, roles


def _damage(panel, leaders, dates):
    frame = panel[OWNER]
    frame.loc[dates, "ma20"] = frame.loc[dates, "close"] * 1.10
    frame.loc[dates, "ret20"] = -.09
    return {symbol: replace(leader, mature=False) for symbol, leader in leaders.items()}


def test_confirmed_core_structure_exit_keeps_native_identity_until_final_fill():
    allocator, account, dates, panel, leaders, roles = _aged_core()
    leaders = _damage(panel, leaders, dates)
    epoch = account.strategic_epochs[0]
    assert epoch.realized_status == "ACTIVE" and not epoch.terminal
    assert account.strategic_epoch == 0
    assert account.strategic_grant.status == "PARTIALLY_FILLED"
    for index, date in enumerate(dates[:DEFAULT_CONFIG.replacement_confirm_days]):
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if index < DEFAULT_CONFIG.replacement_confirm_days - 1:
            assert not orders
    assert len(orders) == 1 and orders[0].side == "SELL" and orders[0].target_weight == 0
    original = orders[0]
    assert original.mechanism == "STRATEGIC_TRAILING_EXIT"
    assert original.origin_subsystem == "STRATEGIC"
    assert original.epoch_id == epoch.epoch_id and original.grant_id == epoch.grant_id
    assert original.event_id and not epoch.terminal
    fill_date = dates[DEFAULT_CONFIG.replacement_confirm_days]
    panel[OWNER].loc[fill_date, "volume"] = 100_000.0
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=fill_date, account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and 0 < fills[0].shares < account.order_ledger[-1].requested_shares
    assert account.positions[OWNER].shares > 0 and not epoch.terminal
    restored = account_from_dict(asdict(account))
    frozen = replace(_risk(), freeze_new_risk=True)
    orders = _decide_and_submit(allocator, restored, fill_date, panel, leaders, roles, risk=frozen)
    assert len(orders) == 1 and orders[0].order_id == original.order_id
    assert orders[0].event_id == original.event_id
    final_date = dates[DEFAULT_CONFIG.replacement_confirm_days + 1]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=final_date, account=restored, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].order_id == original.order_id
    assert OWNER not in restored.positions
    assert not _decide_and_submit(allocator, restored, dates[DEFAULT_CONFIG.replacement_confirm_days + 2],
                                  panel, leaders, roles, risk=frozen)
    assert restored.strategic_epochs[0].terminal


def test_structural_confirmation_counts_sessions_once_across_restart():
    allocator, account, dates, panel, leaders, roles = _aged_core()
    leaders = _damage(panel, leaders, dates)
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    for _ in range(3):
        assert not _decide_and_submit(allocator, restored, dates[0], panel, leaders, roles)
    assert restored.replacement_tenure[f"lifecycle_exit:{OWNER}"] == 1
    assert not _decide_and_submit(allocator, restored, dates[1], panel, leaders, roles)
    orders = _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"


@pytest.mark.parametrize("missing", ("maturity", "price", "return", "interrupted", "partial-entry"))
def test_core_exit_preserves_existing_conjunction_and_entry_completion(missing):
    allocator, account, dates, panel, leaders, roles = _aged_core(partial=missing == "partial-entry")
    damaged = _damage(panel, leaders, dates)
    if missing == "maturity":
        damaged = leaders
    elif missing == "price":
        panel[OWNER].loc[dates, "ma20"] = panel[OWNER].loc[dates, "close"] * .95
    elif missing == "return":
        panel[OWNER].loc[dates, "ret20"] = -.07
    for index, date in enumerate(dates[:4]):
        current = leaders if missing == "interrupted" and index == 1 else damaged
        assert not _decide_and_submit(allocator, account, date, panel, current, roles)
    assert account.positions[OWNER].shares > 0
    assert not account.strategic_epochs[0].terminal


def test_actual_active_epoch_retains_its_strategic_exit_policy():
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    original_leaders = leaders
    assert not _decide_and_submit(allocator, account, dates[0], panel, _entry_deteriorated(leaders), roles)
    for index, date in enumerate(dates[1:DEFAULT_CONFIG.strategic_one_name_confirm_days + 2], start=1):
        orders = _decide_and_submit(allocator, account, date, panel, original_leaders, roles)
        if orders:
            assert len(orders) == 1 and orders[0].side == "BUY"
            fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
                date=dates[index + 1], account=account, panel={OWNER: panel[OWNER]},
            )
            assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
            break
    else:
        pytest.fail("fresh confirmed qualification never promoted the native CORE")
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    remaining = dates[DEFAULT_CONFIG.min_hold_days:]
    damaged = _damage(panel, _entry_deteriorated(leaders), remaining)
    shares = account.positions[OWNER].shares
    for date in remaining[:DEFAULT_CONFIG.replacement_confirm_days + 1]:
        assert not _decide_and_submit(allocator, account, date, panel, damaged, roles)
    assert account.positions[OWNER].shares == shares
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
