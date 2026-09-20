"""Reconcile native/resumed originals and compute the fixed common-close metrics."""
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path


def drawdown(values, peak=0.0):
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, 1 - value / peak)
    return maximum


def read_metrics(path, common='2025-01-16', end='2026-08-05'):
    from uquant.account import account_from_dict
    path = Path(path)
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert data['completed'] is True and not data.get('error'), path
    result = data['result']
    account = account_from_dict(result['final_account'], require_hashes=True)
    curve = result['equity_curve']
    dates = [row['date'] for row in curve]
    assert dates == sorted(set(dates)) and dates[-1] == end
    with Path('data/frozen/sh000300.csv').open() as stream:
        calendar = [row['date'][:10] for row in csv.DictReader(stream)]
    assert dates == sorted(day for day in calendar if dates[0] <= day <= end)
    equity = {row['date']: float(row['equity']) for row in curve}
    assert common in equity and all(math.isfinite(v) and v > 0 for v in equity.values())
    daily = result['daily_replay_evidence']
    for row in daily:
        cash = float(row['cash'])
        assert cash >= -1e-6
        assert all(shares >= 0 for shares in row['position_shares'].values())
        marked = cash + sum(shares * row['close_marks'][s] for s, shares in row['position_shares'].items())
        assert math.isclose(marked, equity[row['date']], rel_tol=1e-10, abs_tol=1e-6)
    assert math.isclose(account.cash, daily[-1]['cash'], abs_tol=1e-6)
    assert {s: p.shares for s, p in account.positions.items() if p.shares > 0} == {s: n for s, n in daily[-1]['position_shares'].items() if n > 0}
    assert all(fill['fill_date'] > fill['signal_date'] for fill in result['final_account']['fills'])
    if 'prefix' in data:
        prefix = data['prefix']
        assert data['prefix_economic_sha256'] == data['migrated_economic_sha256']
        assert curve[:len(prefix['equity_curve'])] == prefix['equity_curve']
        assert [row['date'] for row in daily] == [day for day in dates if day > prefix['checkpoint']]
    else:
        assert [row['date'] for row in daily] == dates
    assert math.isclose(account.capital_peak, max(account.initial_cash, *equity.values()), rel_tol=1e-10)
    full_dd = drawdown(equity.values(), account.initial_cash)
    assert math.isclose(full_dd, result['max_drawdown'], abs_tol=1e-10)
    window = [row for row in daily if common < row['date'] <= end]
    assert len(window) == sum(common < day <= end for day in dates)
    fills = [fill for fill in result['final_account']['fills'] if common < fill['fill_date'] <= end]
    orders = [order for order in result['final_account']['order_ledger'] if common <= order['signal_date'] <= end]
    buys = [fill for fill in fills if fill['side'] == 'BUY']
    fee_keys = ('commission', 'stamp_duty', 'transfer_fee')
    return {
        'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'source_head': data['source_head'], 'source_tree': data['source_tree'],
        'W': equity[end] / account.initial_cash, 'full_DD': full_dd,
        'G': equity[end] / equity[common], 'common_equity': equity[common],
        'suffix_DD': drawdown(v for day, v in equity.items() if day >= common),
        'suffix_flat_fraction': sum(not any(row['position_shares'].values()) for row in window) / len(window),
        'suffix_mean_gross': sum(1-row['cash']/equity[row['date']] for row in window)/len(window),
        'end_cash': account.cash, 'capital_peak': account.capital_peak,
        'suffix_fees': sum(fill.get(key, 0) for fill in fills for key in fee_keys),
        'suffix_slippage': sum(fill.get('slippage_cost', 0) for fill in fills),
        'suffix_gross_traded': sum(fill['gross_value'] for fill in fills),
        'suffix_turnover_on_daily_equity': sum(fill['gross_value']/equity[fill['fill_date']] for fill in fills),
        'suffix_instructions': len({order['order_id'] for order in orders}),
        'suffix_fill_records': len(fills), 'suffix_operation_days': len({order['signal_date'] for order in orders}),
        'first_suffix_buy': ({key: buys[0][key] for key in ('signal_date','fill_date','symbol','shares','mechanism')} if buys else None),
        'suffix_unfilled_orders': [{key: order.get(key) for key in ('order_id','signal_date','symbol','status','cancel_reason')}
                                  for order in orders if order['filled_shares'] == 0],
    }
