"""Arbitrary stock identifiers cannot own scarce restoration capital."""
from dataclasses import replace

import pytest
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.pipeline import _restore_ordinary_holdings
from uquant.types import AccountState, Lifecycle, Position


@pytest.mark.parametrize('strong', ['sh600001', 'sh600002'])
def test_qualified_restore_funds_current_stronger_leader_independent_of_code(strong):
    date, panel, leaders, risk = _inputs()
    names = ['sh600001', 'sh600002']
    weak = next(s for s in names if s != strong)
    leaders = {s: replace(leaders[s], score=.9 if s == strong else .5, industry='same') for s in names}
    account = AccountState.empty(100.)
    account.last_shock_date = str(panel[strong].index[-5].date())
    for s in names:
        account.positions[s] = Position(symbol=s, shares=1, avg_cost=10.,
                                       lifecycle=Lifecycle.CORE.value,
                                       entry_date=str(panel[s].index[0].date()), highest_close=10.)
    account.protected_weights = {s: .5 for s in names}
    weights = {s: .1 for s in names}
    book = AllocationBook(PortfolioAllocator(DEFAULT_CONFIG), date, risk, panel, leaders,
                          account, {s: 10. for s in names}, weights, set(), {},
                          dict(weights), dict(weights), .8)
    _restore_ordinary_holdings(book)
    assert book.proposed[strong] == pytest.approx(.5)
    assert book.proposed[weak] == pytest.approx(.25)
    assert sum(book.proposed.values()) == pytest.approx(DEFAULT_CONFIG.industry_weight_cap)
    assert account.positions[strong].shares == 1 and account.cash == 100.
