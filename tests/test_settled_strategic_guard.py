"""A settled epoch cannot freeze future repair; live liabilities retain its guard."""
from dataclasses import asdict, replace

import pytest
from test_shared_core_qualification import _decide
from test_strategic_flat_retirement import _compress, _deployed
from test_strategic_grant_observation import _risk

from uquant.account.codec import account_from_dict
from uquant.models.strategic_epoch import settle_account_strategic_epoch
from uquant.risk.strategic_guard import strategic_damage_guard_active
from uquant.types import Risk


@pytest.mark.parametrize("cap", [0.0, 0.6])
def test_guard_release_follows_real_epoch_settlement(cap):
    fixture = _deployed()
    account = fixture[1]
    epoch_id = account.active_strategic_epoch_id
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = account.strategic_epoch
    _policy, account, dates, _panel, _leaders, _roles = _compress(fixture, cap)
    account = account_from_dict(asdict(account))
    cash = account.cash
    capital = (account.capital_peak, account.capital_budget_level)
    settled = settle_account_strategic_epoch(
        account, epoch_id=epoch_id, closed_session=str(dates[0].date()), close_reason="owner_exit")
    assert settled is (cap == 0.0)
    assert strategic_damage_guard_active(account) is (cap != 0.0)
    assert account.cash == cash
    assert (account.capital_peak, account.capital_budget_level) == capital
    assert account.flat_book_capital_repair.healthy_session_count == 0
    account = account_from_dict(asdict(account))
    assert strategic_damage_guard_active(account) is (cap != 0.0)


def test_guard_survives_unsettled_epoch_orders():
    policy, account, dates, panel, leaders, roles = _deployed()
    epoch_id = account.active_strategic_epoch_id
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = account.strategic_epoch
    risk = replace(_risk(frozen=True), state=Risk.RISK_OFF, target_gross_cap=0.0,
                   votes=3, reduction_level=2)
    _decide(policy, account, dates[0], panel, leaders, roles, risk=risk)
    assert account.pending_orders
    assert not settle_account_strategic_epoch(
        account, epoch_id=epoch_id, closed_session=str(dates[0].date()), close_reason="owner_exit")
    assert strategic_damage_guard_active(account)
