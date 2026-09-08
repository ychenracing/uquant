"""Observed independent confirmation in strong trends, with native funding gates."""
from dataclasses import replace

import pytest
from test_independent_market_confirmation import _observe
from test_ordinary_cash_rearm import SYMBOL, _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio.strategic.discovery import _independent_market_confirmation
from uquant.portfolio.strategic.qualification_candidates import strategic_candidate_meets_route
from uquant.portfolio.strategic.quorum import strict_absolute_owner_quality

KEY = f"strategic_eligibility:independent_core:{SYMBOL}"
ROUTES = ("established", "transition", "transition_impulse", "persistent_industry", "reversal_industry")


def _trend_risk(risk, *, frozen):
    return replace(risk, freeze_new_risk=frozen,
                   evidence={**risk.evidence, "freeze_new_risk": frozen,
                             "broad_ret120": DEFAULT_CONFIG.strategic_long_cycle_max_tech_ret120 + .10})


@pytest.mark.parametrize("frozen", (False, True))
def test_strong_trend_observes_five_days_but_does_not_override_frozen_funding(frozen):
    fixture = _scenario(sessions=0)
    policy, account, dates, panel, leaders, original = fixture
    account.capital_budget_level = 0
    risk = _trend_risk(original, frozen=frozen)
    assert leaders[SYMBOL].mature
    assert not _independent_market_confirmation(policy, risk), "strategic market predicate stays restrictive"
    for count, date in enumerate(dates[:5], 1):
        snapshots = _observe(fixture, date, risk)
        assert strict_absolute_owner_quality(symbol=SYMBOL, snapshots=snapshots,
                                             leaders=leaders, cfg=DEFAULT_CONFIG)
        assert any(strategic_candidate_meets_route(
            candidate_symbol=SYMBOL, qualification_route=route, snapshots=snapshots,
            leaders=leaders, risk=risk, cfg=DEFAULT_CONFIG) for route in ROUTES)
        _observe(fixture, date, risk)
        assert account.replacement_tenure.get(KEY, 0) == count
        targets = _decide(policy, account, date, panel, leaders, risk)
        if count < 5 or frozen:
            assert not targets and not account.pending_orders
        else:
            assert len(account.pending_orders) == 1
            order = account.pending_orders[0]
            assert order.side == "BUY" and order.target_weight == DEFAULT_CONFIG.core_admission_weight
            assert order.grant_id == order.epoch_id == ""
    assert not account.strategic_epochs and account.strategic_grant is None
    if not frozen:
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
        assert len(fills) == 1 and fills[0].shares > 0
        assert account.positions[SYMBOL].shares == fills[0].shares
    else:
        assert account.cash == account.initial_cash and not account.positions


@pytest.mark.parametrize("lost", ("strict_quality", "all_routes"))
def test_strong_trend_does_not_preserve_confirmation_after_current_evidence_loss(lost):
    fixture = _scenario(sessions=0)
    policy, account, dates, panel, leaders, original = fixture
    for date in dates[:3]:
        _observe(fixture, date, original)
    assert account.replacement_tenure[KEY] == 3
    original_leader = leaders[SYMBOL]
    original_close = panel[SYMBOL]["close"].copy()
    date = dates[3]
    if lost == "strict_quality":
        leaders[SYMBOL] = replace(original_leader, score=DEFAULT_CONFIG.strategic_one_name_min_score - .01)
    else:
        history = panel[SYMBOL].loc[:date]
        panel[SYMBOL].loc[history.index[[-21, -61]], "close"] = history.iloc[-1]["close"] * 2.
    risk = _trend_risk(original, frozen=False)
    snapshots = _observe(fixture, date, risk)
    if lost == "strict_quality":
        assert not strict_absolute_owner_quality(symbol=SYMBOL, snapshots=snapshots,
                                                 leaders=leaders, cfg=DEFAULT_CONFIG)
    else:
        assert strict_absolute_owner_quality(symbol=SYMBOL, snapshots=snapshots,
                                             leaders=leaders, cfg=DEFAULT_CONFIG)
        assert not any(strategic_candidate_meets_route(
            candidate_symbol=SYMBOL, qualification_route=route, snapshots=snapshots,
            leaders=leaders, risk=risk, cfg=DEFAULT_CONFIG) for route in ROUTES)
    assert account.replacement_tenure[KEY] == 0
    assert not _decide(policy, account, date, panel, leaders, risk)
    leaders[SYMBOL] = original_leader
    panel[SYMBOL]["close"] = original_close
    _observe(fixture, dates[4], risk)
    assert account.replacement_tenure[KEY] == 1
    assert not _decide(policy, account, dates[4], panel, leaders, risk)
    assert not account.pending_orders and not account.positions
