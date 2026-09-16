"""Relative protection cannot borrow a rally from before the actual holding."""
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.types import AccountState, Position


@pytest.mark.parametrize("held", [10, 80])
def test_relative_returns_share_the_actual_capped_holding_interval(held):
    from uquant.risk.market_book import _holding_return_context

    dates = pd.bdate_range("2023-01-02", periods=100)
    own = pd.DataFrame({"close": [50.] * 90 + [100.] * 9 + [90.]}, index=dates)
    tech = pd.DataFrame({"close": [100.] * 99 + [110.]}, index=dates)
    account = AccountState(initial_cash=2_000_000., cash=1_900_000.)
    account.positions["stock"] = Position(symbol="stock", shares=1000, avg_cost=100.,
        highest_close=100., entry_date=str(dates[-held].date()))
    result = _holding_return_context(dates[-1], tech, {"stock": own}, account, DEFAULT_CONFIG)
    start = max(100-held, 99-DEFAULT_CONFIG.trend_medium)
    assert result["holding_return_observed:stock"] is True
    assert result["holding_return:stock"] == pytest.approx(90 / own.iloc[start].close - 1)
    assert result["holding_reference_return:stock"] == pytest.approx(.1)
    missing = _holding_return_context(dates[-1], tech.drop(dates[start]), {"stock": own}, account, DEFAULT_CONFIG)
    assert missing == {"holding_return_observed:stock": False}


def test_aligned_post_entry_loss_overrides_positive_pre_entry_return():
    from test_slow_structural_exit import setup_case

    policy, account, symbol, frame, leader = setup_case()
    frame["ma60"] = 100.
    frame["ret60"] = .5
    outcomes = [policy._leader_lifecycle_exit_confirmed(
        symbol=symbol, date=date, user_panel={symbol: frame}, leaders={symbol: leader},
        account=account, reference_return=.1, holding_return=-.1,
    ) for date in frame.index[-3:]]
    assert outcomes == [False, False, True]
