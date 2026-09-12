"""Long-cycle qualification cannot overwrite current entry structure."""

from test_persistent_formation import SYMBOLS, _january_prefix


def test_current_structure_remains_binding_for_initial_formation():
    _, account, _, decision = _january_prefix()
    observed = account.strategic_qualification
    assert observed.qualification_ready
    assert observed.qualification_route == "persistent_industry"
    assert observed.qualification_quorum == "FULL_COHORT"
    entries = decision.risk_summary["core_allocation"]["symbols"]
    assert entries["sz300394"]["entry"]["block"] == "STRUCTURE_NOT_REPAIRED"
    assert not any(order.epoch_id for order in account.pending_orders)
    assert account.strategic_grant is None
    assert not account.positions
    assert set(SYMBOLS) == set(entries)
