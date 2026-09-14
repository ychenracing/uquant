"""Without mature-cycle capital, qualified ordinary requests keep their initial tier."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.leader import apply_leader_tenure


def test_maturity_only_admission_does_not_receive_independent_core_size():
    policy, account, dates, panel, base, risk = _scenario()
    low = tuple(base)[-1]
    base[low] = replace(base[low], score=.81)
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    for frame in panel.values():
        frame["ret120"] = .5
    for date in dates[:5]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        targets = _decide(policy, account, date, panel, leaders, risk)
    entries = risk.evidence["core_allocation"]["symbols"]
    assert not risk.evidence['core_allocation']['ordinary_market']['leader_cycle_armed']
    high = next(s for s in base if s != low)
    assert entries[high]["entry"]["qualification_quorum"] == "ORDINARY_CORE"
    assert {target.symbol for target in targets} == {high}
    assert all(target.weight <= DEFAULT_CONFIG.core_admission_weight for target in targets)
    assert all(order.target_weight <= DEFAULT_CONFIG.core_admission_weight
               for order in account.pending_orders)
    assert account.capital_budget_level == 0
