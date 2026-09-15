"""Paired observations of each checkout's unchanged public production backtest."""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--symbols', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--cost-multiplier', type=float, default=1.0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    data_dir, contract = args.data_dir.resolve(), args.contract.resolve()
    if output.exists():
        raise ValueError('Refusing to overwrite existing replay')
    os.chdir(root)
    sys.path.insert(0, str(root))
    import numpy as np
    import pandas as pd
    from uquant.config import DEFAULT_CONFIG
    from uquant.engine import ProductionEngine
    runtime = {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__,
               'uv': subprocess.check_output(['uv', '--version'], text=True).strip()}
    if (runtime['python'], runtime['numpy'], runtime['pandas']) != ('3.12.13', '2.5.1', '3.0.5') or not runtime['uv'].startswith('uv 0.11.33 '):
        raise ValueError(f'Wrong runtime: {runtime}')
    def git(*argv: str) -> str:
        return subprocess.check_output(['git', '-C', str(root), *argv], text=True).strip()
    if git('diff', 'HEAD', '--', 'uquant', 'benchmarks/reference_registry.json', 'uv.lock', 'pyproject.toml'):
        raise ValueError('Production inputs differ from committed source')
    def inputs() -> dict[str, str]:
        paths = list((root / 'uquant').rglob('*.py')) + list((root / 'uquant').rglob('*.json'))
        paths += [root / 'benchmarks/reference_registry.json']
        files = {str(p.relative_to(root)): digest(p.read_bytes()) for p in paths if p.is_file()}
        files.update({'data/' + str(p.relative_to(data_dir)): digest(p.read_bytes())
                      for p in data_dir.rglob('*') if p.is_file() and '.cache' not in p.parts})
        return files
    before = inputs()
    cfg = DEFAULT_CONFIG
    if args.cost_multiplier != 1:
        if args.cost_multiplier != 2:
            raise ValueError('Only preregistered 2x cost stress is allowed')
        cfg = dataclasses.replace(cfg, **{name: getattr(cfg, name) * 2 for name in
                                         ('commission_rate', 'min_commission', 'stamp_duty', 'transfer_fee', 'slippage')})
    evidence = {'schema_version': 1, 'method': 'native_public_backtest', 'case': args.case,
                'source_head': git('rev-parse', 'HEAD'), 'source_tree': git('rev-parse', 'HEAD:uquant'),
                'inputs': before, 'runtime': runtime, 'config': dataclasses.asdict(cfg),
                'contract_sha256': digest(contract.read_bytes()), 'runner_sha256': digest(Path(__file__).read_bytes()),
                'symbols': sorted(args.symbols.split(',')), 'start': args.start, 'end': args.end,
                'cost_multiplier': args.cost_multiplier, 'completed': False}
    started = time.monotonic()
    try:
        result = ProductionEngine(data_dir, cfg).backtest(symbols=evidence['symbols'], start=args.start, end=args.end)
        curve = result['equity_curve']
        equities = [float(row['equity']) for row in curve]
        if len(equities) < 2 or any(not math.isfinite(x) or x <= 0 for x in equities):
            raise ValueError('Invalid equity curve')
        peak, drawdown = equities[0], 0.0
        for value in equities:
            peak = max(peak, value)
            drawdown = max(drawdown, 1 - value / peak)
        cash = float(cfg.initial_cash)
        if not math.isclose(equities[-1] / cash, float(result['final_wealth']), rel_tol=1e-10):
            raise ValueError('Wealth/curve reconciliation failed')
        if not math.isclose(drawdown, float(result['max_drawdown']), abs_tol=1e-10):
            raise ValueError('Drawdown/curve reconciliation failed')
        if before != inputs():
            raise ValueError('Inputs changed during replay')
        evidence.update(completed=True, result=result, sessions=len(equities))
    except Exception as exc:
        evidence['error'] = repr(exc)
        raise
    finally:
        evidence['elapsed_seconds'] = time.monotonic() - started
        encoded = gzip.compress(json.dumps(evidence, sort_keys=True, allow_nan=False, default=str).encode(), mtime=0)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream:
            stream.write(encoded)
        if digest(output.read_bytes()) != digest(encoded):
            raise RuntimeError('Replay save/readback failed')
        metrics = evidence.get('result', {})
        print(json.dumps({'case':args.case, 'completed': evidence['completed'], 'error':evidence.get('error'),
                          'wealth':metrics.get('final_wealth'), 'drawdown':metrics.get('max_drawdown'),
                          'orders':metrics.get('account_orders'), 'seconds':evidence['elapsed_seconds'],
                          'sha256':digest(encoded)}), flush=True)


if __name__ == '__main__':
    main()
