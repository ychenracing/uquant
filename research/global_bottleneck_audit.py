"""Read-only attribution of already sealed research paths; no strategy simulation.

Counts are observations, not counterfactual profits. Repeated READY days are
not independent opportunities. Historical paths are not fresh holdout evidence.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any

from research.cross_ai_acceptance import read_case

SOURCES = {
    'C': 'b928db51ea6a7bbdfbe79588408717241200d44b3d98b2c39d7668b28e90259d',
    'main': '86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd',
    'rejected': '76805e2f703e98eeafbee9ee05f26bce9e9fb7c78ded52dff43b3a8d53b25d7f',
}


def audit(root: Path, name: str, source: str) -> dict[str, Any]:
    case = {'minus_sz300666': 'remove_all_three', 'no_optical_h1': 'no_optical'}.get(name, name)
    end = '2023-06-30' if name == 'no_optical_h1' else '2026-08-05'
    result = read_case(root, case=case, interval=['2023-01-03', end],
                       source=source,
                       extra_excluded_symbols=('sz300666',) if name == 'minus_sz300666' else ())
    sealed = json.loads((root / 'result.json').read_text())
    counts: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    ready_blocks: Counter[str] = Counter()
    events, freezes = [], []
    previous_targets: dict[str, float] = {}
    prior_freeze = False
    prior_blocked: set[str] = set()
    blocked_episodes = []
    flat_run = longest_flat = 0
    previous_row = None
    buys, sells = [], []
    with gzip.open(root / 'observations.jsonl.gz', 'rt') as stream:
        for line in stream:
            row = json.loads(line)
            allocation = row['observation']['risk_assessment']['evidence'].get('core_allocation', {})
            symbols = allocation.get('symbols', {})
            held = row['ledger']['position_weights']
            targets = row['ledger']['target_weights']
            flat = not any(v > 0 for v in held.values())
            frozen = allocation.get('final_freeze_new_risk', allocation.get('freeze_new_risk', False))
            counts['sessions'] += 1
            counts['flat_days'] += flat
            flat_run = flat_run + 1 if flat else 0
            longest_flat = max(longest_flat, flat_run)
            counts['frozen_days'] += frozen
            counts['flat_frozen_days'] += flat and frozen
            available_ready = []
            current_blocked = set()
            for symbol, detail in symbols.items():
                entry = detail.get('entry', {})
                block = entry.get('block', 'UNOBSERVED')
                if flat:
                    blocks[block] += 1
                if block == 'READY' and held.get(symbol, 0) <= 0:
                    available_ready.append(symbol)
                    if targets.get(symbol, 0) <= 0:
                        reason = detail.get('entry_gate', detail.get('increase_block',
                                     detail.get('allocation_reason', detail.get('final_target_reason', 'UNKNOWN'))))
                        ready_blocks[reason] += 1
                        current_blocked.add(symbol)
                        if symbol not in prior_blocked:
                            blocked_episodes.append({'date': row['date'], 'symbol': symbol,
                                                     'reason': reason, 'flat': flat,
                                                     'frozen': frozen})
            counts['flat_ready_days'] += flat and bool(available_ready)
            counts['frozen_unheld_ready_days'] += frozen and bool(available_ready)
            counts['flat_unfrozen_ready_days'] += flat and not frozen and bool(available_ready)
            if frozen and not prior_freeze:
                freezes.append({'date': row['date'], 'equity': row['equity'],
                                'flat': flat, 'ready': available_ready})
            prior_freeze = frozen
            for symbol, weight in targets.items():
                if weight > 0 and held.get(symbol, 0) <= 0 and previous_targets.get(symbol, 0) <= 0:
                    detail = symbols.get(symbol, {})
                    events.append({'date': row['date'], 'symbol': symbol, 'weight': weight,
                                   'entry': detail.get('entry', {}),
                                   'allocation_reason': detail.get('allocation_reason'),
                                   'equity': row['equity'], 'frozen': frozen})
            for fill in row['new_fills']:
                if fill['side'] == 'BUY':
                    prev: dict[str, float] = previous_row['ledger']['position_weights'] if previous_row else {}
                    if prev.get(fill['symbol'], 0) <= 0:
                        buys.append({k: fill[k] for k in ('symbol', 'signal_date', 'fill_date', 'gross_value', 'mechanism')})
                else:
                    sells.append({k: fill[k] for k in ('symbol', 'signal_date', 'fill_date', 'gross_value', 'mechanism')})
            previous_targets, previous_row, prior_blocked = targets, row, current_blocked
    counts['longest_flat_days'] = longest_flat
    return {
        'path': str(root), 'source': source, 'native_readback': True,
        'result_seal': sealed['canonical_sha256'], 'raw_sha256': sealed['raw_sha256'],
        'data_identity': sealed['identity']['data'],
        'config_sha256': sealed['identity']['config_sha256'],
        'metrics': {k: result['metrics'][k] for k in
                    ('final_wealth', 'max_drawdown', 'account_orders', 'fees', 'slippage_cost')},
        'counts': dict(counts), 'flat_symbol_day_entry_blocks': dict(blocks),
        'unheld_ready_no_target_reasons': dict(ready_blocks),
        'new_target_events': events, 'new_position_buy_fills': buys, 'sell_fills': sells,
        'freeze_transitions': freezes,
        'blocked_ready_episode_starts': blocked_episodes,
        'pnl_by_symbol': {s: v['total_pnl'] for s, v in sealed['attribution']['by_symbol'].items()},
        'pnl_by_industry': {s: v['total_pnl'] for s, v in sealed['attribution']['by_industry'].items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, required=True)
    parser.add_argument('--rejected', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = {}
    for label, directory in [('main', 'recovery-baseline-960539a'), ('C', 'retire-pullback-c')]:
        for name in ('full', 'remove_all_three', 'minus_sz300666', 'no_optical_h1'):
            key = label + '/' + name
            results[key] = audit(args.runs / directory / name, name, SOURCES[label])
            print(key, results[key]['counts'], flush=True)
    results['rejected/no_optical_h1'] = audit(args.rejected, 'no_optical_h1', SOURCES['rejected'])
    args.output.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
