"""Research admission sizing against the unchanged eligible population."""
from dataclasses import replace

import pytest
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.pipeline import _admit_new_cores, _AllocationBook
from uquant.types import AccountState, Opportunity, Risk


@pytest.mark.parametrize("blocked", [False, True])
def test_industry_filter_does_not_redistribute_excluded_admission_budget(blocked):
    date, panel, leaders, risk = _inputs()
    symbols = list(leaders)
    book = _AllocationBook(
        policy=PortfolioAllocator(DEFAULT_CONFIG), date=date, risk=risk,
        user_panel=panel, leaders=leaders, account=AccountState.empty(2_000_000.),
        prices={s: float(panel[s].loc[date, "close"]) for s in symbols},
        weights_now={}, owned=set(), strategic_targets={}, proposed={}, committed={},
        cash_room=1., trace={s: {"entry": {"block": "READY"}} for s in symbols},
    )
    if blocked:
        book.risk = replace(risk, state=Risk.CRISIS)
    _admit_new_cores(book, candidates=symbols[:2], opportunity=Opportunity.TREND)
    if blocked:
        assert book.proposed == {}
        assert book.cash_room == 1.
    else:
        expected = DEFAULT_CONFIG.trend_entry_gross / 3
        assert book.proposed == pytest.approx(dict.fromkeys(symbols[:2], expected))
        assert book.cash_room == pytest.approx(1. - 2 * expected)
        assert symbols[2] not in book.proposed
