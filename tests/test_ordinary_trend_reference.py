"""The fixed ordinary reference uses real cash and an account-independent clock."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.capital import committed_capital
from uquant.portfolio.pipeline import _pending_intents, _reference_trend
from uquant.portfolio_core import current_weights
from uquant.types import AccountState, LeaderScore, PendingOrder, Position, Risk, RiskAssessment


def book():
    dates = pd.bdate_range("2023-01-02", periods=141)
    panel, leaders = {}, {}
    for i in range(7):
        symbol = f"sh60000{i}"
        close = 10 * np.exp(np.arange(len(dates)) * (0.001 + i * 0.0004))
        close *= 1 + 0.002 * np.sin(np.arange(len(dates)) * (0.31 + i * 0.47))
        panel[symbol] = pd.DataFrame(
            dict(close=close, open=close, high=close, low=close, volume=10_000_000.0, amount=200_000_000.0),
            index=dates,
        )
        # Deliberately immature and reverse old alpha ranking: R must not use either.
        leaders[symbol] = LeaderScore(
            symbol, 0.95 - i * 0.1, 1.0, False, False, symbol, {"unknown_industry": 0.0}
        )
    date = dates[-1]
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"ordinary_trend_clock": {"as_of": str(date.date()), "origin": str(dates[0].date()), "ordinal": 140}},
        (),
        "NORMAL",
    )
    prices = {s: float(f.loc[date, "close"]) for s, f in panel.items()}
    return AllocationBook(
        PortfolioAllocator(DEFAULT_CONFIG),
        date,
        risk,
        panel,
        leaders,
        AccountState.empty(2_000_000.0),
        prices,
        {},
        set(),
        {},
        {},
        {},
        1.0,
    )


def hold(b, symbols):
    for s in symbols:
        b.account.positions[s] = Position(s, 1000, b.prices[s], "2023-01-02", b.prices[s])
        b.account.cash -= 1000 * b.prices[s]
    b.weights_now, _ = current_weights(b.account, b.prices)
    b.proposed = dict(b.weights_now)
    b.committed, b.cash_room = committed_capital(account=b.account, prices=b.prices, proposed=b.proposed)


def test_reference_replaces_old_alpha_and_uses_top_three():
    b = book()
    _reference_trend(b, admission_open=True)
    assert {s for s, w in b.proposed.items() if w > 0} == {"sh600004", "sh600005", "sh600006"}
    assert b.cash_room >= 0 and sum(b.committed.values()) <= b.gross_cap + 1e-12


def test_off_clock_retains_actual_drift_without_new_targets():
    b = book()
    hold(b, ["sh600006"])
    before = dict(b.proposed)
    b.risk.evidence["ordinary_trend_clock"]["ordinal"] = 139
    _reference_trend(b, admission_open=True)
    assert b.proposed == before


def test_exiting_holding_does_not_free_ordinary_slot():
    b = book()
    hold(b, ["sh600000", "sh600001", "sh600002"])
    _reference_trend(b, admission_open=True)
    assert b.proposed["sh600000"] == 0
    assert not any(b.proposed.get(s, 0) > 0 for s in ["sh600004", "sh600005", "sh600006"])
    assert b.committed["sh600000"] >= b.weights_now["sh600000"]


def test_top_six_incumbents_have_priority():
    b = book()
    hold(b, ["sh600001", "sh600002", "sh600003"])
    _reference_trend(b, admission_open=True)
    assert {s for s, w in b.proposed.items() if w > 0} == set(b.weights_now)


def test_freeze_cannot_create_new_capital():
    b = book()
    _reference_trend(b, admission_open=False)
    assert not any(b.proposed.values())


def test_future_prices_cannot_change_selection():
    b = book()
    c = deepcopy(b)
    for frame in c.user_panel.values():
        frame.loc[c.date + pd.Timedelta(days=10)] = [1e9] * 6
    _reference_trend(b, admission_open=True)
    _reference_trend(c, admission_open=True)
    assert b.proposed == c.proposed


def test_recovery_owner_is_not_an_ordinary_rotation_target():
    b = book()
    hold(b, ["sh600000"])
    b.account.anchor_weights = {"sh600000": 0.1}
    before = b.proposed["sh600000"]
    _reference_trend(b, admission_open=True)
    assert b.proposed["sh600000"] == before


def test_reference_pending_bypasses_old_qualification_but_keeps_permission():
    for permitted in [False, True]:
        b = book()
        s = "sh600006"
        b.account.pending_orders = [
            PendingOrder(
                str(b.date.date()),
                s,
                "BUY",
                0.1,
                "ordinary trend",
                "CORE",
                reason_code="ordinary_trend_reference",
            )
        ]
        b.committed, b.cash_room = committed_capital(account=b.account, prices=b.prices, proposed=b.proposed)
        _pending_intents(b, buy_open=permitted, market={})
        assert (b.proposed.get(s, 0) > 0) == permitted
        assert b.committed[s] >= 0.1


@pytest.mark.parametrize("bad", ["missing", "stale"])
def test_clock_fails_closed(bad):
    b = book()
    if bad == "missing":
        b.risk.evidence.pop("ordinary_trend_clock")
    else:
        b.risk.evidence["ordinary_trend_clock"]["as_of"] = "2023-01-01"
    with pytest.raises(ValueError, match="clock"):
        _reference_trend(b, admission_open=True)
