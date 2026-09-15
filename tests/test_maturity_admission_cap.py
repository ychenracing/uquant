"""Without confirmed market deployment, qualified ordinary requests remain visible."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.leader import apply_leader_tenure


def test_maturity_only_admission_waits_instead_of_borrowing_independent_size():
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
    assert entries[high]["entry_gate"] == "DEPLOYMENT_CONFIRMATION_PENDING"
    assert not targets and not account.pending_orders
    assert account.capital_budget_level == 0
