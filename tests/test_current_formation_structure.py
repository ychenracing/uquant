"""Long-cycle qualification cannot overwrite current entry structure."""

import pandas as pd
import pytest
from test_persistent_formation import SYMBOLS, _january_prefix


def test_current_structure_remains_binding_for_initial_formation():
    engine, account, panel, decision = _january_prefix()
    observed = account.strategic_qualification
    assert observed.qualification_ready
    assert observed.qualification_route == "persistent_industry"
    assert observed.qualification_quorum == "FULL_COHORT"
    entries = decision.risk_summary["core_allocation"]["symbols"]
    assert entries["sz300394"]["entry"]["block"] == "STRUCTURE_NOT_REPAIRED"
    assert len(account.pending_orders) == 1
    order = account.pending_orders[0]
    assert order.symbol == "sz300502" and order.target_weight == pytest.approx(.2)
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == order.symbol
    assert order.epoch_id == account.strategic_grant.epoch_id
    assert not account.positions
    assert set(SYMBOLS) == set(entries)

    fills = engine.execution.execute_open(
        date=pd.Timestamp("2024-01-04"), account=account, panel=panel,
    )
    assert len(fills) == 1 and fills[0].symbol == "sz300502" and fills[0].shares > 0
    assert account.positions["sz300502"].epoch_id == order.epoch_id
    assert account.cash >= 0
