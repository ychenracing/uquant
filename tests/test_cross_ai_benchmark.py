"""Bounded checks of benchmark execution and fail-closed evidence."""
from __future__ import annotations

import pandas as pd
import pytest

from research.cross_ai_benchmark import correlation_groups, submit_targets
from uquant.account import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.types import AccountState, Target


def test_benchmark_partial_fill_survives_account_roundtrip_without_duplicate_order():
    engine = ProductionEngine('data/frozen')
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    date = pd.Timestamp('2023-01-03')
    target = Target('sz300308', 0.30, 'CORE', 0.0, 1.0, 'research monthly trend',
                    origin_subsystem='LEADER', mechanism='LEADER_SELECTION', origin_lifecycle='CORE')
    submit_targets(engine, account, date, (target,), {'sz300308': 10.0})
    assert account.pending_orders and not account.fills
    frame = pd.DataFrame({'open': [10.0] * 3, 'high': [10.2] * 3, 'low': [9.8] * 3,
                          'close': [10.0] * 3, 'volume': [10_000_000.0] * 3,
                          'amount': [100_000_000.0] * 3},
                         index=pd.to_datetime(['2023-01-03', '2023-01-04', '2023-01-05']))
    engine.execution.execute_open(date=pd.Timestamp('2023-01-04'), account=account,
                                  panel={'sz300308': frame})
    assert len(account.fills) == 1 and account.positions['sz300308'].shares > 0
    restored = account_from_dict(account.to_dict(), require_hashes=False)
    engine.execution.execute_open(date=pd.Timestamp('2023-01-05'), account=restored,
                                  panel={'sz300308': frame})
    assert len(restored.fills) == 2
    assert len(restored.order_ledger) == 1
    assert restored.fills[0].order_id == restored.fills[1].order_id
    assert restored.cash < account.cash
    assert not restored.strategic_epochs and restored.strategic_grant is None


def test_missing_correlation_is_explicit_and_nonfinite_price_fails():
    dates = pd.bdate_range('2023-01-03', periods=4)
    panel = {'sz300308': pd.DataFrame({'close': [10.0, 10.1, 10.3, 10.2]}, index=dates),
             'sz300502': pd.DataFrame({'close': [20.0, 20.2, 20.6, 20.4]}, index=dates)}
    groups, missing = correlation_groups(panel, list(panel), dates[-1], DEFAULT_CONFIG)
    assert missing == {('sz300308', 'sz300502')}
    assert groups == [{'sz300308'}, {'sz300502'}]
    panel['sz300308'].loc[dates[-1], 'close'] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        correlation_groups(panel, list(panel), dates[-1], DEFAULT_CONFIG)
