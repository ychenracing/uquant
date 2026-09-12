"""Earned repair cannot be spent using only ordinary maturity evidence."""
from dataclasses import asdict

from test_mature_cash_rearm import _mature_repair
from test_ordinary_cash_rearm import SYMBOL, _decide

from uquant.account.codec import account_from_dict
from uquant.execution import ExecutionPlanner


def test_maturity_only_preserves_ready_repair_without_order_or_synthetic_epoch():
    policy, account, dates, panel, leaders, risk = _mature_repair()
    before = account.capital_budget_level, account.capital_peak, account.operating_peak
    assert not _decide(policy, account, dates[0], panel, leaders, risk)
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == 20
    assert account.replacement_tenure.get(
        f"strategic_eligibility:independent_core:{SYMBOL}", 0) == 0
    assert not account.pending_orders
    assert account.strategic_cash_rearm.consumed_order is None
    assert account.strategic_grant is None and not account.strategic_epochs
    restored = account_from_dict(asdict(account))
    assert not ExecutionPlanner(policy.cfg).execute_open(date=dates[1], account=restored, panel=panel)
    assert (restored.capital_budget_level, restored.capital_peak, restored.operating_peak) == before
