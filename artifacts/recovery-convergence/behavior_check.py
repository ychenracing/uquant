"""Task-local exact allocator replay on baseline native account paths."""
import argparse
import copy
import gzip
import hashlib
import json
import pickle
import subprocess
from dataclasses import asdict
from pathlib import Path

from uquant.engine import ProductionEngine
from uquant.portfolio import PortfolioAllocator


def encoded(targets, kwargs):
    # Full account and risk evidence, including identity; no tolerance or field removal.
    return json.dumps({'targets': [asdict(t) for t in targets],
                       'account': kwargs['account'].to_dict(),
                       'risk': asdict(kwargs['risk'])}, sort_keys=True, default=str).encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['capture', 'compare'])
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    path = args.folder / 'allocator-inputs.pkl.gz'
    original = PortfolioAllocator.allocate
    count = 0
    if args.mode == 'capture':
        cases = [('early_ai_entry', '2023-01-03', '2023-01-20', ['sz300308','sz300502','sz300394']),
                 ('late_2024_rotation', '2024-08-01', '2024-09-02', ['sz300308','sz300502','sz300394','sh688008','sh603986']),
                 ('recent_shock', '2026-06-30', '2026-07-30', ['sz300308','sz300502','sz300394','sh688008','sh603986']),
                 ('recovery_2024', '2024-01-02', '2024-06-03',
                  json.loads(Path('artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())['universe'])]
        with gzip.open(path, 'xb') as stream:
            def capture(self, **kwargs):
                nonlocal count
                before = copy.deepcopy(kwargs)
                result = original(self, **kwargs)
                pickle.dump((self.cfg, before, encoded(result, kwargs)), stream, protocol=5)
                count += 1
                return result
            PortfolioAllocator.allocate = capture
            try:
                for name, start, end, symbols in cases:
                    result = ProductionEngine(Path('data/frozen')).backtest(symbols=symbols, start=start, end=end)
                    with gzip.open(args.folder / (name + '.json.gz'), 'xt') as output:
                        json.dump(result, output, sort_keys=True, default=str)
                    print(name, result['final_wealth'], result['max_drawdown'], flush=True)
            finally:
                PortfolioAllocator.allocate = original
        summary = {'source': source, 'calls': count, 'input_sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}
    else:
        with gzip.open(path, 'rb') as stream:
            while True:
                try:
                    cfg, kwargs, expected = pickle.load(stream)
                except EOFError:
                    break
                actual = encoded(original(PortfolioAllocator(cfg), **kwargs), kwargs)
                if actual != expected:
                    (args.folder / 'first-difference-expected.json').write_bytes(expected)
                    (args.folder / 'first-difference-actual.json').write_bytes(actual)
                    raise AssertionError(f'Allocator differs at call {count}, {kwargs["date"]}')
                count += 1
        summary = {'source': source, 'calls': count, 'exact_targets_account_risk_equal': True,
                   'input_sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest(),
                   'scope': 'candidate allocator applied to identical baseline native inputs; source identities unmodified'}
    (args.folder / (args.mode + '.json')).write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
