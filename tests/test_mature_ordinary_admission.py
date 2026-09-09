"""Shared discovery evidence is not permission for immature ordinary exposure."""
from dataclasses import replace

from test_shared_core_qualification import WITNESSES, _decide, _held_book

from uquant.config import DEFAULT_CONFIG


def test_confirmed_shared_witnesses_wait_for_own_maturity_before_ordinary_buy():
    policy, account, dates, panel, leaders, roles = _held_book()
    leaders = {symbol: replace(leader, mature=False) if symbol in WITNESSES else leader
               for symbol, leader in leaders.items()}
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles)
    assert not any(order.side == 'BUY' and not order.grant_id for order in account.pending_orders)
