"""Fresh ordinary trend capital requires a current positive long market leg."""
import math

import pytest
from test_shared_core_qualification import CHALLENGER, _decide, _held_book
from test_strategic_probe_holding import OWNER
from test_strategic_universe_quorum import _risk

from uquant.config import DEFAULT_CONFIG


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
    (-.04, -.07), (0., 0.), (None, .04), (.04, None),
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
