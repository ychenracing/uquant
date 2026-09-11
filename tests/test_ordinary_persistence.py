"""Sustained market witnesses never replace a stock's own current proof."""
from dataclasses import replace

import pytest
from test_ordinary_trend_budget import _scenario

from uquant.portfolio.ordinary import observe_ordinary_market, ordinary_core_entry
from uquant.types import Opportunity, Risk


def _setup():
    policy, account, dates, panel, leaders, risk = _scenario()
    symbol = next(iter(leaders))
    witness = 'reference_only'
    refs = {**leaders, witness: replace(leaders[symbol], symbol=witness)}
    frames = {**panel, witness: panel[symbol].copy()}
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.55, broad_ret20=-.01, tech_ret20=.1,
                         broad_ret120=.04, tech_ret120=.3)
    return policy, account, dates, panel, leaders, risk, refs, frames, symbol


def _observe(policy, account, date, panel, leaders, risk, refs, frames):
    from uquant.portfolio.ordinary import observe_persistent_maturity, observe_repair_maturity
    market = observe_ordinary_market(policy, date=date, opportunity=Opportunity.STRONG_TREND,
        risk=risk, leaders=leaders, user_panel=panel)
    observe_repair_maturity(policy, account=account, date=date, market=market, user_panel=panel)
    observe_persistent_maturity(policy, account=account, date=date, market=market,
        opportunity=Opportunity.STRONG_TREND, risk=risk, leaders=refs, user_panel=frames)
    return market


def test_persistent_reference_evidence_still_needs_own_five_current_observations():
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _setup()
    a.leader_tenure[symbol] = 5
    for i, day in enumerate(dates[:5]):
        market = _observe(p, a, day, panel, leaders, risk, refs, frames)
        _observe(p, a, day, panel, leaders, risk, refs, frames)  # Same date is idempotent.
        proof = ordinary_core_entry(p, symbol=symbol, score=leaders[symbol], date=day,
            user_panel=panel, account=a, confirmation_days=5, market=market)
        assert (proof['block'] == 'READY') == (i == 4)
    assert proof['qualification_quorum'] == 'ORDINARY_CORE'
    assert proof['confirmations']['credible_maturity'] == 5
    assert not market['impulse'] and not market['mature_entry_open']
    assert 'reference_only' not in market['credible_symbols']
    assert a.strategic_grant is None and not a.strategic_epochs
    a.replacement_tenure['ordinary_repair_maturity:' + symbol] = 0
    proof = ordinary_core_entry(p, symbol=symbol, score=leaders[symbol], date=day,
        user_panel=panel, account=a, confirmation_days=5, market=market)
    assert proof['block'] != 'READY'


@pytest.mark.parametrize('damage', ['freeze', 'witness', 'missing', 'gap', 'stale'])
def test_market_persistence_resets_on_invalid_or_missing_sessions(damage):
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _setup()
    for day in dates[:4]:
        _observe(p, a, day, panel, leaders, risk, refs, frames)
    if damage == 'freeze':
        risk = replace(risk, state=Risk.CAUTION, freeze_new_risk=True)
    elif damage == 'witness':
        refs = {k:replace(v, industry=leaders[symbol].industry) for k,v in refs.items()}
    elif damage == 'missing':
        risk.evidence.pop('tech_ret120')
    elif damage == 'stale':
        frames = {k:v.drop(dates[4]) for k,v in frames.items()}
    market = _observe(p, a, dates[5] if damage == 'gap' else dates[4],
                      panel, leaders, risk, refs, frames)
    assert not market['persistent_mature_entry_open']


@pytest.mark.parametrize('diverse', [True, False])
def test_market_breadth_requires_independent_industries_not_more_names(diverse):
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _setup()
    if diverse:
        refs.pop('reference_only')
        assert len(refs) == 2 and len({v.industry for v in refs.values()}) == 2
    else:
        refs = {k:replace(v, industry=leaders[symbol].industry) for k,v in refs.items()}
        assert len(refs) == 3
    for day in dates[:5]:
        market = _observe(p, a, day, panel, leaders, risk, refs, frames)
    assert market['persistent_mature_entry_open'] is diverse
