"""New grant authority is separate from shared ordinary trend qualification."""
from dataclasses import replace

import pytest
from test_lifecycle_and_risk import _normal_risk

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import discovery
from uquant.portfolio.strategic.qualification_candidates import StrategicRoute
from uquant.portfolio.strategic.quorum import StrategicQuorumResult, StrategicQuorumRoute
from uquant.types import AccountState


@pytest.mark.parametrize('family', ('established', 'transition', 'transition_impulse'))
@pytest.mark.parametrize('repair_ready', (False, True))
def test_ordinary_only_rank_cannot_hide_formation_or_actual_repair_fallback(
    monkeypatch, family, repair_ready,
):
    ordinary = StrategicRoute(['a'], family, None, False, [], False, True, 'a')
    formation = replace(ordinary, symbols=['b'], owner_symbol='b',
                        route='reversal_industry', synchronized_reversal=True)
    quorum = StrategicQuorumResult(
        StrategicQuorumRoute.FULL_COHORT, True, True, True, True, True, (), (), (), 2, None,
    )
    monkeypatch.setattr(discovery, 'strategic_candidate_certificates',
                        lambda *args, **kwargs: [(ordinary, quorum, 2), (formation, quorum, 2)])
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    # This is only certificate ordering, not a substitute for native repair proof.
    if repair_ready:
        account.flat_book_capital_repair.status = 'READY'
    selected = discovery._select_qualified_strategic_route(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots={}, leaders={}, risk=_normal_risk(),
        account=account, reference_snapshots={}, strategic_universe=None,
    )
    assert selected is (ordinary if repair_ready else formation)


@pytest.mark.parametrize('family', ('established', 'transition', 'transition_impulse'))
def test_independent_single_authority_is_preserved_but_group_strength_is_ordinary(family):
    route = StrategicRoute(['a'], family, None, False, [], False, True, 'a')
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    assert discovery._new_strategic_formation_open(
        policy, route=route, snapshots={}, quorum_route=StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
    )
    assert not discovery._new_strategic_formation_open(
        policy, route=route, snapshots={}, quorum_route=StrategicQuorumRoute.FULL_COHORT.value,
    )
