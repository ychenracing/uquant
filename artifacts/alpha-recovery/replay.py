"""Fixed observer for historical alpha attribution; diagnostic, never acceptance."""
import argparse
import dataclasses
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--pool', choices=['a', 'd', 'e'], required=True)
p.add_argument('--start', required=True)
p.add_argument('--end', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
root = a.root.resolve()
sys.path.insert(0, str(root))
os.chdir(root)
import numpy as np
import pandas as pd
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine

def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args])

def digest(data):
    return hashlib.sha256(data).hexdigest()

def identity():
    paths = git('ls-files', 'uquant', 'data/frozen', 'benchmarks', 'uv.lock', 'requirements.txt', 'pyproject.toml').decode().splitlines()
    return {name: digest((root / name).read_bytes()) for name in paths if (root / name).is_file()}

adapter_path = Path(__file__).resolve().with_name('trace_adapter.py')
spec = importlib.util.spec_from_file_location('alpha_fixed_observer', adapter_path)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
pool_spec = json.loads(Path(__file__).resolve().parents[2].joinpath('benchmarks/promotion_baseline.json').read_text())
symbols = pool_spec['pools'][a.pool]
before = identity()
config = DEFAULT_CONFIG.to_dict() if hasattr(DEFAULT_CONFIG, 'to_dict') else dataclasses.asdict(DEFAULT_CONFIG)
random.seed(0)
np.random.seed(0)
started = time.monotonic()
payload = {
    'status': 'DIAGNOSTIC_ONLY', 'commit': git('rev-parse', 'HEAD').decode().strip(),
    'source_files': before, 'config': config, 'symbols': symbols,
    'interval': {'start': a.start, 'end': a.end}, 'seed': 0,
    'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__,
                    'uv': subprocess.check_output(['uv', '--version'], text=True).strip()},
    'adapter_sha256': digest(adapter_path.read_bytes()),
    'runner_sha256': digest(Path(__file__).read_bytes()),
}
try:
    result, trace = adapter.trace_backtest(ProductionEngine(root / 'data/frozen', DEFAULT_CONFIG), symbols=symbols, start=a.start, end=a.end)
    payload['metrics'] = {k: result[k] for k in ['final_wealth', 'max_drawdown', 'account_orders', 'annual_turnover', 'gross_turnover']}
    payload['trace'] = trace
    if identity() != before:
        raise RuntimeError('Executable inputs changed during replay')
    payload['completed'] = True
except Exception as exc:
    payload['completed'] = False
    payload['error'] = repr(exc)
    raise
finally:
    payload['elapsed_seconds'] = time.monotonic() - started
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_bytes(gzip.compress(json.dumps(payload, sort_keys=True, default=str).encode(), mtime=0))
    print(json.dumps({k: v for k, v in payload.items() if k in ['commit', 'metrics', 'error', 'elapsed_seconds', 'completed']}), flush=True)
