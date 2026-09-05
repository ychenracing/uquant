"""Independent core admission combines strict owner quality with current routes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_lifecycle_and_risk import _leader
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.discovery import (
    _observe_resolved_strategic_candidates,
    observe_strategic_candidates,
    strategic_qualification_snapshots,
)
from uquant.portfolio.strategic.qualification_candidates import strategic_candidate_meets_route
from uquant.portfolio.strategic.quorum import (
    StrategicQuorumRoute,
    route_consistent_owner_quality,
    strict_absolute_owner_quality,
)
from uquant.types import AccountState, Opportunity

SYMBOL = "sh600001"
ROUTES = ("established", "transition", "transition_impulse", "persistent_industry", "reversal_industry")
INDEPENDENT = f"strategic_eligibility:independent_core:{SYMBOL}"
ESTABLISHED = f"strategic_eligibility:established:{SYMBOL}"


def _scenario(monkeypatch):
    _, original_panel, _, risk = _inputs()
    panel = {SYMBOL: original_panel[SYMBOL]}
    dates = panel[SYMBOL].index[-8:]
    leaders = {SYMBOL: _leader(SYMBOL, 0.95)}
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1_000_000.0)
    # Exercise ordinary admission without giving this single-name fixture a cohort.
    monkeypatch.setattr(policy, "_strategic_cohort_targets", lambda **kwargs: None)

    def universe(date):
        return build_strategic_universe_roles(
            as_of=str(date.date()), tradable_symbols=(SYMBOL,),
            qualification_reference_symbols=(SYMBOL,), risk_reference_symbols=(),
            industries={SYMBOL: "optical"}, available_symbols={SYMBOL},
        )

    def observe(date):
        return _observe_resolved_strategic_candidates(
            policy, date=date, account=account, risk=risk,
            panel=panel, leaders=leaders, universe=universe(date),
        )

    def allocate(date):
        return policy.allocate(
            date=date, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
            leaders=leaders, account=account, prices={SYMBOL: float(panel[SYMBOL].loc[date, "close"])},
        )

    return policy, account, dates, panel, leaders, risk, universe, observe, allocate


@pytest.mark.parametrize("weak_component", ("leader_score", "secular_score"))
def test_mature_established_candidate_needs_strict_independent_quality(monkeypatch, weak_component):
    _, account, dates, _, leaders, risk, _, observe, allocate = _scenario(monkeypatch)
    leader = leaders[SYMBOL]
    if weak_component == "leader_score":
        leaders[SYMBOL] = replace(leader, score=DEFAULT_CONFIG.strategic_one_name_min_score - 0.01)
    else:
        leaders[SYMBOL] = replace(leader, components={
            **leader.components, "secular_score": DEFAULT_CONFIG.strategic_one_name_min_secular_score - 0.01,
        })
    assert leaders[SYMBOL].mature
    for count, date in enumerate(dates[:DEFAULT_CONFIG.leader_tenure_days], start=1):
        snapshots = observe(date)
        assert not strict_absolute_owner_quality(
            symbol=SYMBOL, snapshots=snapshots, leaders=leaders, cfg=DEFAULT_CONFIG,
        )
        assert route_consistent_owner_quality(
            symbol=SYMBOL, qualification_route="established",
            quorum_route=StrategicQuorumRoute.FULL_COHORT.value,
            snapshots=snapshots, leaders=leaders, risk=risk, cfg=DEFAULT_CONFIG,
        )
        assert account.replacement_tenure[ESTABLISHED] == count
        assert account.replacement_tenure.get(INDEPENDENT, 0) == 0
        assert not allocate(date)


def test_strict_quality_and_absolute_route_need_five_sessions_before_admission(monkeypatch):
    _, account, dates, _, leaders, _, _, observe, allocate = _scenario(monkeypatch)
    assert DEFAULT_CONFIG.leader_tenure_days == 5
    for count, date in enumerate(dates[:5], start=1):
        snapshots = observe(date)
        assert strict_absolute_owner_quality(
            symbol=SYMBOL, snapshots=snapshots, leaders=leaders, cfg=DEFAULT_CONFIG,
        )
        assert account.replacement_tenure[ESTABLISHED] == count
        assert account.replacement_tenure.get(INDEPENDENT, 0) == count
        observe(date)
        assert account.replacement_tenure[INDEPENDENT] == count
        targets = allocate(date)
        if count < 5:
            assert not targets
        else:
            assert len(targets) == 1 and targets[0].symbol == SYMBOL
            assert targets[0].weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
            assert targets[0].mechanism == "LEADER_SELECTION"


@pytest.mark.parametrize("lost_predicate", ("quality", "route"))
def test_loss_of_quality_or_all_routes_resets_independent_confirmation(monkeypatch, lost_predicate):
    policy, account, dates, panel, leaders, risk, _, observe, allocate = _scenario(monkeypatch)
    for date in dates[:3]:
        observe(date)
    assert account.replacement_tenure.get(INDEPENDENT, 0) == 3
    original = leaders[SYMBOL]
    original_close = panel[SYMBOL]["close"].copy()
    date = dates[3]
    if lost_predicate == "quality":
        leaders[SYMBOL] = replace(original, score=DEFAULT_CONFIG.strategic_one_name_min_score - 0.01)
    else:
        history = panel[SYMBOL].loc[:date]
        panel[SYMBOL].loc[history.index[[-21, -61]], "close"] = history.iloc[-1]["close"] * 2.0
        snapshots = strategic_qualification_snapshots(policy, date=date, user_panel=panel, leaders=leaders)
        assert strict_absolute_owner_quality(
            symbol=SYMBOL, snapshots=snapshots, leaders=leaders, cfg=DEFAULT_CONFIG,
        )
        assert not any(strategic_candidate_meets_route(
            candidate_symbol=SYMBOL, qualification_route=route, snapshots=snapshots,
            leaders=leaders, risk=risk, cfg=DEFAULT_CONFIG,
        ) for route in ROUTES)
    observe(date)
    assert account.replacement_tenure[INDEPENDENT] == 0
    assert account.replacement_tenure[ESTABLISHED] == (4 if lost_predicate == "quality" else 0)
    assert not allocate(date)
    leaders[SYMBOL] = original
    panel[SYMBOL]["close"] = original_close
    for count, date in enumerate(dates[4:], start=1):
        observe(date)
        assert account.replacement_tenure[INDEPENDENT] == count
        assert not allocate(date)


def test_public_observer_keeps_same_session_independent_confirmation(monkeypatch):
    policy, account, dates, panel, leaders, risk, universe, _, _ = _scenario(monkeypatch)
    for count, date in enumerate(dates[:3], start=1):
        arguments = dict(
            date=date, user_panel=panel, leaders=leaders, account=account,
            risk=risk, strategic_universe=universe(date),
        )
        first = observe_strategic_candidates(policy, **arguments)
        assert account.replacement_tenure.get(INDEPENDENT, 0) == count
        assert first[SYMBOL]["independent_core"] == count
        assert observe_strategic_candidates(policy, **arguments) == first
        assert account.replacement_tenure[INDEPENDENT] == count
