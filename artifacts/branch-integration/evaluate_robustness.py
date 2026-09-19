"""Evaluate the authorized startup-date contract; missing cells fail closed."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('paired_compare', HERE.parent/'alpha-recovery/local-enhancement/compare.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def case_name(year, offset):
    return ('full' if offset == '0' else f'full-offset{offset}') if year == '2023' else f'{year}-offset{offset}'


def metrics(data):
    result = data['result']
    # The durable account ledger includes submitted-but-unfilled instructions.
    # The compact replay ledger contains executed orders only.
    orders = result['final_account']['order_ledger']
    by_day = Counter(order['signal_date'] for order in orders)
    days = result['daily_replay_evidence']
    return {'W': result['final_wealth'], 'DD': result['max_drawdown'],
            'account_instructions': len({order['order_id'] for order in orders}),
            'fill_records': len(result['final_account']['fills']),
            'pending_instructions': len(result['final_account'].get('pending_orders', [])),
            'operation_days': len(by_day), 'peak_daily_instructions': max(by_day.values(), default=0),
            'gross_turnover': result['gross_turnover'], 'fees': result['fees'],
            'slippage_cost': result['slippage_cost'], 'cash': result['final_account']['cash'],
            'held_session_fraction': sum(any(v > 0 for v in row['position_shares'].values()) for row in days)/len(days),
            'peak_to_recovery_days': result['peak_to_recovery_days']}


def evaluate(runs, baseline, repo):
    contract = json.loads((HERE/'START_DATE_CONTRACT.json').read_text())
    gate = contract['gates']
    required = {case_name(year, offset) for year, cluster in contract['clusters'].items() for offset in cluster}
    required.update(contract['strong_cases'] + contract['weak_cases'] + ['full-offset5-cost2'])
    scenario = json.loads((HERE.parent/'alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())
    failures, missing, cells, raw = [], [], {}, {}
    for name in sorted(required):
        path = runs/(name+'.json.gz')
        if not path.exists():
            missing.append(name)
            continue
        data = compare.read(path, repo)
        if data['case'] != name or data['end'] != contract['end']:
            raise ValueError(f'Wrong case/window: {name}')
        if name.startswith('loo-'):
            symbols = set(scenario['universe']) - {name.removeprefix('loo-')}
        elif name == 'remove_all_three':
            symbols = set(scenario['universe']) - {'sz300308', 'sz300502', 'sz300394'}
        elif name == 'd-continuous_ai_era':
            symbols = set(scenario['pools']['d'])
        else:
            symbols = set(scenario['universe'])
        if set(data['symbols']) != symbols or data['cost_multiplier'] != (2 if name.endswith('cost2') else 1):
            raise ValueError(f'Wrong universe/cost: {name}')
        raw[name] = data
        cells[name] = metrics(data)
        if cells[name]['DD'] > gate['maximum_drawdown']:
            failures.append(f'{name}: absolute drawdown')
    identities = {(d['source_head'], d['source_tree']) for d in raw.values()}
    if len(identities) > 1:
        raise ValueError('Mixed production candidates')
    if raw:
        first = next(iter(raw.values()))
        normal_config = next((d['config'] for d in raw.values() if d['cost_multiplier'] == 1), None)
        for data in raw.values():
            expected_config = dict(normal_config or data['config'])
            if normal_config and data['cost_multiplier'] == 2:
                for field in ('commission_rate', 'min_commission', 'stamp_duty', 'transfer_fee', 'slippage'):
                    expected_config[field] *= 2
            if data['config'] != expected_config:
                raise ValueError('Mixed configuration beyond declared cost stress')
            for key in ('inputs', 'runtime', 'runner_sha256', 'contract_sha256'):
                if data[key] != first[key]:
                    raise ValueError(f'Mixed candidate inputs: {key}')
    clusters = {}
    for year, offsets in contract['clusters'].items():
        common = max(offsets.values())
        growth = {}
        for offset, start in offsets.items():
            name = case_name(year, offset)
            if name not in raw:
                continue
            data = raw[name]
            if data['start'] != start:
                raise ValueError(f'Wrong start: {name}')
            equity = {row['date']: row['equity'] for row in data['result']['equity_curve']}
            growth[offset] = data['result']['final_equity']/equity[common]
        ratio = min(growth.values())/max(growth.values()) if len(growth) == len(offsets) else None
        clusters[year] = {'common_date': common, 'G': growth, 'R_common': ratio}
        if ratio is not None and ratio < gate['common_growth_retention']:
            failures.append(f'{year}: common growth retention')
    if ('full' in raw and 'full-offset5' in raw
            and cells['full-offset5']['W']/cells['full']['W'] < gate['offset5_wealth_retention']):
        failures.append('offset5 raw wealth retention')
    relative = {}
    for name in contract['strong_cases'] + contract['weak_cases']:
        if name not in raw:
            continue
        left = compare.read(baseline/(name+'.json.gz'), repo)
        right = raw[name]
        # B0 mapping is explicit; old producer identities are never relabelled.
        if left['source_tree'] != '120fa8a2d2914b9185239a0918a84a8838aeed77':
            raise ValueError('Baseline is not the audited B0 production tree')
        for key in ('runtime', 'config', 'symbols', 'start', 'end', 'cost_multiplier', 'sessions'):
            if left[key] != right[key]:
                raise ValueError(f'Non-comparable baseline: {name}/{key}')
        for key, value in left['inputs'].items():
            if not key.startswith('uquant/') and right['inputs'].get(key) != value:
                raise ValueError(f'Changed non-production input: {key}')
        ratio = cells[name]['W']/left['result']['final_wealth']
        relative[name] = {'baseline_source': left['source_head'], 'B0': metrics(left), 'W_ratio': ratio}
        floor = gate['strong_wealth_retention'] if name in contract['strong_cases'] else gate['weak_minimum_retention']
        if ratio < floor:
            failures.append(f'{name}: baseline wealth retention')
    weak = [relative[name]['W_ratio'] for name in contract['weak_cases'] if name in relative]
    geometric = math.exp(sum(map(math.log, weak))/len(weak)) if len(weak) == len(contract['weak_cases']) else None
    if geometric is not None and geometric < gate['weak_geometric_retention']:
        failures.append('weak pool geometric retention')
    costs = {}
    for name in contract['cost_pairs']:
        stress = name+'-cost2'
        if name in cells and stress in cells:
            costs[name] = cells[stress]['W']/cells[name]['W']
            if costs[name] < gate['cost2_retention']:
                failures.append(f'{name}: doubled cost retention')
    # Economic matrix alone cannot certify safety, mechanism disposition or publication.
    return {'status': 'NOT_MET' if failures else 'INCOMPLETE' if missing else 'ECONOMIC_MATRIX_MET',
            'contract': contract, 'candidate_identity': sorted(identities), 'cells': cells,
            'relative': relative, 'clusters': clusters, 'cost_retention': costs,
            'weak_geometric_retention': geometric, 'failures': failures, 'NOT_RUN': missing,
            'completion': 'Separate mechanism, invariant, evidence preservation and protected merge evidence required.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.runs, args.baseline, args.repo)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ('status', 'failures', 'NOT_RUN')}))
    raise SystemExit(report['status'] != 'ECONOMIC_MATRIX_MET')
