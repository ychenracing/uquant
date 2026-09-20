"""Fixed cross-vintage gates; missing evidence is never a pass."""
import argparse
import json
import sys
from pathlib import Path

from cross_vintage_metrics import read_metrics


def evaluate(root, label):
    contract = json.loads((root / 'artifacts/branch-integration/CROSS_VINTAGE_CONTRACT.json').read_text())
    base = root.parent
    normal = base / 'cross-vintage/native' / label
    legacy = base / 'cross-vintage' / label
    cells, missing, failures = {}, [], []

    def add(key, path, common=contract['common_close']):
        if not path.exists():
            missing.append(key)
            return
        cells[key] = read_metrics(path, common, contract['end'])
        if cells[key]['full_DD'] > contract['gates']['full_drawdown']:
            failures.append(key + ': full drawdown')

    for year in ('2024', '2025'):
        for offset in (0, 5, 10):
            name = f'{year}-offset{offset}'
            add('C-native-' + name, normal / (name + '.json.gz'))
            source = base / 'evidence/checkpoint5/robustness-runs/c9' / (name + '.json.gz')
            add('B-' + name, source)
            if year == '2024':
                add('C-legacy-' + name, legacy / ('legacy-' + name + '.json.gz'))
    add('C-legacy-cost2', legacy / 'legacy-2024-offset0-cost2.json.gz')
    add('C-new-cost2', legacy / 'native-2025-offset5-suffix-cost2.json.gz')
    new_keys = [f'{source}-2025-offset{offset}' for source in ('B', 'C-native') for offset in (0, 5, 10)]
    reference = max(cells[key]['G'] for key in new_keys) if all(key in cells for key in new_keys) else None
    ratios, retention, costs = {}, {}, {}
    if reference is not None:
        for method in ('native', 'legacy'):
            keys = [f'C-{method}-2024-offset{offset}' for offset in (0, 5, 10)]
            if all(key in cells for key in keys):
                ratios[method] = min(cells[key]['G'] / reference for key in keys)
                if ratios[method] < contract['gates'][method + '_old_ratio']:
                    failures.append(method + ': old growth ratio')
        for offset in (0, 5, 10):
            retention[str(offset)] = cells[f'C-native-2025-offset{offset}']['G'] / cells[f'B-2025-offset{offset}']['G']
            if retention[str(offset)] < contract['gates']['each_new_retention']:
                failures.append(f'2025-offset{offset}: new retention')
    for stress, regular in [('C-legacy-cost2', 'C-legacy-2024-offset0'), ('C-new-cost2', 'C-native-2025-offset5')]:
        if stress in cells and regular in cells:
            costs[stress] = cells[stress]['G'] / cells[regular]['G']
            if costs[stress] < contract['gates']['suffix_cost2_retention']:
                failures.append(stress + ': cost retention')
    candidate = {(row['source_head'], row['source_tree']) for key, row in cells.items() if key.startswith('C-')}
    assert len(candidate) <= 1, 'Mixed candidate producers'
    return {'status': 'NOT_MET' if failures else 'INCOMPLETE' if missing else 'CORE_CROSS_VINTAGE_MET',
            'contract': contract, 'G_ref': reference, 'old_ratios': ratios, 'new_retention': retention,
            'suffix_cost_retention': costs, 'cells': cells, 'failures': failures, 'missing': missing,
            'scope': 'Core cross-vintage only; original 23 cases, confirmations, mechanism and delivery required separately.',
            'exposure_convention': 'Close observations strictly after common close through end; common-close equity is the growth denominator. Fees and fills use the same suffix; common-close orders are included because they can execute in the suffix.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    report = evaluate(Path.cwd(), args.label)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status','G_ref','old_ratios','new_retention','suffix_cost_retention','failures','missing')}))
    raise SystemExit(report['status'] != 'CORE_CROSS_VINTAGE_MET')
