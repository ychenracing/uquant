"""Repeated shock snapshots remain valid across durable account restarts."""
from dataclasses import asdict

import pandas as pd
import pytest

from uquant.account.codec import account_from_dict
from uquant.risk.protected_recovery import capture_protected_holdings
from uquant.types import AccountState, Position


@pytest.mark.parametrize('prior,new_weight', [(0.8, 0.6), (0.3, 0.2), (1.0, 0.4)])
@pytest.mark.parametrize('anchors', [False, True])
def test_capture_bounds_mixed_date_rights(prior, new_weight, anchors):
    date = pd.Timestamp('2025-01-10')
    account = AccountState.empty(10_000.0)
    account.last_shock_date = '2025-01-06'
    account.positions = {
        'sz300308': Position('sz300308', 100, 10.0, '2025-01-02', 10.0),
        'sz300502': Position('sz300502', int(new_weight * 1000), 10.0, '2025-01-09', 10.0),
    }
    account.cash = 10_000.0 - sum(p.shares * 10.0 for p in account.positions.values())
    if anchors:
        account.anchor_weights = {'sz300308': prior}
    else:
        account.protected_weights = {'sz300308': prior}
    panel = {s: pd.DataFrame({'close': [10.0]}, index=[date]) for s in account.positions}
    before = asdict(account)

    capture_protected_holdings(account=account, date=date, user_panel=panel, equity=10_000.0)

    scale = max(1.0, prior + new_weight)
    assert account.protected_weights == pytest.approx({
        'sz300308': prior / scale, 'sz300502': new_weight / scale,
    })
    assert sum(account.protected_weights.values()) <= 1.0 + 1e-12
    assert account.cash == before['cash']
    assert asdict(account)['positions'] == before['positions']
    protected = dict(account.protected_weights)
    account.last_shock_date = str(date.date())
    capture_protected_holdings(account=account, date=date, user_panel=panel, equity=10_000.0)
    assert account.protected_weights == protected


def test_account_still_rejects_overcommitted_protection():
    account = AccountState.empty(10_000.0)
    account.protected_weights = {'sz300308': 0.8, 'sz300502': 0.6}
    with pytest.raises(RuntimeError, match='protected_weights total weight exceeds one'):
        account_from_dict(asdict(account), require_hashes=False)


def test_native_account_with_mixed_date_protection_roundtrips():
    from test_strategic_cohort_deployment_settlement import _native_full

    _, account, dates, panel, _, _ = _native_full()
    date = dates[0]
    symbols = tuple(account.positions)
    account.protected_weights = {symbols[0]: 0.9}
    equity = account.cash + sum(p.shares * panel[s].loc[date, 'close']
                                for s, p in account.positions.items())
    assert 0.9 + sum(account.positions[s].shares * panel[s].loc[date, 'close'] / equity
                     for s in symbols[1:]) > 1.0
    capture_protected_holdings(account=account, date=date, user_panel=panel, equity=equity)
    restored = account_from_dict(asdict(account))
    assert sum(restored.protected_weights.values()) == pytest.approx(1.0)
    assert restored.protected_weights == account.protected_weights
