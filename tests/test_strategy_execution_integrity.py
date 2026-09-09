"""Real executor regression for same-order strategic partial retries."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest
from test_strategic_cash_rearm_state import _authorize, _ready_account
from test_strategic_grant_recovery import _account_with_grant, _submit, _tradable_rows

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.strategic_rearm import validate_strategic_cash_rearm_state
from uquant.portfolio.strategic.authority import assess_strategic_capital_authority


@pytest.mark.parametrize("next_open", (5.0, 10.0, 20.0))
def test_strategic_partial_retry_keeps_order_quantity_identity_through_restart(next_open: float) -> None:
    account = _account_with_grant()
    original_id = _submit(account)
    planner = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=0.002))
    planner.execute_open(date=pd.Timestamp('2026-01-06'), account=account,
                         panel={'sz300308': _tradable_rows('2026-01-05', '2026-01-06', volume=100_000)})
    order = account.order_ledger[0]
    requested, remaining = order.requested_shares, order.remaining_shares
    assert order.status == 'PARTIALLY_FILLED'
    assert account.pending_orders[0].order_id == original_id
    assert not order.cancel_reason and not order.replaced_by
    restored = account_from_dict(account.to_dict())
    frame = _tradable_rows('2026-01-05', '2026-01-06', '2026-01-07')
    frame.loc[pd.Timestamp('2026-01-06'), 'close'] = next_open
    frame.loc[pd.Timestamp('2026-01-07'), ['open', 'high', 'low', 'close']] = [
        next_open, next_open * 1.02, next_open * 0.98, next_open,
    ]
    frame.loc[pd.Timestamp('2026-01-07'), 'amount'] = next_open * 10_000_000
    planner.execute_open(date=pd.Timestamp('2026-01-07'), account=restored,
                         panel={'sz300308': frame})
    assert len(restored.order_ledger) == 1
    assert restored.order_ledger[0].requested_shares == requested
    if next_open <= 10.0:
        assert restored.fills[-1].shares == remaining
    else:
        assert restored.fills[-1].shares < remaining
        assert restored.order_ledger[0].cancel_reason == 'target already satisfied'
    assert (restored.order_ledger[0].filled_shares + restored.order_ledger[0].remaining_shares
            == requested)
    assert {f.order_id for f in restored.fills} == {original_id}
    assert {f.grant_id for f in restored.fills} == {order.grant_id}
    assert assess_strategic_capital_authority(restored).unsettled_order_ids == ()


def test_rejected_zero_budget_observation_roundtrips_without_authorizing() -> None:
    account = _ready_account()
    account.capital_budget_level = 0
    observation = _authorize(account)
    assert observation.status == 'OBSERVING' and not observation.authorized
    assert observation.capital_budget_level == 0
    account_from_dict(account.to_dict(), require_hashes=False)
    for status, authorized in [('AUTHORIZED', True), ('CONSUMED', False)]:
        invalid = replace(observation, status=status, authorized=authorized)
        with pytest.raises(ValueError, match='capital budget'):
            validate_strategic_cash_rearm_state(invalid)
