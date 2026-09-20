"""Shared competition uses real capital and preserves recovery ownership."""
from dataclasses import replace

import pytest
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.pipeline import _admit_new_cores
from uquant.types import AccountState, Opportunity, Target


@pytest.mark.parametrize('recovery_stronger', [False, True])
def test_mixed_requests_share_one_budget(monkeypatch, recovery_stronger):
    date, panel, leaders, risk = _inputs()
    ordinary, recovery = list(leaders)[:2]
    leaders = {s: replace(v, score=.95 if (s == recovery) == recovery_stronger else .8)
               for s, v in leaders.items()}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.anchor_weights = {recovery: .2}
    book = AllocationBook(PortfolioAllocator(DEFAULT_CONFIG), date, risk, panel, leaders,
                          account, dict.fromkeys(leaders, 10.), {}, set(), {}, {}, {}, .2)
    monkeypatch.setattr('uquant.portfolio.pipeline._core_requests', lambda *a, **kw: {ordinary: .2})
    target = Target(recovery, .2, 'RECOVERY', .9, .9, 'confirmed recovery',
                    origin_subsystem='RECOVERY', mechanism='RECOVERY_COHORT')
    _admit_new_cores(book, candidates=[ordinary], opportunity=Opportunity.RECOVERY,
                     market={}, recovery=(target,))
    winner = recovery if recovery_stronger else ordinary
    assert book.proposed == pytest.approx({winner: .2})
    assert book.cash_room == pytest.approx(0)
    assert sum(book.committed.values()) == pytest.approx(.2)
    assert set(book.recovery_targets) == ({recovery} if recovery_stronger else set())
    assert set(account.anchor_weights) == ({recovery} if recovery_stronger else set())
