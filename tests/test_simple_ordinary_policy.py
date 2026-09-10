"""Safety and causality checks for the fixed integrated challenger."""
from dataclasses import replace

import numpy as np
import pandas as pd
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.ordinary import ordinary_core_entry, ordinary_trend, ordinary_trend_exit
from uquant.portfolio.pipeline import _core_candidates
from uquant.types import AccountState, Opportunity


def _frame(growth=0.003):
    dates = pd.bdate_range("2024-01-02", periods=150)
    close = pd.Series(10 * (1 + growth) ** np.arange(150), index=dates)
    return pd.DataFrame({"close": close, "ma60": close.rolling(60).mean(),
                         "ret60": close.pct_change(60),
                         "amount": 100_000_000.0, "volume": 1_000_000.0})


def test_signal_ignores_future_rows_and_contains_no_recovery_certificate():
    frame = _frame()
    date = frame.index[-6]
    expected = ordinary_trend(frame, date)
    assert expected["block"] == "READY"
    frame.loc[frame.index > date, "close"] = np.nan
    assert ordinary_trend(frame, date) == expected
    assert "confirmations" not in expected
    assert "qualification_route" not in expected


def test_invalid_history_and_unconfirmed_trend_are_not_admitted():
    frame = _frame()
    assert ordinary_trend(frame, frame.index[123])["block"] != "READY"
    frame.iloc[-1, frame.columns.get_loc("close")] = 1.0
    assert ordinary_trend(frame, frame.index[-1])["block"] != "READY"
    frame.iloc[-1, frame.columns.get_loc("close")] = np.nan
    assert ordinary_trend(frame, frame.index[-1])["block"] != "READY"


def test_two_below_ma_closes_required_for_exit():
    frame = _frame()
    frame.loc[frame.index[-2:], "close"] = 1.0
    assert not ordinary_trend_exit(frame, frame.index[-2])
    assert ordinary_trend_exit(frame, frame.index[-1])


def test_ret120_ranking_is_independent_of_trace_collection():
    _, _, leaders, _ = _inputs()
    symbols = sorted(leaders)
    panel = {s: _frame(0.001 * (i + 1)) for i, s in enumerate(symbols)}
    date = panel[symbols[0]].index[-1]
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    args = dict(date=date, user_panel=panel, leaders=leaders,
                account=AccountState.empty(2_000_000.0))
    assert _core_candidates(policy, **args) == list(reversed(symbols))
    assert _core_candidates(policy, **args, trace={}) == list(reversed(symbols))


def test_unknown_industry_remains_blocked():
    _, _, leaders, _ = _inputs()
    symbol = next(iter(leaders))
    score = replace(leaders[symbol], industry="unknown")
    frame = _frame()
    result = ordinary_core_entry(PortfolioAllocator(DEFAULT_CONFIG), symbol=symbol,
        score=score, date=frame.index[-1], user_panel={symbol: frame},
        account=AccountState.empty(2_000_000.0), confirmation_days=5)
    assert result["block"] == "INDUSTRY_NOT_VERIFIED"


def test_simple_signal_does_not_bypass_freeze_or_create_grant_authority():
    _, _, leaders, risk = _inputs()
    symbols = sorted(leaders)
    panel = {s: _frame(0.001 * (i + 1)) for i, s in enumerate(symbols)}
    date = panel[symbols[0]].index[-1]
    prices = {s: float(f.loc[date, "close"]) for s, f in panel.items()}
    for frozen in (False, True):
        account = AccountState.empty(2_000_000.0)
        current_risk = replace(risk, evidence=dict(risk.evidence), freeze_new_risk=frozen)
        targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
            date=date, opportunity=Opportunity.TREND, user_panel=panel, leaders=leaders,
            risk=current_risk, account=account, prices=prices)
        assert account.strategic_grant is None
        if frozen:
            assert targets == ()
        else:
            assert targets
            assert len(targets) <= 3 and sum(t.weight for t in targets) <= 1.0
            assert all(t.weight <= 0.30 and not t.grant_id and not t.epoch_id for t in targets)
