"""Capture preregistered real baseline prefixes, without evaluating candidates."""
import concurrent.futures
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


def capture(case, *, expected_source="3af6aba18f133f4e22612769c703dac080d940d9"):
    name, start, cut, symbols = case
    root = Path('../cross-vintage/confirmation-prefixes')
    root.mkdir(parents=True, exist_ok=True)
    source = root / (name + '-account.json')
    meta = root / (name + '-prefix.json')
    if source.exists() or meta.exists():
        raise ValueError('Existing prefix must be verified before reuse')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert head == expected_source
    engine = ProductionEngine('data/frozen')
    engine.workspace.prepare(engine.workspace.bind_tradable(symbols))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {s: engine._raw[s] for s in symbols}
    calendar = engine.workspace.common_sessions(*engine.workspace.universe.index_symbols)
    curve = []
    for date in calendar[(calendar >= start) & (calendar <= cut)]:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        day = str(date.date())
        curve.append({'date': day, 'equity': engine.equity(account, date)})
        decision = engine.decide(symbols=symbols, as_of=day, account=account)
        account.pending_orders = list(decision.pending_orders)
    assert curve[-1]['date'] == cut
    save_account(account, source)
    meta.write_text(json.dumps({'start': start, 'checkpoint': cut, 'source_head': head,
        'symbols': symbols, 'equity_curve': curve,
        'checkpoint_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}, indent=2))
    print(name, 'prefix saved', len(curve), flush=True)


if __name__ == '__main__':
    contract = json.loads(Path(sys.argv[1]).read_text())
    universe = json.loads(Path('artifacts/alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())['universe']
    cases = [(group['id'] + '-' + role, group[role + '_start'], group['common_close'],
              [s for s in universe if group['pool'] == 'full' or s != 'sz300308'])
             for group in contract['confirmation'] for role in ('old', 'new')]
    with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
        list(pool.map(capture, cases))
