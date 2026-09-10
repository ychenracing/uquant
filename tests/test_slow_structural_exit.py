"""A pre-entry rally must not veto a confirmed loss of holding structure."""
from dataclasses import replace

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Position


def setup_case():
    dates = pd.bdate_range("2023-01-02", periods=60)
    close = pd.Series([80.] * 42 + [110.] * 7 + [90.] * 11, index=dates)
    frame = pd.DataFrame({"close": close, "ma20": close.rolling(20).mean(),
                          "ma60": close.rolling(60).mean(),
                          "ret20": close.pct_change(20, fill_method=None)})
    symbol = "sh688018"
    account = AccountState(initial_cash=2000000, cash=1800000)
    account.positions[symbol] = Position(symbol=symbol, shares=1000, avg_cost=110.,
                                        highest_close=110., entry_date=str(dates[47].date()))
    leader = LeaderScore(symbol=symbol, score=.5, confidence=1., mature=False,
                         emerging=False, industry="design", components={})
    return PortfolioAllocator(DEFAULT_CONFIG), account, symbol, frame, leader


def observe(policy, account, symbol, frame, leader, date):
    return policy._leader_lifecycle_exit_confirmed(
        symbol=symbol, date=date, user_panel={symbol: frame},
        leaders={symbol: leader}, account=account,
    )


def test_confirmed_structure_loss_exits_despite_pre_entry_rally():
    policy, account, symbol, frame, leader = setup_case()
    assert frame.iloc[-1].ret20 > 0
    assert frame.iloc[-1].close < account.positions[symbol].avg_cost
    outcomes = [observe(policy, account, symbol, frame, leader, date) for date in frame.index[-3:]]
    assert outcomes == [False, False, True]


def test_mature_and_protected_winner_controls_remain():
    policy, account, symbol, frame, leader = setup_case()
    mature = replace(leader, mature=True)
    assert not any(observe(policy, account, symbol, frame, mature, date) for date in frame.index[-3:])
    policy, account, symbol, frame, leader = setup_case()
    account.positions[symbol].highest_close = 140.
    assert frame.iloc[-1].ma20 > frame.iloc[-1].close > frame.iloc[-1].ma60
    assert not any(observe(policy, account, symbol, frame, leader, date) for date in frame.index[-3:])


def test_repeated_same_session_does_not_accelerate_confirmation():
    policy, account, symbol, frame, leader = setup_case()
    date = frame.index[-3]
    assert not any(observe(policy, account, symbol, frame, leader, date) for _ in range(3))
    assert account.replacement_tenure[f"lifecycle_exit:{symbol}"] == 1
