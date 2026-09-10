"""A simple fallback needs current positive technology leadership over the broad index."""
from dataclasses import replace

import pytest
from test_simple_ordinary_policy import _frame
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.ordinary import observe_ordinary_market, ordinary_core_entry
from uquant.types import AccountState, Opportunity


@pytest.mark.parametrize('broad,tech,ready', [
    (.065, .0028, False), (.11, .436, True),
    (.05, .05, False), (-.2, -.1, False), (-.1, .1, True),
    (.1, float('nan'), False), (.1, True, False), (.1, None, False),
])
def test_simple_fallback_requires_positive_relative_sector_trend(broad, tech, ready):
    _, _, leaders, risk = _inputs()
    symbol = next(iter(leaders))
    frame = _frame()
    date = frame.index[-1]
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    risk = replace(risk, evidence={**risk.evidence, 'broad_ret120': broad, 'tech_ret120': tech})
    market = observe_ordinary_market(policy, date=date, opportunity=Opportunity.TREND,
                                    risk=risk, leaders=leaders, user_panel={symbol: frame})
    result = ordinary_core_entry(policy, symbol=symbol, score=leaders[symbol], date=date,
                                 user_panel={symbol: frame}, account=AccountState.empty(2_000_000.),
                                 confirmation_days=5, market=market)
    assert (result['block'] == 'READY') is ready
    assert 'qualification_route' not in result


def test_stale_simple_market_proof_cannot_fund_but_existing_strict_proof_still_can():
    _, _, leaders, _ = _inputs()
    symbol = next(iter(leaders))
    frame = _frame()
    date = frame.index[-1]
    account = AccountState.empty(2_000_000.)
    args = dict(symbol=symbol, score=leaders[symbol], date=date, user_panel={symbol: frame},
                account=account, confirmation_days=5,
                market={'as_of': str(frame.index[-2].date()), 'simple_backdrop_confirmed': True})
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    assert ordinary_core_entry(policy, **args)['block'] == 'SIMPLE_MARKET_NOT_CONFIRMED'
    account.replacement_tenure[f'strategic_eligibility:independent_core:{symbol}'] = 5
    result = ordinary_core_entry(policy, **args)
    assert result['block'] == 'READY'
    assert result['confirmations']['independent_core'] == 5


def test_strict_and_simple_candidates_use_the_same_causal_return_ranking():
    from uquant.portfolio.pipeline import _core_candidates

    _, _, leaders, _ = _inputs()
    symbols = sorted(leaders)
    panel = {s: _frame(.001 * (i + 1)) for i, s in enumerate(symbols)}
    date = panel[symbols[0]].index[-1]
    account = AccountState.empty(2_000_000.)
    account.replacement_tenure[f'strategic_eligibility:independent_core:{symbols[-1]}'] = 5
    args = dict(date=date, user_panel=panel, leaders=leaders, account=account,
                market={'as_of': str(date.date()), 'simple_backdrop_confirmed': True})
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    assert _core_candidates(policy, **args) == list(reversed(symbols))
    assert _core_candidates(policy, **args, trace={}) == list(reversed(symbols))
