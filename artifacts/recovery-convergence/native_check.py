"""Confirm source-bound native economics for the affected recovery path."""
import gzip
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import numpy
import pandas

from uquant.engine import ProductionEngine

folder = Path('/workspace/scratch/e1556fe9bbd2/convergence-evidence')
with gzip.open(folder / 'recovery_2024.json.gz', 'rt') as stream:
    baseline = json.load(stream)
symbols = json.loads(Path('artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())['universe']
result = ProductionEngine(Path('data/frozen')).backtest(symbols=symbols, start='2024-01-02', end='2024-06-03')
path = folder / 'candidate-recovery-2024.json.gz'
with gzip.open(path, 'xt') as stream:
    json.dump(result, stream, sort_keys=True, default=str)
keys = ['equity_curve', 'final_wealth', 'max_drawdown', 'fees', 'slippage_cost',
        'gross_turnover', 'annual_turnover', 'account_orders', 'submitted_account_orders',
        'unfilled_account_submissions', 'round_trips', 'median_holding_days']
for key in keys:
    assert baseline[key] == result[key], key
assert [len(baseline[k]) for k in ('order_ledger', 'decision_trace')] == [len(result[k]) for k in ('order_ledger', 'decision_trace')]
assert {f['fill_date'] for f in baseline['final_account']['fills']} == {f['fill_date'] for f in result['final_account']['fills']}
receipt = {'source_head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           'production_tree': subprocess.check_output(['git','rev-parse','HEAD:uquant'],text=True).strip(),
           'runtime': {'python':platform.python_version(),'numpy':numpy.__version__,'pandas':pandas.__version__},
           'exact_equal_fields':keys, 'sessions':len(result['equity_curve']),
           'wealth':result['final_wealth'],'max_drawdown':result['max_drawdown'],
           'wealth_delta':0,'drawdown_delta':0,'turnover_delta':0,'cost_delta':0,
           'order_count_delta':0,'operation_days_delta':0,
           'raw_sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()}
Path('artifacts/recovery-convergence/NATIVE_COMPARISON.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt),flush=True)
