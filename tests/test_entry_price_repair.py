"""Admission capital must be supported by current own-price evidence."""
from test_ordinary_trend_budget import _scenario
from test_persistent_formation import SYMBOLS, _decide, _formation_fixture

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio.ordinary import observe_ordinary_market, ordinary_core_entry
from uquant.types import Opportunity


def test_unrepaired_persistent_formation_keeps_existing_immature_cap():
    policy, account, dates, panel, leaders, risk, _, _ = _formation_fixture()
    for frame in panel.values():
        frame["ma60"] = frame["close"] * 1.1
    _decide(policy, account, dates[-2], panel, leaders, risk)
    assert {order.symbol for order in account.pending_orders} == set(SYMBOLS)
    assert all(order.target_weight <= DEFAULT_CONFIG.core_admission_weight
               for order in account.pending_orders)
    assert account.strategic_grant is not None
    assert all(order.epoch_id == account.strategic_grant.epoch_id
               for order in account.pending_orders)


def test_mature_only_entry_requires_own_long_horizon_relative_leadership():
    policy, account, dates, panel, leaders, risk = _scenario()
    symbol = next(iter(leaders))
    account.leader_tenure[symbol] = DEFAULT_CONFIG.leader_tenure_days
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    market = observe_ordinary_market(policy, date=dates[0], opportunity=Opportunity.TREND,
        risk=risk, leaders=leaders, user_panel=panel)
    for own, ready in [(.2, False), (.5, True)]:
        panel[symbol]["ret120"] = own
        entry = ordinary_core_entry(policy, symbol=symbol, score=leaders[symbol], date=dates[0],
            user_panel=panel, account=account, confirmation_days=5, market=market)
        assert (entry["block"] == "READY") == ready
