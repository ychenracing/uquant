"""Local mature proof can enter ordinary risk without a same-day impulse."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.leader import apply_leader_tenure
from uquant.portfolio.ordinary import observe_ordinary_market, ordinary_core_entry
from uquant.types import Opportunity, Position, Risk
from uquant.validation.universe import default_ai_universe


def test_native_mature_entry_without_impulse_keeps_proof_but_waits_to_deploy():
    policy, account, dates, panel, base, risk = _scenario()
    for frame in panel.values():
        frame["ret120"] = .5
    low = tuple(base)[-1]
    base[low] = replace(base[low], score=.81)
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for i, date in enumerate(dates[:5]):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _decide(policy, account, date, panel, leaders, risk)
        if i < 4:
            assert not account.pending_orders
    assert not risk.evidence['core_allocation']['ordinary_market']['impulse']
    high = next(s for s in base if s != low)
    allocation = risk.evidence["core_allocation"]
    assert not allocation["ordinary_market"]["persistent_mature_entry_open"]
    assert allocation["symbols"][high]["entry"]["block"] == "READY"
    assert allocation["symbols"][high]["entry_gate"] == "DEPLOYMENT_CONFIRMATION_PENDING"
    assert not account.pending_orders


def test_persistent_mature_market_keeps_local_proof_without_spending_capital():
    policy, account, dates, panel, base, risk = _scenario()
    third = next(symbol for symbol in default_ai_universe().symbols_as_of(str(dates[-1].date()))
                 if symbol not in panel)
    template = next(iter(panel.values()))
    panel[third] = template.copy()
    template_leader = next(iter(base.values()))
    base[third] = replace(template_leader, symbol=third, score=.83,
                          industry=default_ai_universe().industry_of(third, str(dates[-1].date())))
    for frame in panel.values():
        frame["ret120"] = .5
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for date in dates:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _decide(policy, account, date, panel, leaders, risk)
    market = risk.evidence["core_allocation"]["ordinary_market"]
    assert market["persistent_mature_entry_open"]
    high = max(base, key=lambda symbol: base[symbol].score)
    assert risk.evidence["core_allocation"]["symbols"][high]["entry"]["block"] == "READY"
    assert risk.evidence["core_allocation"]["symbols"][high]["entry_gate"] == "DEPLOYMENT_CONFIRMATION_PENDING"
    assert not account.pending_orders


def test_settled_strategic_exit_releases_persistent_local_deployment():
    policy, account, dates, panel, base, risk = _scenario()
    third = next(symbol for symbol in default_ai_universe().symbols_as_of(str(dates[-1].date()))
                 if symbol not in panel)
    template = next(iter(panel.values()))
    panel[third] = template.copy()
    template_leader = next(iter(base.values()))
    base[third] = replace(template_leader, symbol=third, score=.83,
                          industry=default_ai_universe().industry_of(third, str(dates[-1].date())))
    for frame in panel.values():
        frame["ret120"] = .5
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for date in dates[:-1]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _decide(policy, account, date, panel, leaders, risk)
    account.strategic_epochs_completed = 1
    account.strategic_last_exit_date = str(dates[-1].date())
    leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    _decide(policy, account, dates[-1], panel, leaders, risk)
    market = risk.evidence["core_allocation"]["ordinary_market"]
    assert market["persistent_mature_entry_open"]
    assert market["settled_strategic_rotation_open"]
    assert account.pending_orders


def test_armed_mature_cycle_releases_visible_local_deployment():
    policy, account, dates, panel, base, risk = _scenario()
    for frame in panel.values():
        frame["ret120"] = .5
    low = tuple(base)[-1]
    base[low] = replace(base[low], score=.81)
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for date in dates[:5]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _decide(policy, account, date, panel, leaders, risk)
    assert not account.pending_orders
    account.candidate_tenure["leader_cycle_armed"] = 1
    leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    _decide(policy, account, dates[5], panel, leaders, risk)
    assert account.pending_orders
    assert all(order.side == "BUY" and not order.grant_id and not order.epoch_id
               for order in account.pending_orders)


def test_deployed_ordinary_book_can_allocate_to_another_visible_local_core():
    policy, account, dates, panel, base, risk = _scenario()
    for frame in panel.values():
        frame["ret120"] = .5
    incumbent = min(base, key=lambda symbol: base[symbol].score)
    challenger = next(symbol for symbol in base if symbol != incumbent)
    base[incumbent] = replace(base[incumbent], score=.81)
    price = float(panel[incumbent].loc[dates[0], "close"])
    shares = int(DEFAULT_CONFIG.initial_cash * .1 / price)
    account.positions[incumbent] = Position(
        incumbent,
        shares=shares,
        avg_cost=price,
        entry_date=str(dates[0].date()),
        highest_close=price,
    )
    account.cash -= shares * price
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for date in dates[:5]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _decide(policy, account, date, panel, leaders, risk)
    allocation = risk.evidence["core_allocation"]
    assert not allocation["ordinary_market"]["leader_cycle_armed"]
    assert allocation["symbols"][challenger]["entry"]["block"] == "READY"
    assert any(order.side == "BUY" and order.symbol == challenger
               for order in account.pending_orders)


def test_local_maturity_does_not_open_caution_or_frozen_risk():
    policy, _account, dates, panel, base, risk = _scenario()
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for state, frozen in [(Risk.CAUTION, False), (Risk.NORMAL, True)]:
        observed = observe_ordinary_market(policy, date=dates[0], opportunity=Opportunity.TREND,
            risk=replace(risk, state=state, freeze_new_risk=frozen), leaders=base, user_panel=panel)
        assert observed['mature_entry_open'] is False


def test_local_permission_is_current_and_cannot_borrow_strategic_capital():
    policy, account, dates, panel, base, risk = _scenario()
    for frame in panel.values():
        frame["ret120"] = .5
    symbol = next(iter(base))
    account.leader_tenure[symbol] = 5
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    market = observe_ordinary_market(policy, date=dates[0], opportunity=Opportunity.TREND,
        risk=risk, leaders=base, user_panel=panel)
    def entry(date):
        return ordinary_core_entry(policy, symbol=symbol, score=base[symbol], date=date,
            user_panel=panel, account=account, confirmation_days=5, market=market)
    assert entry(dates[0])['block'] == 'READY'
    assert entry(dates[1])['block'] != 'READY'
    account.strategic_cohort_targets[symbol] = .5
    assert entry(dates[0])['block'] != 'READY'


def test_forming_full_retains_strict_proof():
    policy, account, dates, panel, base, risk = _scenario()
    symbol = next(iter(base))
    account.leader_tenure[symbol] = 5
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    account.strategic_qualification.qualification_last_observed_session = str(dates[0].date())
    account.strategic_qualification.qualification_quorum = "FULL_COHORT"
    account.strategic_qualification.qualification_streak = 1
    market = observe_ordinary_market(policy, date=dates[0], opportunity=Opportunity.TREND,
        risk=risk, leaders=base, user_panel=panel)
    result = ordinary_core_entry(policy, symbol=symbol, score=base[symbol], date=dates[0],
        user_panel=panel, account=account, confirmation_days=5, market=market)
    assert result['block'] != 'READY'


def test_local_admission_changes_only_the_long_cycle_upper_bound_case():
    policy, _account, dates, panel, base, risk = _scenario()
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for field, value in [('breadth20', .59), ('broad_ret20', -.01),
                         ('tech_ret20', -.01), ('tech_ret120', .2),
                         ('breadth20', None), ('broad_ret20', float('nan')),
                         ('tech_ret20', True)]:
        changed = replace(risk, evidence={**risk.evidence, field: value})
        result = observe_ordinary_market(policy, date=dates[0], opportunity=Opportunity.TREND,
            risk=changed, leaders=base, user_panel=panel)
        assert result['mature_entry_open'] is False, (field, value)
