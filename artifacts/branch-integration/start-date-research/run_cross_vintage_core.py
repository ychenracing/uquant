"""Run the already registered core matrix through the existing strict runner."""
import argparse
import concurrent.futures
import json
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'artifacts/branch-integration'))
from run_pairs import CONTRACT, run  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--only')
    args = parser.parse_args()
    scenario = json.loads(CONTRACT.read_text())
    contract = json.loads((ROOT / 'artifacts/branch-integration/START_DATE_CONTRACT.json').read_text())
    universe = scenario['universe']
    cases = []
    for year, cluster in contract['clusters'].items():
        for offset, start in cluster.items():
            name = ('full' if offset == '0' else 'full-offset' + offset) if year == '2023' else year + '-offset' + offset
            cases.append((name, universe, start, 1))
    cases.extend([
        ('full-cost2', universe, '2023-01-03', 2),
        ('full-offset5-cost2', universe, '2023-01-10', 2),
        ('d-continuous_ai_era', scenario['pools']['d'], '2023-01-03', 1),
        ('loo-sz300308', [s for s in universe if s != 'sz300308'], '2023-01-03', 1),
        ('loo-sz300502', [s for s in universe if s != 'sz300502'], '2023-01-03', 1),
        ('remove_all_three', [s for s in universe if s not in {'sz300308', 'sz300502', 'sz300394'}], '2023-01-03', 1),
    ])
    assert len(cases) == 23 and len({case[0] for case in cases}) == 23
    if args.only:
        selected = set(args.only.split(','))
        assert selected <= {case[0] for case in cases}
        cases = [case for case in cases if case[0] in selected]

    def execute(case):
        result = run((args.label, ROOT, case), runs=ROOT.parent / 'cross-vintage/native',
                     data_dir=ROOT / 'data/frozen', contract=CONTRACT, end=contract['end'])
        print(json.dumps(result), flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(execute, cases))
    return int(any(row['exit_code'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
