"""Fresh cohort requests share scarce capital without moving existing rights."""
from copy import deepcopy
from dataclasses import replace
from math import sin

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.recovery.current_cohort import _fund_members
from uquant.types import AccountState, Target


def _book(held=.65, cash=.35):
    dates = pd.bdate_range("2023-01-02", periods=260)
    names = ("sz300308", "sz300394", "sz300502")
    panel = {s: _strategic_frame(dates) for s in names}
    leaders = {s: _leader(s, .9) for s in names}
    weights = {names[0]: held}
    book = AllocationBook(PortfolioAllocator(DEFAULT_CONFIG), dates[-1], _risk(frozen=False),
                          panel, leaders, AccountState.empty(2_000_000.), {}, weights,
                          set(), {}, dict(weights), dict(weights), cash)
    return book, names, leaders


@pytest.mark.parametrize("held,cash,expected", [(0.65, .35, .135), (.6, .4, .16), (.88, .12, 0.), (.6, .02, 0.)])
def test_fresh_peer_budget_is_order_independent_and_keeps_minimum(held, cash, expected):
    book, names, leaders = _book(held, cash)
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    results = []
    for ordered in (targets, tuple(reversed(targets))):
        current = deepcopy(book)
        _fund_members(current, ordered, {names[0]}, leaders)
        assert current.proposed[names[0]] == held
        assert sum(current.committed.values()) <= DEFAULT_CONFIG.recovery_target_gross + 1e-12
        assert current.cash_room >= 0
        results.append(current.proposed)
        for name in names[1:]:
            assert current.proposed.get(name, 0.) == pytest.approx(expected)
    assert results[0] == pytest.approx(results[1])


def test_distinct_independent_industries_keep_full_repaired_gross():
    book, names, _ = _book(0., 1.)
    book.weights_now.clear()
    book.proposed.clear()
    book.committed.clear()
    leaders = {s: replace(score, industry=s) for s, score in book.leaders.items()}
    book.leaders = leaders
    for index, frame in enumerate(book.user_panel.values(), 1):
        frame["close"] = [100. + sin(day * index * .31) for day in range(len(frame))]
    targets = tuple(Target(s, weight, "RECOVERY", .9, .9, "independent cohort")
                    for s, weight in zip(names, (.6, .2, .2), strict=True))
    _fund_members(book, targets, set(), leaders)
    assert sum(book.committed.values()) == pytest.approx(1.)


def test_existing_peer_reservation_is_not_diluted_by_new_request():
    book, names, leaders = _book(.65, .11)
    book.committed[names[1]] = .16
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    _fund_members(book, targets, {names[0]}, leaders)
    assert book.proposed[names[1]] == pytest.approx(.16)
    assert book.proposed[names[2]] == pytest.approx(.11)
    assert sum(book.committed.values()) == pytest.approx(.92)


def test_subminimum_peer_share_is_not_transferred_to_other_peer():
    book, names, leaders = _book(.65, .27)
    targets = tuple(Target(s, weight, "RECOVERY", .9, .9, "confirmed peer")
                    for s, weight in zip(names[1:], (.04, .28), strict=True))
    _fund_members(book, targets, {names[0]}, leaders)
    assert names[1] not in book.proposed
    assert book.proposed[names[2]] == pytest.approx(.28 * .27 / .32)
    assert book.cash_room == pytest.approx(.04 * .27 / .32)
