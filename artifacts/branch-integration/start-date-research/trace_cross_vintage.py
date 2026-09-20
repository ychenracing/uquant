"""Capture real baseline prefixes and causal state without changing decisions."""
import concurrent.futures
import dataclasses
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.account import save_account
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.types import AccountState

OUT = Path('../cross-vintage/baseline')
SYMBOLS = json.loads(Path('artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())['universe']


def run(start):
    e = ProductionEngine('data/frozen')
    e.workspace.prepare(e.workspace.bind_tradable(SYMBOLS))
    a = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {s: e._raw[s] for s in SYMBOLS}
    cal = e.workspace.common_sessions(*e.workspace.universe.index_symbols)
    curve, states = [], []
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    OUT.mkdir(parents=True, exist_ok=True)
    for date in cal[(cal >= start) & (cal <= '2025-05-08')]:
        e.execution.execute_open(date=date, account=a, panel=panel)
        day = str(date.date())
        curve.append({'date': day, 'equity': e.equity(a, date)})
        d = e.decide(symbols=SYMBOLS, as_of=day, account=a)
        a.pending_orders = list(d.pending_orders)
        states.append({'date': day, 'account': a.to_dict(), 'decision': dataclasses.asdict(d)})
        if day == '2025-01-16':
            checkpoint = OUT / (start + '-account.json')
            save_account(a, checkpoint)
            (OUT / (start + '-prefix.json')).write_text(json.dumps({
                'start': start, 'checkpoint': day, 'source_head': head, 'symbols': SYMBOLS,
                'equity_curve': curve, 'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            }, indent=2))
        if len(curve) % 100 == 0:
            print(start, day, len(curve), flush=True)
    with gzip.open(OUT / (start + '-trace.json.gz'), 'wt') as stream:
        json.dump(states, stream, default=str, allow_nan=False)
    print(start, 'done', flush=True)


if __name__ == '__main__':
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, ['2024-01-02', '2024-01-09', '2024-01-16', '2025-01-09']))
