"""Non-impulse entry needs the current credible stock, not only old tenure."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure


def test_old_mature_tenure_waits_for_current_credible_observations_then_fills():
    policy, account, _, panel, base, risk = _scenario()
    dates = next(iter(panel.values())).index[-12:]
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         broad_ret20=.03, tech_ret20=.10, tech_ret120=.50, breadth20=.8)
    weak = {symbol: replace(leader, score=.81) for symbol, leader in base.items()}
    for date in dates[:5]:
        leaders = apply_leader_tenure(weak, account=account, cfg=policy.cfg)
        assert not _decide(policy, account, date, panel, leaders, risk)
    for index, date in enumerate(dates[5:10]):
        leaders = apply_leader_tenure(base, account=account, cfg=policy.cfg)
        targets = _decide(policy, account, date, panel, leaders, risk)
        assert bool(targets) == (index == 4)
        assert not risk.evidence['core_allocation']['ordinary_market']['impulse']
        assert all(account.leader_tenure[symbol] >= 5 for symbol in base)
    assert account.strategic_grant is None and not account.strategic_epochs
    assert all(account.replacement_tenure['ordinary_repair_maturity:' + symbol] == 5 for symbol in base)
    fills = ExecutionPlanner(policy.cfg).execute_open(date=dates[10], account=account, panel=panel)
    assert fills and all(fill.side == 'BUY' and fill.shares > 0 for fill in fills)
    assert not account.pending_orders
