"""Ordinary maturity may consume real repair without claiming strategic quality."""
from dataclasses import asdict, replace

import pytest
from test_ordinary_cash_rearm import SYMBOL, _decide, _scenario

from uquant.account.codec import account_from_dict
from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure


def _mature_repair():
    policy, account, dates, panel, leaders, risk = _scenario(sessions=0)
    leaders[SYMBOL] = replace(leaders[SYMBOL], score=.88)
    base = leaders
    risk = replace(risk, evidence={**risk.evidence, "tech_ret120": .50,
                   "ai_fast_return": .01, "declining_ratio": .05, "below_ma20_ratio": .05,
                   "tech_speed": .01, "broad_speed": .01})
    for date in dates[:19]:
        leaders = apply_leader_tenure(base, account=account, cfg=policy.cfg)
        assert not _decide(policy, account, date, panel, leaders, risk)
    return policy, account, dates[19:], panel, leaders, risk


def test_mature_repair_binds_and_fills_one_native_order_after_real_repair():
    policy, account, dates, panel, leaders, risk = _mature_repair()
    before = account.capital_budget_level, account.capital_peak, account.operating_peak
    targets = _decide(policy, account, dates[0], panel, leaders, risk)
    assert len(targets) == 1
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{SYMBOL}", 0) == 0
    assert account.strategic_cash_rearm.qualification_quorum == "MATURE_CORE"
    assert account.strategic_cash_rearm.consumed_order is not None
    restored = account_from_dict(asdict(account))
    fills = ExecutionPlanner(policy.cfg).execute_open(date=dates[1], account=restored, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    assert (account.capital_budget_level, account.capital_peak, account.operating_peak) == before
    assert account.strategic_grant is None and not account.strategic_epochs


@pytest.mark.parametrize("denial", ("quality", "breadth", "sentinel", "reference", "tenure"))
def test_mature_repair_cannot_replace_current_proof_or_risk_permission(denial):
    policy, account, dates, panel, leaders, risk = _mature_repair()
    if denial == "quality":
        leaders[SYMBOL] = replace(leaders[SYMBOL], score=.70, mature=False)
    elif denial == "tenure":
        account.leader_tenure[SYMBOL] = 0
    else:
        key, value = {"breadth": ("breadth20", .1), "sentinel": ("sentinel_freeze_new_risk", True),
                      "reference": ("reference_coverage", .99)}[denial]
        risk = replace(risk, evidence={**risk.evidence, key: value})
    assert not _decide(policy, account, dates[0], panel, leaders, risk)
    assert not account.pending_orders


def test_mature_repair_remainder_loses_permission_when_current_market_weakens():
    policy, account, dates, panel, leaders, risk = _mature_repair()
    assert _decide(policy, account, dates[0], panel, leaders, risk)
    original = account.pending_orders[0]
    panel[SYMBOL].loc[dates[1], "volume"] = 100_000.
    fills = ExecutionPlanner(policy.cfg.override(max_volume_participation=.002)).execute_open(
        date=dates[1], account=account, panel=panel)
    assert fills and account.order_ledger[0].status == "PARTIALLY_FILLED"
    shares = account.positions[SYMBOL].shares
    weakened = replace(risk, evidence={**risk.evidence, "broad_ret20": -.20})
    _decide(policy, account, dates[1], panel, leaders, weakened)
    assert not account.pending_orders
    assert account.order_ledger[0].order_id == original.order_id
    assert account.order_ledger[0].status == "CANCELLED"
    restored = account_from_dict(asdict(account))
    assert restored.positions[SYMBOL].shares == shares
