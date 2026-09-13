"""Scarce fresh capital respects observed volatility and original request caps."""
from copy import deepcopy

import pytest
from test_recovery_fresh_budget import _book

from uquant.portfolio.recovery.current_cohort import _fund_members
from uquant.types import Target


@pytest.mark.parametrize("reverse", [False, True])
def test_lower_volatility_gets_more_scarce_capital_without_expanding_request(reverse):
    book, names, leaders = _book(.65, .35)
    for name, volatility in zip(names[1:], [.04, .01], strict=True):
        frame = book.user_panel[name]
        frame.loc[book.date, "atr"] = frame.loc[book.date, "close"] * volatility
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    _fund_members(book, tuple(reversed(targets)) if reverse else targets, {names[0]}, leaders)
    assert book.proposed[names[0]] == .65
    assert book.proposed[names[1]] == pytest.approx(.11)
    assert book.proposed[names[2]] == pytest.approx(.16)
    assert sum(book.committed.values()) == pytest.approx(.92)
    assert book.cash_room == pytest.approx(.08)


@pytest.mark.parametrize("bad", [float("nan"), 0., -1.])
def test_missing_volatility_keeps_previous_whole_cohort_allocation(bad):
    book, names, leaders = _book(.65, .35)
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    expected = deepcopy(book)
    _fund_members(expected, targets, {names[0]}, leaders)
    book.user_panel[names[1]].loc[book.date, "atr"] = bad
    _fund_members(book, targets, {names[0]}, leaders)
    assert book.proposed == pytest.approx(expected.proposed)
