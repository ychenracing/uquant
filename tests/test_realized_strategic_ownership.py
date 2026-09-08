"""Realized ownership is independent from restricted grant deployment stage."""
from dataclasses import asdict

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_probe_holding import OWNER, _allocate, _decide_and_submit, _entry_deteriorated, _submit

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState


def _native_order():
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
    return allocator, account, dates[index + 1:], panel, leaders, roles


@pytest.mark.parametrize("partial", [False, True], ids=["complete-first-order", "partial-first-order"])
def test_first_real_owner_buy_activates_ownership_without_completing_grant(partial):
    allocator, account, dates, panel, leaders, roles = _native_order()
    epoch = account.strategic_epochs[0]
    assert epoch.realized_status == "PROBE" and not epoch.active_session
    risk_generation = account.strategic_epoch
    generation_tags = dict(account.candidate_tenure)
    if partial:
        panel[OWNER].loc[dates[0], "volume"] = 100_000.
    cfg = DEFAULT_CONFIG.override(max_volume_participation=.002) if partial else DEFAULT_CONFIG
    fills = ExecutionPlanner(cfg).execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    assert len(fills) == 1 and fills[0].shares > 0
    assert epoch.realized_status == "ACTIVE"
    assert epoch.active_session == str(dates[0].date())
    assert account.active_strategic_epoch_id == epoch.epoch_id
    assert account.strategic_grant.status == "PARTIALLY_FILLED"
    assert account.strategic_epoch == risk_generation
    assert account.candidate_tenure == generation_tags
    assert account.order_ledger[0].status == ("PARTIALLY_FILLED" if partial else "FILLED")
    assert fills[0].grant_id == epoch.grant_id
    assert fills[0].epoch_id == epoch.epoch_id
    before = account.positions[OWNER].shares
    _decide_and_submit(allocator, account, dates[1], panel, _entry_deteriorated(leaders), roles)
    assert not account.pending_orders
    assert account.positions[OWNER].shares == before
    restored = account_from_dict(asdict(account))
    _decide_and_submit(allocator, restored, dates[2], panel, _entry_deteriorated(leaders), roles)
    assert not restored.pending_orders
    assert restored.positions[OWNER].shares == before


def test_later_native_deployment_advances_risk_generation_only_once():
    allocator, account, dates, panel, leaders, roles = _native_order()
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    executor.execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    epoch = account.strategic_epochs[0]
    assert epoch.active and account.strategic_epoch == 0
    _decide_and_submit(allocator, account, dates[1], panel, _entry_deteriorated(leaders), roles)
    for date in dates[2:]:
        _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if account.pending_orders:
            break
    else:
        pytest.fail("current qualification never permitted the later deployment")
    assert account.strategic_epoch == 0
    fill_date = dates[dates.get_loc(date) + 1]
    fills = executor.execute_open(date=fill_date, account=account, panel={OWNER: panel[OWNER]})
    assert fills and all(fill.shares > 0 for fill in fills)
    assert account.strategic_grant.status == "COMPLETED"
    assert account.strategic_epoch == 1
    assert epoch.active_session == str(dates[0].date())
    restored = account_from_dict(asdict(account))
    assert restored.strategic_epoch == 1
    assert not executor.execute_open(date=fill_date, account=restored, panel={OWNER: panel[OWNER]})
    assert restored.strategic_epoch == 1


def test_native_submitted_zero_fill_does_not_activate_ownership():
    _, account, dates, panel, _, _ = _native_order()
    panel[OWNER].loc[dates[0], "volume"] = 0.
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    assert not fills
    assert not account.positions
    assert account.strategic_epochs[0].realized_status == "PROBE"
    assert not account.strategic_epochs[0].active_session
    assert not account.active_strategic_epoch_id


def test_same_session_partial_receipts_do_not_complete_deployment():
    _, account, dates, panel, _, _ = _native_order()
    panel[OWNER].loc[dates[0], "volume"] = 100_000.
    executor = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002))
    first = executor.execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    second = executor.execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    assert first and second and first[0].shares > 0 and second[0].shares > 0
    assert account.strategic_epochs[0].active
    assert account.strategic_epoch == 0
    assert account.strategic_grant.status == "PARTIALLY_FILLED"
    assert account.pending_orders


def test_first_owned_admission_does_not_bind_unfilled_target_rights():
    from uquant.models.strategic_epoch import bind_account_strategic_ownership

    _, account, dates, panel, _, _ = _native_order()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[0], account=account, panel={OWNER: panel[OWNER]})
    witness = "sz300502"
    # Isolate binding's absence-of-inventory boundary; this is not economic evidence.
    account.strategic_cohort_targets[witness] = .1
    account.protected_weights[witness] = .1
    account.strategic_restore_weights[witness] = .1
    account.protected_weights[OWNER] = .2
    bind_account_strategic_ownership(account)
    assert account.protected_weight_epoch_ids[OWNER] == account.strategic_epochs[0].epoch_id
    assert witness not in account.protected_weight_epoch_ids
    assert witness not in account.strategic_restore_epoch_ids
