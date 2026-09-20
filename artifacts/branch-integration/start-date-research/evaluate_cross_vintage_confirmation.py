"""Evaluate registered confirmation economics separately from mechanism coverage."""
import argparse
import json
import sys
from pathlib import Path

from cross_vintage_metrics import read_metrics


def evaluate(root, label):
    contract = json.loads((root / 'artifacts/branch-integration/CROSS_VINTAGE_CONTRACT.json').read_text())
    native = root.parent / 'cross-vintage/confirmation-native' / label
    resumed = root.parent / 'cross-vintage/confirmation' / label
    groups, missing, failures = {}, [], []
    for group in contract['confirmation']:
        name = group['id']
        paths = {'C_old_native': native / (name + '-old.json.gz'),
                 'C_new_native': native / (name + '-new.json.gz'),
                 'C_old_legacy': resumed / (name + '-legacy-C.json.gz'),
                 'B_old': resumed / (name + '-old-B.json.gz'),
                 'B_new': resumed / (name + '-new-B.json.gz')}
        cells = {}
        for role, path in paths.items():
            if not path.exists():
                missing.append(name + '/' + role)
                continue
            cells[role] = read_metrics(path, group['common_close'], contract['end'])
            if role.startswith('C_') and cells[role]['full_DD'] > contract['gates']['full_drawdown']:
                failures.append(name + '/' + role + ': full drawdown')
        row = {'registration': group, 'cells': cells}
        if len(cells) == len(paths):
            reference = max(cells['B_new']['G'], cells['C_new_native']['G'])
            row['G_ref'] = reference
            row['native_old_ratio'] = cells['C_old_native']['G'] / reference
            row['legacy_old_ratio'] = cells['C_old_legacy']['G'] / reference
            row['new_retention'] = cells['C_new_native']['G'] / cells['B_new']['G']
            for key, floor in [('native_old_ratio', .7), ('legacy_old_ratio', .7), ('new_retention', .9)]:
                if row[key] < floor:
                    failures.append(name + ': ' + key)
            assert len({(cells[key]['source_head'], cells[key]['source_tree']) for key in cells if key.startswith('C_')}) == 1
        groups[name] = row
    return {'status': 'NOT_MET' if failures else 'INCOMPLETE' if missing else 'CONFIRMATION_ECONOMICS_MET',
            'groups': groups, 'failures': failures, 'missing': missing,
            'coverage': 'Separate actual affected-path evidence required. Untriggered pairs are not generalization passes; a valid failure cannot be replaced by the reserve.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    report = evaluate(Path.cwd(), args.label)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'failures', 'missing')}))
    raise SystemExit(report['status'] != 'CONFIRMATION_ECONOMICS_MET')
