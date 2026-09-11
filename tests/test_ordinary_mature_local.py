"""Local mature proof can enter ordinary risk without a same-day impulse."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure
from uquant.portfolio.ordinary import observe_ordinary_market, ordinary_core_entry
from uquant.types import Opportunity, Risk


def test_native_mature_entry_without_impulse_keeps_own_proof_and_foundation_budget():
    policy, account, dates, panel, base, risk = _scenario()
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
    assert {o.symbol for o in account.pending_orders} == {high}
    assert account.pending_orders[0].target_weight == DEFAULT_CONFIG.core_admission_weight
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == high and fills[0].shares > 0
    assert not fills[0].grant_id and not fills[0].epoch_id


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


def test_local_maturity_shares_one_foundation_budget_across_names_and_sessions():
    policy, account, dates, panel, base, risk = _scenario()
    account.leader_tenure.update({s: 20 for s in base})
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    _decide(policy, account, dates[0], panel, base, risk)
    assert len(account.pending_orders) == 1
    order = account.pending_orders[0]
    assert order.target_weight == DEFAULT_CONFIG.core_admission_weight
    _decide(policy, account, dates[1], panel, base, risk)
    assert len(account.pending_orders) == 1
    assert account.pending_orders[0].order_id == order.order_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
    assert fills
    _decide(policy, account, dates[2], panel, base, risk)
    assert not any(o.side == 'BUY' for o in account.pending_orders)
    assert len(account.positions) == 1
