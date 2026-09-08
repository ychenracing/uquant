"""Real CORE inventory must close its epoch on settlement, not stale qualification."""
from dataclasses import asdict, replace

import pytest
from test_strategic_probe_holding import (
    OWNER,
    _decide_and_submit,
    _entry_deteriorated,
    _filled_probe,
)
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.types import Risk


@pytest.mark.parametrize("partial", (False, True), ids=("settled-core", "actual-partial"))
def test_real_core_liquidation_uses_current_settlement_session_across_restart(partial):
    policy, account, dates, panel, leaders, roles = _filled_probe(partial=partial)
    epoch_id = account.strategic_epochs[0].epoch_id
    shares = account.positions[OWNER].shares
    first_buy = account.order_ledger[0]
    assert first_buy.filled_shares > 0
    assert bool(first_buy.remaining_shares) is partial
    leaders = _entry_deteriorated(leaders)

    # Entry proof expires while the actual filled inventory remains healthy.
    # No synthetic grant, target, fill, closed date or ledger state is injected.
    for date in dates[:3]:
        account = account_from_dict(asdict(account))
        assert not _decide_and_submit(policy, account, date, panel, leaders, roles)
        assert account.positions[OWNER].shares == shares
        epoch = next(e for e in account.strategic_epochs if e.epoch_id == epoch_id)
        assert not epoch.terminal and not epoch.closed_session
    old_observation = account.strategic_qualification.qualification_last_observed_session
    assert old_observation < str(dates[3].date())

    risk = replace(_risk(), state=Risk.RISK_OFF, target_gross_cap=0.,
                   freeze_new_risk=True, reduction_level=3)
    _decide_and_submit(policy, account, dates[3], panel, leaders, roles, risk=risk)
    assert account.pending_orders and all(o.side == "SELL" for o in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[4], account=account, panel={OWNER: panel[OWNER]},
    )
    assert sum(fill.shares for fill in fills if fill.side == "SELL") == shares
    assert not account.positions
    account = account_from_dict(asdict(account))
    _decide_and_submit(policy, account, dates[4], panel, leaders, roles)
    epoch = next(e for e in account.strategic_epochs if e.epoch_id == epoch_id)
    assert epoch.terminal
    assert epoch.closed_session == str(dates[4].date())
    account = account_from_dict(asdict(account))
    _decide_and_submit(policy, account, dates[5], panel, leaders, roles)
    assert next(e for e in account.strategic_epochs if e.epoch_id == epoch_id).closed_session == str(dates[4].date())
