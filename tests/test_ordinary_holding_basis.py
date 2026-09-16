"""An ordinary trend holding keeps its entry horizon before proving a profit."""

from copy import deepcopy

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Position


def holding_case():
    dates = pd.bdate_range("2020-01-02", periods=120)
    close = pd.Series([80.] * 96 + [110.] * 7 + [90.] * 17, index=dates)
    frame = pd.DataFrame({"close": close, "ma20": close.rolling(20).mean(),
                          "ma60": close.rolling(60).mean()})
    symbol = "sh688018"
    account = AccountState(initial_cash=2000000, cash=1800000)
    account.positions[symbol] = Position(symbol=symbol, shares=1000, avg_cost=110.,
                                        highest_close=110., entry_date=str(dates[102].date()))
    leader = LeaderScore(symbol=symbol, score=.5, confidence=1., mature=False,
                         emerging=False, industry="design", components={})
    return PortfolioAllocator(DEFAULT_CONFIG), account, symbol, frame, leader


def observe(case, *, own=-.20, reference=.05):
    policy, account, symbol, frame, leader = case
    outcomes = []
    for date in frame.index[-3:]:
        account = deepcopy(account)
        outcomes.append(policy._leader_lifecycle_exit_confirmed(
            symbol=symbol, date=date, user_panel={symbol: frame},
            leaders={symbol: leader}, account=account,
            holding_return=own, reference_return=reference,
        ))
    return outcomes, account


@pytest.mark.parametrize("highest_close", [110., 140.])
def test_medium_trend_survives_short_pullback_before_and_after_profit(highest_close):
    case = holding_case()
    _, account, symbol, frame, _ = case
    account.positions[symbol].highest_close = highest_close
    last = frame.iloc[-3:]
    assert (last.ma20 > last.close).all()
    assert (last.close > last.ma60).all()
    assert observe(case)[0] == [False, False, False]


@pytest.mark.parametrize("own,reference,expected", [(-.20, .05, True), (-.05, -.20, False)])
def test_medium_damage_still_requires_causal_confirmation_and_relative_damage(own, reference, expected):
    case = holding_case()
    case[3]["ma60"] = 100.
    assert observe(case, own=own, reference=reference)[0] == [False, False, expected]


@pytest.mark.parametrize("medium", [float("nan"), float("inf"), 0., -1.])
def test_unavailable_medium_observation_preserves_existing_fast_fallback(medium):
    case = holding_case()
    case[3]["ma60"] = medium
    assert observe(case)[0] == [False, False, True]


def test_valid_medium_recovery_resets_the_exit_streak():
    case = holding_case()
    case[3].loc[case[3].index[-3:-1], "ma60"] = 100.
    outcomes, account = observe(case)
    assert outcomes == [False, False, False]
    assert account.replacement_tenure[f"lifecycle_exit:{case[2]}"] == 0
