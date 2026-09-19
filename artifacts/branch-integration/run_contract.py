"""Schedule the revised contract through the existing validated pair runner."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path

from run_pairs import CONTRACT, ROOT, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-root', required=True, type=Path)
    parser.add_argument('--label', required=True)
    parser.add_argument('--stage', required=True, choices=('development', 'pools', 'confirmation'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--runs', type=Path, default=ROOT.parent / 'robustness-runs')
    args = parser.parse_args()
    if Path(args.label).name != args.label or args.label in {'.', '..'}:
        parser.error('Label must be one path component')
    plan = json.loads((Path(__file__).parent / 'START_DATE_CONTRACT.json').read_text())
    frozen = json.loads(CONTRACT.read_text())
    universe = frozen['universe']
    cases = []
    if args.stage != 'pools':
        for year, offsets in plan['clusters'].items():
            for offset, start in offsets.items():
                development = year == '2023' and offset in {'0', '1', '5', '10'}
                if development != (args.stage == 'development'):
                    continue
                name = ('full' if offset == '0' else f'full-offset{offset}') if year == '2023' else f'{year}-offset{offset}'
                cases.append((name, universe, start, 1))
    else:
        start = plan['clusters']['2023']['0']
        cases.extend(('loo-' + symbol, [s for s in universe if s != symbol], start, 1)
                     for symbol in ('sz300308', 'sz300502'))
        cases.extend([
            ('remove_all_three', [s for s in universe if s not in {'sz300308', 'sz300502', 'sz300394'}], start, 1),
            ('d-continuous_ai_era', frozen['pools']['d'], start, 1),
            ('full-cost2', universe, start, 2),
            ('full-offset5-cost2', universe, plan['clusters']['2023']['5'], 2),
        ])
    print(json.dumps({'stage': args.stage, 'cases': [{'case': c[0], 'start': c[2], 'cost': c[3]} for c in cases]}), flush=True)

    def execute(case):
        return run((args.label, args.candidate_root.resolve(), case), runs=args.runs.resolve(),
                   data_dir=ROOT / 'data/frozen', contract=CONTRACT, end=plan['end'])

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(execute, cases))
    for result in results:
        print(json.dumps(result), flush=True)
    return int(any(result['exit_code'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
