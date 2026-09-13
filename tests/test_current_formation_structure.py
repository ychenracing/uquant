"""Completed own long-cycle proof admits the original full formation."""

from test_persistent_formation import SYMBOLS, _january_prefix


def test_current_own_persistent_formation_admits_full_cohort():
    _, account, _, _ = _january_prefix()
    observed = account.strategic_qualification
    assert observed.qualification_ready
    assert observed.qualification_route == "persistent_industry"
    assert observed.qualification_quorum == "FULL_COHORT"
    assert {order.symbol for order in account.pending_orders} == set(SYMBOLS)
    assert all(order.epoch_id for order in account.pending_orders)
    assert account.strategic_grant is not None
    assert not account.positions
