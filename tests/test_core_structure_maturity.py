"""Existing price damage owns exits independently from entry maturity."""
from dataclasses import replace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Position

SYMBOL = "sh688041"


def _case(*, winner=False, recent=False):
    dates = pd.bdate_range("2024-01-02", periods=40)
    frame = pd.DataFrame({"close": 90., "ma20": 100., "ma60": 95., "ret20": -.10}, index=dates)
    account = AccountState.empty(200_000.)
    account.positions[SYMBOL] = Position(
        symbol=SYMBOL, shares=1000, avg_cost=100.,
        entry_date=str(dates[20 if recent else 0].date()),
        highest_close=125. if winner else 110.,
    )
    return PortfolioAllocator(DEFAULT_CONFIG), account, frame, dates[20:]


def _observe(policy, account, frame, date, *, mature=False):
    return policy._leader_lifecycle_exit_confirmed(
        symbol=SYMBOL, date=date, user_panel={SYMBOL: frame},
        leaders={SYMBOL: replace(_leader(SYMBOL, .90), mature=mature)}, account=account,
    )


def test_maturity_recovery_does_not_erase_confirmed_price_damage():
    policy, account, frame, dates = _case()
    assert not _observe(policy, account, frame, dates[0])
    assert not _observe(policy, account, frame, dates[1])
    assert _observe(policy, account, frame, dates[2], mature=True)
    assert account.replacement_tenure[f"lifecycle_exit:{SYMBOL}"] == DEFAULT_CONFIG.replacement_confirm_days
    assert _observe(policy, account, frame, dates[2], mature=True)
    assert account.replacement_tenure[f"lifecycle_exit:{SYMBOL}"] == DEFAULT_CONFIG.replacement_confirm_days


@pytest.mark.parametrize("field,value", (("close", 101.), ("ret20", -.01)))
def test_healthy_structure_resets_existing_confirmation(field, value):
    policy, account, frame, dates = _case()
    assert not _observe(policy, account, frame, dates[0])
    assert not _observe(policy, account, frame, dates[1])
    frame.loc[dates[2], field] = value
    assert not _observe(policy, account, frame, dates[2])
    assert account.replacement_tenure[f"lifecycle_exit:{SYMBOL}"] == 0
    assert not _observe(policy, account, frame, dates[3])


@pytest.mark.parametrize("field,value", (("ret20", -.10), ("ma60", 85.)))
def test_protected_winner_keeps_both_stricter_damage_requirements(field, value):
    policy, account, frame, dates = _case(winner=True)
    frame["ret20"] = -.16
    frame[field] = value
    for date in dates[:3]:
        assert not _observe(policy, account, frame, date)
    assert account.replacement_tenure[f"lifecycle_exit:{SYMBOL}"] == 0
    frame.loc[dates[3]:, "ret20"] = -.16
    frame.loc[dates[3]:, "ma60"] = 95.
    assert not _observe(policy, account, frame, dates[3], mature=True)
    assert not _observe(policy, account, frame, dates[4], mature=True)
    assert _observe(policy, account, frame, dates[5], mature=True)


def test_confirmed_damage_does_not_bypass_minimum_holding_period():
    policy, account, frame, dates = _case(recent=True)
    for date in dates[:DEFAULT_CONFIG.min_hold_days - 1]:
        assert not _observe(policy, account, frame, date)
    assert _observe(policy, account, frame, dates[DEFAULT_CONFIG.min_hold_days - 1])
