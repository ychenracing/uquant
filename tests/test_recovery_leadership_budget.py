"""Scarce confirmed-peer capital follows current leadership within old caps."""
from dataclasses import replace

import pytest
from test_recovery_fresh_budget import _book

from uquant.portfolio.recovery.cohort_admission import (
    RecoverySelection,
    _entry_leadership_weights,
)
from uquant.portfolio.recovery.current_cohort import _fund_members
from uquant.types import Target


@pytest.mark.parametrize("reverse", [False, True])
def test_expansion_leadership_sizes_entry_without_moving_restoration_rights(reverse):
    book, names, leaders = _book(.643423858223, .356576141777)
    for name, score in zip(names[1:], (.2, .8), strict=True):
        leaders[name] = replace(leaders[name], score=score)
    selection = RecoverySelection(
        previous_members={names[0]},
        selected=list(names),
        candidates=list(leaders.values()),
        crash_depth={},
        recovery_elapsed=0,
        lead=names[0],
        secondaries=list(reversed(names[1:])) if reverse else list(names[1:]),
    )
    owner_rights = {names[0]: .60, names[1]: .16, names[2]: .16}

    entry = _entry_leadership_weights(selection, owner_rights)

    assert owner_rights == pytest.approx({names[0]: .60, names[1]: .16, names[2]: .16})
    assert entry == pytest.approx({names[0]: .60, names[1]: .064, names[2]: .256})
    book.account.anchor_weights = dict(owner_rights)
    targets = tuple(Target(symbol, weight, "RECOVERY", .9, .9, "confirmed peer")
                    for symbol, weight in entry.items() if symbol != names[0])
    _fund_members(book, targets, {names[0]}, leaders)
    room = .92 - .643423858223
    assert book.proposed == pytest.approx({names[0]: .643423858223,
                                          names[1]: room * .2,
                                          names[2]: room * .8})


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


def test_persisted_unequal_rights_still_use_current_leadership():
    book, names, leaders = _book(.65, .35)
    for name, score in zip(names[1:], (.2, .8), strict=True):
        leaders[name] = replace(leaders[name], score=score)
    rights = {names[0]: .60, names[1]: .10, names[2]: .22}
    book.account.anchor_weights = dict(rights)
    targets = tuple(Target(symbol, rights[symbol], "RECOVERY", .9, .9, "confirmed peer")
                    for symbol in names[1:])

    _fund_members(book, targets, {names[0]}, leaders)

    assert book.proposed == pytest.approx({names[0]: .65, names[1]: .05, names[2]: .22})


def test_expansion_cash_surplus_cannot_increase_requested_targets():
    book, names, leaders = _book(.4, .6)
    book.account.anchor_weights = dict(zip(names, (.6, .16, .16), strict=True))
    targets = tuple(Target(s, w, "RECOVERY", .9, .9, "confirmed peer")
                    for s, w in zip(names[1:], (.064, .256), strict=True))
    _fund_members(book, targets, {names[0]}, leaders)
    assert book.proposed == pytest.approx({names[0]: .4, names[1]: .064, names[2]: .256})
    assert book.cash_room == pytest.approx(.28)
