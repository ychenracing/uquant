"""Scarce confirmed-peer capital follows current leadership within old caps."""
from dataclasses import replace

import pytest
from test_recovery_fresh_budget import _book

from uquant.portfolio.recovery.current_cohort import _fund_members
from uquant.types import Target


@pytest.mark.parametrize("reverse", [False, True])
def test_current_stronger_peer_receives_more_without_moving_incumbent(reverse):
    book, names, leaders = _book(.65, .35)
    for name, score in zip(names[1:], (.2, .8), strict=True):
        leaders[name] = replace(leaders[name], score=score)
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    _fund_members(book, tuple(reversed(targets)) if reverse else targets, {names[0]}, leaders)
    assert book.proposed == pytest.approx({names[0]: .65, names[1]: .11, names[2]: .16})
    assert sum(book.committed.values()) == pytest.approx(.92)
    assert book.cash_room == pytest.approx(.08)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0., -1.])
def test_unavailable_peer_strength_keeps_original_group_proportions(bad):
    book, names, leaders = _book(.65, .35)
    leaders[names[1]] = replace(leaders[names[1]], score=bad)
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    _fund_members(book, targets, {names[0]}, leaders)
    assert book.proposed == pytest.approx({names[0]: .65, names[1]: .135, names[2]: .135})


@pytest.mark.parametrize("reverse", [False, True])
def test_unequal_incremental_requests_preserve_existing_reservation(reverse):
    book, names, leaders = _book(.65, .15)
    book.committed[names[1]] = .05
    for name, score in zip(names[1:], (.4, .8), strict=True):
        leaders[name] = replace(leaders[name], score=score)
    targets = tuple(Target(s, .16, "RECOVERY", .9, .9, "confirmed peer") for s in names[1:])
    _fund_members(book, tuple(reversed(targets)) if reverse else targets, {names[0]}, leaders)
    # Only the unreserved .11 participates; neither share reaches its cap.
    increment = .15 * (.11 * .4) / (.11 * .4 + .16 * .8)
    assert book.proposed == pytest.approx({names[0]: .65, names[1]: .05 + increment,
                                          names[2]: .15 - increment})
    assert sum(book.committed.values()) == pytest.approx(.85)
    assert book.cash_room == pytest.approx(0.)
