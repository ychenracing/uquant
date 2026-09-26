"""Verify real snapshot distributions against independent cash/share arithmetic."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

from uquant.account import load_account, save_account
from uquant.account.corporate_actions import apply_corporate_actions, receivable_total
from uquant.broker import sync_broker_snapshot
from uquant.data import DataStore
from uquant.data_check import check_snapshot
from uquant.types import AccountState


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--snapshot-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'python': sys.version, 'scope': 'real distributions; seeded research inventory, not a real account',
              'passed': False, 'cases': []}
    try:
        store = DataStore(args.snapshot_root)
        report['snapshot'] = store.snapshot_manifest
        report['data_check'] = check_snapshot(store.root)
        assert report['data_check']['ok'], 'snapshot content validation failed'
        events = json.loads((store.root / 'CORPORATE_ACTIONS.json').read_text())
        assert any(e['cash_per_share'] > 0 for e in events), 'no actual cash dividend'
        assert any(e['share_ratio'] > 0 for e in events), 'no actual bonus/transfer distribution'
        for event in events:
            symbol = event['symbol']
            frame = store.load(symbol)
            before = frame.loc[frame.index < event['ex_date']]
            assert not before.empty, 'missing pre-event close'
            previous = float(before['close'].iloc[-1])
            entry_date = str(before.index[-1].date())
            shares, cash = 10000, 1000000.0
            state = AccountState.empty(cash + shares * previous)
            state.data_hash = hashlib.sha256((store.root / 'DATA_MANIFEST.json').read_bytes()).hexdigest()
            state.code_hash = report['source_sha']
            sync_broker_snapshot(state, {
                'as_of': entry_date, 'cash': cash,
                'positions': [{'symbol': symbol, 'shares': shares, 'sellable_shares': 0, 'avg_cost': previous}],
                'fills': [], 'external_trades': [{'trade_id': event['event_id'], 'source': 'MANUAL',
                    'side': 'BUY', 'symbol': symbol, 'shares': shares, 'price': previous,
                    'trade_date': entry_date, 'commission': 0.0}],
            })
            expected_shares = math.floor(shares * (1 + event['share_ratio']) + 1e-9)
            expected_dividend = shares * event['cash_per_share']
            reference = (previous - event['cash_per_share']) / (1 + event['share_ratio'])
            assert apply_corporate_actions(state, [event], through=event['ex_date'], frames={symbol: frame}) == 1
            position = state.positions[symbol]
            assert position.shares == expected_shares
            assert math.isclose(state.cash + receivable_total(state), cash + expected_dividend, abs_tol=1e-6)
            assert math.isclose(position.shares * position.avg_cost,
                                shares * previous - expected_dividend, abs_tol=1e-6)
            equity = state.cash + receivable_total(state) + position.shares * reference
            assert math.isclose(equity, cash + shares * previous, abs_tol=reference + 1e-6)
            account_file = output.parent / 'accounts' / f"{symbol}-{event['ex_date']}.json"
            account_file.parent.mkdir(exist_ok=True)
            save_account(state, account_file)
            state = load_account(account_file)
            assert apply_corporate_actions(state, [event], through=event['ex_date'], frames={symbol: frame}) == 0
            apply_corporate_actions(state, [event], through=max(event['pay_date'], event['ex_date']), frames={symbol: frame})
            assert not state.receivables
            assert math.isclose(state.cash, cash + expected_dividend, abs_tol=1e-6)
            save_account(state, account_file)
            assert load_account(account_file).cash == state.cash
            report['cases'].append({'event': event, 'previous_close': previous,
                                    'expected_shares': expected_shares, 'actual_shares': position.shares,
                                    'expected_dividend': expected_dividend, 'cash_after_payment': state.cash,
                                    'theoretical_ex_equity': equity, 'idempotence_and_readback': True})
        report['passed'] = True
    except Exception as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
