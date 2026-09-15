"""Ordinary stock confirmation stays separate from strategic ownership."""
from dataclasses import replace

import pytest
from test_ordinary_persistence import _observe, _setup
from test_ordinary_trend_budget import _decide

from uquant.portfolio.ordinary import ordinary_core_entry
from uquant.portfolio.strategic.qualification_candidates import candidate_entry
from uquant.types import Risk


def _stock_proof():
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _setup()
    a.leader_tenure[symbol] = p.cfg.leader_tenure_days
    risk.evidence["tech_ret120"] = -.05
    return p, a, dates, panel, leaders, risk, refs, frames, symbol


def test_own_confirmed_leader_does_not_wait_for_market_cycle():
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _stock_proof()
    for index, date in enumerate(dates[:5]):
        market = _observe(p, a, date, panel, leaders, risk, refs, frames)
        _observe(p, a, date, panel, leaders, risk, refs, frames)
        proof = ordinary_core_entry(
            p, symbol=symbol, score=leaders[symbol], date=date, user_panel=panel,
            account=a, confirmation_days=5, market=market,
        )
        assert not market["persistent_mature_entry_open"]
        assert not market["impulse"] and not market["mature_entry_open"]
        assert (proof["block"] == "READY") == (index == 4)
    assert proof["qualification_quorum"] == "ORDINARY_CORE"
    assert proof["confirmations"]["credible_maturity"] == 5
    strict = candidate_entry(
        p, symbol=symbol, score=leaders[symbol], date=date, user_panel=panel,
        account=a, confirmation_days=5,
    )
    assert strict["block"] == "CONFIRMATION_INCOMPLETE"
    assert a.strategic_grant is None and not a.strategic_epochs


@pytest.mark.parametrize("damage", ["stale", "gap", "relative", "sector_rearm", "immature"])
def test_own_confirmation_does_not_replace_required_stock_and_repair_proof(damage):
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _stock_proof()
    for date in dates[:5]:
        market = _observe(p, a, date, panel, leaders, risk, refs, frames)
    if damage == "stale":
        market["as_of"] = str(dates[3].date())
    elif damage == "gap":
        date = dates[6]
        market = _observe(p, a, date, panel, leaders, risk, refs, frames)
    elif damage == "relative":
        panel[symbol].loc[date, "ret120"] = -.1
    elif damage == "sector_rearm":
        a.candidate_tenure["ordinary_market_rearm_required"] = 1
    else:
        leaders[symbol] = replace(leaders[symbol], mature=False)
    proof = ordinary_core_entry(
        p, symbol=symbol, score=leaders[symbol], date=date, user_panel=panel,
        account=a, confirmation_days=5, market=market,
    )
    assert proof["block"] != "READY"


def test_stock_confirmation_cannot_authorize_frozen_capital():
    p, a, dates, panel, leaders, risk, refs, frames, symbol = _stock_proof()
    risk = replace(risk, state=Risk.CAUTION, freeze_new_risk=True)
    for date in dates[:5]:
        _observe(p, a, date, panel, leaders, risk, refs, frames)
        assert not _decide(p, a, date, panel, leaders, risk)
    assert not a.pending_orders and not a.positions
    assert a.cash == p.cfg.initial_cash
