"""Fresh maturity-only capital remains in the existing admission tier."""
from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.leader import apply_leader_tenure


def test_maturity_only_admission_does_not_receive_independent_core_size():
    policy, account, dates, panel, base, risk = _scenario()
    for frame in panel.values():
        frame["ret120"] = .5
    for date in dates[:5]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        targets = _decide(policy, account, date, panel, leaders, risk)
    entries = risk.evidence["core_allocation"]["symbols"]
    assert all(entries[s]["entry"]["qualification_quorum"] == "ORDINARY_CORE"
               for s in leaders)
    assert len(targets) == len(leaders)
    assert all(target.weight <= DEFAULT_CONFIG.core_admission_weight for target in targets)
    assert all(order.target_weight <= DEFAULT_CONFIG.core_admission_weight
               for order in account.pending_orders)
    assert account.capital_budget_level == 0
