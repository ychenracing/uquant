"""Witness membership is fixed before candidate-owner ranking."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest
from test_unified_strategic_selection import (
    _leader,
    _observe_group_candidates,
    _risk,
    _snapshot,
    _strategic_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import grant_lifecycle
from uquant.portfolio.strategic.qualification_candidates import strategic_route_candidates


def _absolute(score, *, secular=0.9):
    return {
        **_snapshot(score=score), "secular_score": secular, "history": 250.0,
        "ret240": 0.4, "persistent_ret240": 0.4, "ret5": 0.01,
        "transition_score": 0.0, "short_relative_strength": 0.85,
        "breakout_quality": 0.85,
    }


@pytest.mark.parametrize("certificate", ("ready", "unconfirmed", "not_decisive", "other_industry"))
def test_true_decisive_evidence_precedes_score_only_after_confirmation(monkeypatch, certificate):
    reversal = ("REV_LEAD", "REV_RUNNER", "REV_RESERVE")
    ordinary = ("REG_LEAD", "REG_SECOND", "REG_THIRD")
    snapshots = {}
    leaders = {}
    for index, symbol in enumerate(reversal):
        score = (0.85, 0.75, 0.60)[index]
        snapshots[symbol] = {
            **_absolute(score), "ret240": -0.20, "persistent_ret240": -0.20,
            "ret5": 0.06, "ret20": 0.10 - 0.01 * index,
            "ret60": 0.10 if index == 0 else -0.10, "ret120": -0.10,
            "trend_persistence": 0.9 if index == 0 else 0.3,
        }
        leaders[symbol] = _leader(symbol, score=score, industry="compute")
    for index, symbol in enumerate(ordinary):
        score = 0.95 - 0.01 * index
        snapshots[symbol] = _absolute(score)
        leaders[symbol] = _leader(symbol, score=score, industry="power")
    if certificate == "not_decisive":
        snapshots[reversal[1]]["trend_persistence"] = 0.9
    elif certificate == "other_industry":
        leaders[reversal[1]] = replace(leaders[reversal[1]], industry="memory")
    counts = {symbol: 8 for symbol in snapshots}
    if certificate == "unconfirmed":
        counts.update(dict.fromkeys(reversal, 0))
    risk = replace(_risk(), evidence={
        **_risk().evidence, "risk_anchor_symbols": [],
        "tech_ret120": -0.05, "broad_ret120": -0.05,
    })
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders, counts=counts, risk=risk,
    )

    expected = reversal[0] if certificate == "ready" else ordinary[0]
    assert account.strategic_qualification.candidate_symbol == expected
    assert account.strategic_qualification.qualification_ready
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == expected
    assert account.strategic_cohort_targets.get(expected, 0.0) > 0.0


@pytest.mark.parametrize("family", ("established", "transition"))
def test_only_canonical_witness_members_can_be_group_or_pair_owners(family):
    symbols = ("RANK_FIRST", "RANK_SECOND", "RANK_THIRD", "RANK_FOURTH")
    snapshots = {symbol: {**_absolute(0.95 - 0.01 * index), "transition_score": 0.8}
                 for index, symbol in enumerate(symbols)}
    leaders = {symbol: _leader(symbol, score=0.95 - 0.01 * index, industry="compute")
               for index, symbol in enumerate(symbols)}
    routes = [route for route in strategic_route_candidates(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots=snapshots, leaders=leaders, risk=_risk(),
    ) if route.route == family]
    pairs = [route for route in routes if len(route.symbols) == 2]

    assert {frozenset(route.symbols) for route in pairs} == {frozenset(symbols[:2])}
    assert {route.owner_symbol for route in pairs} == set(symbols[:2])
    assert {frozenset(route.symbols) for route in routes if len(route.symbols) == 3} == {
        frozenset(symbols[:3]),
    }
    outside = [route for route in routes if route.owner_symbol == symbols[-1]]
    assert outside
    assert all(route.symbols == [symbols[-1]] for route in outside)


def _maturity_inputs(*, outsider_score=0.80, second_industry=False):
    mature = ("MATURE_FIRST", "MATURE_SECOND", "MATURE_THIRD")
    snapshots = {symbol: _absolute(0.97 - 0.01 * index, secular=0.95)
                 for index, symbol in enumerate(mature)}
    leaders = {symbol: replace(_leader(symbol, score=0.97 - 0.01 * index, industry="compute"), mature=True)
               for index, symbol in enumerate(mature)}
    snapshots["OUTSIDE"] = _absolute(outsider_score, secular=0.80)
    leaders["OUTSIDE"] = _leader("OUTSIDE", score=outsider_score, industry="compute")
    if second_industry:
        for index, symbol in enumerate(("POWER_FIRST", "POWER_SECOND", "POWER_THIRD")):
            score = 0.85 - 0.01 * index
            snapshots[symbol] = _absolute(score)
            leaders[symbol] = _leader(symbol, score=score, industry="power")
    return snapshots, leaders


def test_rank_outside_immature_owner_cannot_make_mature_witnesses_durable(monkeypatch):
    snapshots, leaders = _maturity_inputs()
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in snapshots}, risk=_risk(),
    )

    assert not account.strategic_qualification.qualification_ready
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}


@pytest.mark.parametrize("strict_single", (True, False))
def test_fixed_witnesses_preserve_independent_single_and_other_industry_group(monkeypatch, strict_single):
    snapshots, leaders = _maturity_inputs(
        outsider_score=0.96 if strict_single else 0.80, second_industry=True,
    )
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in snapshots}, risk=_risk(),
        strict_counts={"OUTSIDE": 8} if strict_single else {},
    )
    expected = "OUTSIDE" if strict_single else "POWER_FIRST"

    assert account.strategic_qualification.candidate_symbol == expected
    assert account.strategic_qualification.qualification_ready
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == expected
    assert account.strategic_cohort_targets.get(expected, 0.0) > 0.0
    if strict_single:
        assert account.strategic_qualification.qualification_quorum == "ABSOLUTE_SINGLE"
        assert account.strategic_cohort_targets == {expected: pytest.approx(DEFAULT_CONFIG.core_admission_weight)}
    else:
        assert account.strategic_qualification.qualification_quorum == "FULL_COHORT"
        assert set(account.strategic_qualification.candidate_symbols) == {
            "POWER_FIRST", "POWER_SECOND", "POWER_THIRD",
        }


def _strong_decisive_inputs(symbols=("A_LEAD", "B_RUNNER", "C_RESERVE")):
    snapshots = {}
    leaders = {}
    for index, symbol in enumerate(symbols):
        score = (0.95, 0.75, 0.60)[index]
        snapshots[symbol] = {
            **_absolute(score), "ret240": -0.20, "persistent_ret240": -0.20,
            "ret5": 0.06, "ret20": 0.10 - 0.01 * index,
            "ret60": 0.10 if index == 0 else -0.10, "ret120": -0.10,
            "trend_persistence": 0.9 if index == 0 else 0.3,
        }
        leaders[symbol] = _leader(symbol, score=score, industry="compute")
    risk = replace(_risk(), evidence={
        **_risk().evidence, "risk_anchor_symbols": [],
        "tech_ret120": -0.05, "broad_ret120": -0.05,
    })
    return snapshots, leaders, risk


def test_singleton_never_carries_full_reversal_context_or_decisive_authority():
    snapshots, leaders, risk = _strong_decisive_inputs()
    routes = strategic_route_candidates(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots=snapshots, leaders=leaders, risk=risk,
    )
    singletons = [route for route in routes if len(route.symbols) == 1]

    assert singletons
    assert all(not route.reversal_groups for route in singletons)
    assert all(not route.synchronized_reversal for route in singletons)
    assert all(route.decisive_reversal_symbol is None for route in singletons)


@pytest.mark.parametrize("symbols", (
    pytest.param(("A_LEAD", "B_RUNNER", "C_RESERVE"), id="runner-before-reserve"),
    pytest.param(("dominant", "runner", "reserve"), id="reserve-before-runner"),
))
def test_stronger_decisive_owner_keeps_real_pair_capital_semantics(monkeypatch, symbols):
    owner, runner, _reserve = symbols
    snapshots, leaders, risk = _strong_decisive_inputs(symbols)
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in snapshots}, risk=risk,
        strict_counts={owner: 8},
    )

    assert account.strategic_qualification.candidate_symbol == owner
    assert account.strategic_qualification.qualification_ready
    assert account.strategic_qualification.qualification_quorum == "FULL_COHORT"
    assert set(account.strategic_qualification.candidate_symbols) == {owner, runner}
    assert account.strategic_grant is not None
    assert account.strategic_cohort_targets == {
        owner: pytest.approx(DEFAULT_CONFIG.strategic_dominant_max_weight),
    }


@pytest.mark.parametrize("symbols", (
    pytest.param(("A_LEAD", "B_RUNNER", "C_RESERVE"), id="strong-witnesses-first"),
    pytest.param(("B_LEAD", "C_RUNNER", "A_RESERVE"), id="weak-reserve-first"),
))
def test_original_reversal_witness_order_uses_evidence_not_signature_names(monkeypatch, symbols):
    snapshots = {}
    leaders = {}
    for index, symbol in enumerate(symbols):
        score = (0.85, 0.80, 0.75)[index]
        snapshots[symbol] = {
            **_absolute(score), "ret240": -0.20, "persistent_ret240": -0.20,
            "ret5": 0.06, "ret20": (0.10, 0.06, -0.30)[index],
            "ret60": -0.10, "ret120": -0.10, "trend_persistence": 0.90,
        }
        leaders[symbol] = _leader(symbol, score=score, industry="compute")
    risk = replace(_risk(), evidence={
        **_risk().evidence, "risk_anchor_symbols": [],
        "tech_ret120": -0.05, "broad_ret120": -0.05,
    })
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in symbols}, risk=risk,
    )
    grant = account.strategic_grant
    assert grant is not None and grant.qualification_quorum == "FULL_COHORT"
    original = (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.candidate_symbol)
    dates = pd.bdate_range("2024-01-02", periods=251)
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    universe = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=symbols,
        qualification_reference_symbols=symbols, risk_reference_symbols=(),
        industries={symbol: "compute" for symbol in symbols}, available_symbols=symbols,
    )
    monkeypatch.setattr(grant_lifecycle, "strategic_qualification_snapshots",
                        lambda self, **kwargs: deepcopy(snapshots))
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    evidence = grant_lifecycle._grant_route_evidence(
        policy, grant=grant, date=dates[-1], user_panel=panel, resolved_panel=panel,
        leaders=leaders, risk=risk, universe=universe,
    )
    assert evidence.raw
    assert set(evidence.symbols) == set(symbols)
    assert grant_lifecycle.revalidate_strategic_grant(
        policy, date=dates[-1], user_panel=panel, leaders=leaders, account=account,
        risk=risk, admission_open=True, weights_now={}, strategic_universe=universe,
    )
    assert not account.strategic_qualification.deployment_blocked
    assert account.strategic_grant is grant
    assert (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.candidate_symbol) == original
