"""Current-account checks for persistent mature leader allocation."""
from dataclasses import replace

import pytest
from test_ordinary_trend_budget import _scenario

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.leader import apply_leader_tenure
from uquant.types import Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


@pytest.mark.parametrize('frozen', [False, True])
def test_persistent_leader_cycle_uses_group_capital_only_when_unfrozen(frozen):
    policy, account, dates, panel, base, risk = _scenario()
    risk = replace(risk, freeze_new_risk=frozen)
    risk.evidence.update(broad_ret120=.04, tech_ret120=.04, ai_fast_return=.01,
                         broad_speed=.01, tech_speed=.01, tech_ret20=.01,
                         breadth20=.8)
    for date in dates[:7]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        prices = {s: float(f.loc[date, 'close']) for s, f in panel.items()}
        args = dict(date=date, opportunity=Opportunity.STRONG_TREND, risk=risk,
                    user_panel=panel, leaders=leaders, account=account, prices=prices)
        targets = policy.allocate(**args)
        before = account.candidate_tenure.get('leader_cycle_evidence', 0)
        assert policy.allocate(**args) == targets
        assert account.candidate_tenure.get('leader_cycle_evidence', 0) == before
    if frozen:
        assert not any(t.weight > 0 for t in targets)
        return
    assert sum(t.weight for t in targets) > 2 * DEFAULT_CONFIG.core_admission_weight
    assert {t.symbol for t in targets if t.weight > 0} == set(base)
    previous = list(account.pending_orders)
    attributed = tuple(item for target in targets for item in attach_target_attribution(
        leaders[target.symbol].industry, REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=str(dates[6].date()), targets=(target,), retained_orders=previous))
    planned = plan_orders(signal_date=str(dates[6].date()), targets=attributed,
                          account=account, prices=prices, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(account=account, previous=previous,
        current=merged, submitted_date=str(dates[6].date())))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[7], account=account, panel=panel)
    assert {f.symbol for f in fills if f.side == 'BUY' and f.shares > 0} == set(base)
    assert all(not f.grant_id and not f.epoch_id for f in fills)


def test_capacity_counts_qualified_incumbents_before_subtracting_fresh_slots():
    from uquant.portfolio.allocation_book import AllocationBook
    from uquant.portfolio.leaders.cycle import mature_cycle_weights

    policy, account, dates, panel, leaders, risk = _scenario()
    held, peer = sorted(leaders)
    fresh = "sz000636"
    assert fresh not in leaders
    leaders = {s: replace(leader, score=.86) for s, leader in leaders.items()}
    leaders[fresh] = replace(leaders[held], symbol=fresh, industry="passives")
    panel[fresh] = panel[held].copy()
    account.dynamic_k = 2
    account.candidate_tenure['leader_cycle_armed'] = 1
    for date in dates[:7]:
        book = AllocationBook(policy, date, risk, panel, leaders, account,
                              {s: float(f.loc[date, 'close']) for s, f in panel.items()},
                              {held: .2, peer: .2}, set(), {}, {held: .2, peer: .2},
                              {held: .2, peer: .2}, .6)
        weights = mature_cycle_weights(book, [fresh], Opportunity.STRONG_TREND)
    assert account.dynamic_k == 3
    assert weights == {fresh: DEFAULT_CONFIG.core_admission_weight}
