"""Actual legacy fills retain graduation and disaster protection after migration."""
from dataclasses import replace

import pandas as pd
from test_ordinary_pullback_execution import _submitted
from test_ordinary_pullback_lifecycle import _bar

from uquant.account import load_account, save_account
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.ordinary_entry import holding_pullback_entry, pullback_graduated
from uquant.types import Opportunity, Risk


def test_allocator_graduates_actual_legacy_fill_once_and_preserves_disaster_exit(tmp_path):
    policy, account, _, day, panel, leaders, risk = _submitted()
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=day, account=account, panel=panel)
    assert fills
    symbol = fills[0].symbol
    frame = panel[symbol]
    price = float(frame['close'].max()) * 1.1
    date = day + pd.offsets.BDay(1)
    _bar(frame, date, price)
    frame.loc[date, ['ma120', 'ma60', 'ret60']] = (price * .95, price * .98, .1)
    leaders[symbol] = replace(leaders[symbol], mature=True)
    entry = holding_pullback_entry(account, symbol)
    risk = replace(risk, state=Risk.NORMAL, votes=0, freeze_new_risk=False,
                   evidence={**risk.evidence, 'freeze_new_risk': False})
    for _ in range(2):
        policy.allocate(date=date, opportunity=Opportunity.TREND, risk=risk,
                        user_panel=panel, leaders=leaders, account=account, prices={symbol: price})
    assert pullback_graduated(account, entry)
    assert sum(e.get('event') == 'ORDINARY_PULLBACK_GRADUATION'
               for e in account.lifecycle_events) == 1
    path = tmp_path / 'legacy.json'
    save_account(account, path)
    account = load_account(path)
    assert pullback_graduated(account, entry)
    crash = date + pd.offsets.BDay(1)
    price = account.positions[symbol].avg_cost * .79
    _bar(frame, crash, price)
    targets = policy.allocate(date=crash, opportunity=Opportunity.TREND, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices={symbol: price})
    target = next(t for t in targets if t.symbol == symbol)
    assert target.weight == 0 and 'disaster' in target.reason
