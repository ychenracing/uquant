"""Weak long-market evidence shares one existing ordinary admission allowance."""
import math
from dataclasses import replace

import pytest
from test_shared_core_qualification import CHALLENGER, WITNESSES, _decide, _held_book
from test_single_immature_core import _early_book
from test_strategic_probe_holding import OWNER
from test_strategic_universe_quorum import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.types import Risk


def _confirmed_book(risk, *, require_ready=True):
    policy, account, dates, panel, leaders, roles = _held_book()
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
    entry = risk.evidence["core_allocation"]["symbols"][CHALLENGER]["entry"]
    if require_ready:
        assert entry["block"] == "READY"
        assert entry["qualification_quorum"] == "FULL_COHORT"
    return policy, account, dates, panel, leaders, roles


@pytest.mark.parametrize("broad,tech", (
    (None, .04), (.04, None),
    (float("nan"), .04), (.04, float("inf")), (True, .04),
))
def test_ready_shared_certificate_cannot_create_fresh_capital_without_long_trend(broad, tech):
    risk = _risk()
    for key, value in (("broad_ret120", broad), ("tech_ret120", tech)):
        if value is None:
            risk.evidence.pop(key, None)
        else:
            risk.evidence[key] = value
    complete = all(type(value) in {int, float} and math.isfinite(value) for value in (broad, tech))
    _, account, _, _, _, _ = _confirmed_book(risk, require_ready=complete)
    assert not any(order.side == "BUY" and not order.grant_id for order in account.pending_orders)
    assert account.positions[OWNER].shares > 0
    assert account.strategic_grant is not None


@pytest.mark.parametrize("broad,tech", ((.04, -.07), (-.04, .07)))
def test_one_positive_leg_preserves_existing_nominal_budget_without_another_clock(broad, tech):
    risk = _risk()
    risk.evidence.update(broad_ret120=broad, tech_ret120=tech)
    _, account, _, _, _, _ = _confirmed_book(risk)
    order = next(order for order in account.pending_orders
                 if order.symbol == CHALLENGER and order.side == "BUY")
    row = risk.evidence["core_allocation"]["symbols"][CHALLENGER]
    assert order.target_weight == pytest.approx(row["proposal_weight"])
    assert row["budget_checks"][0]["desired_increment"] == pytest.approx(DEFAULT_CONFIG.trend_entry_gross / 3)
    assert order.grant_id == order.epoch_id == ""


def test_later_weak_market_does_not_cancel_original_qualified_pending_buy():
    risk = _risk()
    risk.evidence.update(broad_ret120=.04, tech_ret120=-.07)
    policy, account, dates, panel, leaders, roles = _confirmed_book(risk)
    original = next(order for order in account.pending_orders
                    if order.symbol == CHALLENGER and order.side == "BUY")
    identity = (original.order_id, original.event_id, original.target_weight)
    shares = account.positions[OWNER].shares
    risk.evidence.update(broad_ret120=-.04, tech_ret120=-.07)
    _decide(policy, account, dates[2], panel, leaders, roles, risk=risk)
    remaining = next(order for order in account.pending_orders
                     if order.symbol == CHALLENGER and order.side == "BUY")
    assert (remaining.order_id, remaining.event_id, remaining.target_weight) == identity
    assert account.positions[OWNER].shares == shares


@pytest.mark.parametrize("broad,tech", ((-.04, -.07), (0., 0.)))
def test_weak_market_shares_one_allowance_among_all_selected_ordinary_candidates(broad, tech):
    risk = _risk()
    risk.evidence.update(broad_ret120=broad, tech_ret120=tech)
    _, account, _, _, _, _ = _confirmed_book(risk)
    buys = [o for o in account.pending_orders if o.side == "BUY" and not o.grant_id]
    assert {o.symbol for o in buys} == set(WITNESSES)
    assert sum(o.target_weight for o in buys) == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    for order in buys:
        assert order.target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight / len(WITNESSES))
    # The true strategic owner remains outside this ordinary allowance.
    assert account.positions[OWNER].shares > 0
    assert account.positions[OWNER].grant_id


@pytest.mark.parametrize("execution", ("pending", "partial", "filled", "unsettled-sell"))
def test_existing_ordinary_capital_reserves_weak_market_allowance(execution):
    policy, account, dates, panel, leaders, roles, risk = _early_book()
    original = next(o for o in account.pending_orders if o.symbol == CHALLENGER)
    assert original.target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    if execution != "pending":
        if execution == "partial":
            frame = panel[CHALLENGER]
            frame.loc[dates[2], "volume"] = 100_000.
            frame.loc[dates[2], "amount"] = float(frame.loc[dates[2], "close"]) * 100_000.
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
        assert any(f.symbol == CHALLENGER and f.shares > 0 for f in fills)
        if execution == "partial":
            assert next(o for o in account.order_ledger if o.order_id == original.order_id).status == "PARTIALLY_FILLED"
        if execution == "unsettled-sell":
            liquidation = replace(risk, state=Risk.RISK_OFF, target_gross_cap=0.,
                                  freeze_new_risk=True, reduction_level=3)
            _decide(policy, account, dates[3], panel, leaders, roles, risk=liquidation)
            assert any(o.side == "SELL" and o.symbol == CHALLENGER for o in account.pending_orders)
    # Maturity releases the independent early-slot gate, isolating committed capital.
    leaders = {symbol: replace(leader, mature=True) for symbol, leader in leaders.items()}
    risk.evidence.update(broad_ret120=-.04, tech_ret120=-.07)
    shares = account.positions[CHALLENGER].shares if CHALLENGER in account.positions else 0
    _decide(policy, account, dates[3], panel, leaders, roles, risk=risk)
    assert not any(o.side == "BUY" and o.symbol in set(WITNESSES) - {CHALLENGER}
                   for o in account.pending_orders)
    assert (account.positions[CHALLENGER].shares if CHALLENGER in account.positions else 0) == shares
    if execution in {"pending", "partial"}:
        remaining = next(o for o in account.pending_orders if o.symbol == CHALLENGER and o.side == "BUY")
        assert (remaining.order_id, remaining.event_id) == (original.order_id, original.event_id)


@pytest.mark.parametrize("reserved", (.08, .12, .2))
def test_weak_market_new_capital_uses_only_uncommitted_remainder(reserved):
    from test_ordinary_pullback_lifecycle import _ordinary_target, _plan

    policy, account, dates, panel, leaders, roles = _held_book()
    # A native submitted ordinary order reserves part or all of the allowance.
    orders = _plan(account, dates[0], panel, (_ordinary_target(CHALLENGER, reserved),))
    assert any(o.symbol == CHALLENGER and o.side == "BUY" for o in orders)
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    shares = account.positions[CHALLENGER].shares
    values = {symbol: position.shares * float(panel[symbol].loc[dates[2], "close"])
              for symbol, position in account.positions.items()}
    reserved = values[CHALLENGER] / (account.cash + sum(values.values()))
    risk = _risk()
    risk.evidence.update(broad_ret120=-.04, tech_ret120=-.07)
    for date in dates[1:1 + DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
    new = [o for o in account.pending_orders if o.side == "BUY" and o.symbol in set(WITNESSES) - {CHALLENGER}]
    nominal = max(0., DEFAULT_CONFIG.core_admission_weight - reserved) / 2
    if nominal < DEFAULT_CONFIG.min_trade_weight:
        assert not new
        assert not any(o.side == "SELL" for o in account.pending_orders)
    else:
        assert {o.symbol for o in new} == set(WITNESSES) - {CHALLENGER}
        assert sum(o.target_weight for o in new) == pytest.approx(DEFAULT_CONFIG.core_admission_weight - reserved)
        assert all(o.target_weight == pytest.approx(nominal) for o in new)
    assert account.positions[CHALLENGER].shares == shares
