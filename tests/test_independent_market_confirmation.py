"""Independent CORE observes complete data; strategic market bounds stay separate."""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from test_ordinary_cash_rearm import SYMBOL, _decide, _roles, _scenario

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio.strategic.discovery import (
    _independent_market_confirmation,
    _observe_resolved_strategic_candidates,
    observe_strategic_candidates,
)

KEY = f"strategic_eligibility:independent_core:{SYMBOL}"
SHARED = f"strategic_eligibility:established:{SYMBOL}"
MARKET_KEYS = ("breadth20", "broad_ret20", "tech_ret20", "broad_ret120", "tech_ret120")


def _observe(fixture, date, risk, *, public=False):
    policy, account, _, panel, leaders, _ = fixture
    if public:
        return observe_strategic_candidates(
            policy, date=date, user_panel=panel, leaders=leaders, account=account,
            risk=risk, strategic_universe=_roles(date),
        )
    return _observe_resolved_strategic_candidates(
        policy, date=date, account=account, risk=risk, panel=panel,
        leaders=leaders, universe=_roles(date),
    )


@pytest.mark.parametrize("public", (False, True), ids=("resolved", "public"))
def test_five_actual_healthy_market_sessions_confirm_once_per_session(public):
    fixture = _scenario(sessions=0)
    _, account, dates, _, _, risk = fixture
    for count, date in enumerate(dates[:5], 1):
        _observe(fixture, date, risk, public=public)
        _observe(fixture, date, risk, public=public)
        assert account.replacement_tenure[KEY] == count
        assert account.replacement_tenure[SHARED] == count
    assert DEFAULT_CONFIG.leader_tenure_days == 5


@pytest.mark.parametrize("key", MARKET_KEYS)
@pytest.mark.parametrize("value", (None, float("nan"), float("inf"), True), ids=("absent", "nan", "inf", "boolean"))
def test_independent_confirmation_requires_each_current_finite_market_measure(key, value):
    fixture = _scenario(sessions=0)
    _, account, dates, _, _, risk = fixture
    evidence = dict(risk.evidence)
    if value is None:
        evidence.pop(key)
    else:
        evidence[key] = value
    _observe(fixture, dates[0], replace(risk, evidence=evidence), public=True)
    assert account.replacement_tenure.get(KEY, 0) == 0
    assert account.replacement_tenure[SHARED] == 1


@pytest.mark.parametrize("failure", ("breadth", "broad_short", "tech_short", "weak_long", "overheated"))
def test_strategic_market_bounds_do_not_reset_current_independent_quality(failure):
    fixture = _scenario(sessions=0)
    _, account, dates, _, _, risk = fixture
    for date in dates[:4]:
        _observe(fixture, date, risk)
    evidence = dict(risk.evidence)
    if failure == "breadth":
        evidence["breadth20"] = DEFAULT_CONFIG.high_confidence_entry_breadth - .01
    elif failure in {"broad_short", "tech_short"}:
        evidence["broad_ret20" if failure == "broad_short" else "tech_ret20"] = (
            DEFAULT_CONFIG.strategic_transition_impulse_min_market_ret20 - .01
        )
    elif failure == "weak_long":
        evidence.update(broad_ret120=DEFAULT_CONFIG.recovery_transition_weak_leg_ret120,
                        tech_ret120=DEFAULT_CONFIG.recovery_transition_weak_leg_ret120)
    else:
        evidence["broad_ret120"] = DEFAULT_CONFIG.strategic_long_cycle_max_tech_ret120 + .01
    changed = replace(risk, evidence=evidence)
    assert not _independent_market_confirmation(fixture[0], changed)
    _observe(fixture, dates[4], changed)
    assert account.replacement_tenure.get(KEY, 0) == 5
    assert account.replacement_tenure[SHARED] == 5
    _observe(fixture, dates[5], risk)
    assert account.replacement_tenure[KEY] == 6
    assert account.replacement_tenure[SHARED] == 6


def test_market_evidence_loss_cancels_native_partial_buy_but_keeps_real_holding():
    policy, account, dates, panel, leaders, original_risk = _scenario(sessions=0)
    account.capital_budget_level = 0
    risk = replace(original_risk, freeze_new_risk=False,
                   evidence={**original_risk.evidence, "freeze_new_risk": False})
    for date in dates[:5]:
        _decide(policy, account, date, panel, leaders, risk)
    assert len(account.pending_orders) == 1
    original = account.pending_orders[0]
    panel[SYMBOL].loc[dates[5], "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=dates[5], account=account, panel=panel,
    )
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].status == "PARTIALLY_FILLED"
    shares, cash = account.positions[SYMBOL].shares, account.cash
    incomplete = dict(risk.evidence)
    incomplete.pop("broad_ret20")
    weakened = replace(risk, evidence=incomplete)
    _decide(policy, account, dates[5], panel, leaders, weakened)
    assert not account.pending_orders, "lost admission alone must neither buy nor sell held shares"
    assert account.order_ledger[0].order_id == original.order_id
    assert account.order_ledger[0].status == "CANCELLED"
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[6], panel, leaders, weakened)
    assert not account.pending_orders
    assert account.positions[SYMBOL].shares == shares and account.cash == cash
