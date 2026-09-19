"""A repaired account reserves scarce capital for independent qualification."""

from dataclasses import asdict, replace

from test_ordinary_cash_rearm import SYMBOL, _decide, _scenario

from uquant.account.codec import account_from_dict
from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure


def _mature_repair():
    policy, account, dates, panel, leaders, risk = _scenario(sessions=0)
    leaders[SYMBOL] = replace(leaders[SYMBOL], score=.88)
    base = leaders
    risk = replace(
        risk,
        evidence={
            **risk.evidence,
            "tech_ret120": .50,
            "ai_fast_return": .01,
            "declining_ratio": .05,
            "below_ma20_ratio": .05,
            "tech_speed": .01,
            "broad_speed": .01,
        },
    )
    for date in dates[:19]:
        leaders = apply_leader_tenure(base, account=account, cfg=policy.cfg)
        assert not _decide(policy, account, date, panel, leaders, risk)
    return policy, account, dates[19:], panel, leaders, risk


def test_maturity_only_preserves_ready_repair_without_order_or_synthetic_epoch():
    policy, account, dates, panel, leaders, risk = _mature_repair()
    before = account.capital_budget_level, account.capital_peak, account.operating_peak

    assert not _decide(policy, account, dates[0], panel, leaders, risk)
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == 20
    assert account.replacement_tenure.get(
        f"strategic_eligibility:independent_core:{SYMBOL}", 0,
    ) == 0
    assert not account.pending_orders
    assert account.strategic_cash_rearm.consumed_order is None
    assert account.strategic_grant is None and not account.strategic_epochs

    restored = account_from_dict(asdict(account))
    assert not ExecutionPlanner(policy.cfg).execute_open(
        date=dates[1], account=restored, panel=panel,
    )
    assert (restored.capital_budget_level, restored.capital_peak, restored.operating_peak) == before
