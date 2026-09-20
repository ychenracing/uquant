"""Replay the real pre-upgrade account through the public daily production path."""
import argparse
import dataclasses
import gzip
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
import numpy
import pandas

from uquant.account import load_account, migrate_code_identity
from uquant.account.economic_identity import economic_state_sha256
from uquant.attribution import build_daily_replay_evidence_row
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import ProductionEngine, code_fingerprint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--end', default='2026-08-05')
    parser.add_argument('--cost2', action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite original evidence')
    if subprocess.check_output(['git', 'diff', 'HEAD', '--', 'uquant', 'uv.lock', 'pyproject.toml']):
        raise ValueError('Uncommitted production inputs')
    runtime = {'python': platform.python_version(), 'numpy': numpy.__version__, 'pandas': pandas.__version__}
    assert tuple(runtime.values()) == ('3.12.13', '2.5.1', '3.0.5'), runtime
    source_hash = code_fingerprint()
    meta = json.loads(args.prefix.read_text())
    source = args.prefix.with_name(args.prefix.name.replace('-prefix.json', '-account.json'))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == meta['checkpoint_sha256']
    account = load_account(source)
    before = economic_state_sha256(account)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if account.code_hash != source_hash:
        account = migrate_code_identity(source, args.output.with_suffix('.start-account.json'),
            new_code_hash=source_hash, acknowledge_code_change=True)
    assert economic_state_sha256(account) == before
    cfg = DEFAULT_CONFIG
    if args.cost2:
        cfg = dataclasses.replace(cfg, **{key: getattr(cfg, key) * 2 for key in
            ('commission_rate', 'min_commission', 'stamp_duty', 'transfer_fee', 'slippage')})
    engine = ProductionEngine('data/frozen', cfg)
    symbols = meta['symbols']
    engine.workspace.prepare(engine.workspace.bind_tradable(symbols))
    panel = {s: engine._raw[s] for s in symbols}
    calendar = engine.workspace.common_sessions(*engine.workspace.universe.index_symbols)
    curve = list(meta['equity_curve'])
    daily, decisions, diagnostics = [], [], []
    evidence = {'completed': False, 'method': 'legacy_resume_public_daily_path', 'prefix': meta,
        'prefix_economic_sha256': before, 'migrated_economic_sha256': economic_state_sha256(account),
        'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'source_tree': subprocess.check_output(['git', 'rev-parse', 'HEAD:uquant'], text=True).strip(),
        'source_hash': source_hash, 'runtime': runtime, 'config': dataclasses.asdict(cfg),
        'cost_scope': 'suffix_only_2x' if args.cost2 else 'normal',
        'data_manifest_sha256': hashlib.sha256(Path('data/frozen/DATA_MANIFEST.json').read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        for date in calendar[(calendar > meta['checkpoint']) & (calendar <= args.end)]:
            engine.execution.execute_open(date=date, account=account, panel=panel)
            day = str(date.date())
            curve.append({'date': day, 'equity': engine.equity(account, date)})
            decision = engine.decide(symbols=symbols, as_of=day, account=account)
            prices = {s: engine._price(s, date) for s, p in account.positions.items() if p.shares > 0}
            daily.append(build_daily_replay_evidence_row(date=day, account=account, close_prices=prices))
            decisions.append(decision.canonical_payload(effective_config_sha256=config_fingerprint(cfg)))
            diagnostics.append({'date': day, 'risk': decision.risk_summary,
                                'capital_budget_level': account.capital_budget_level})
            account.pending_orders = list(decision.pending_orders)
        assert code_fingerprint() == source_hash
        assert [r['date'] for r in daily] == [str(d.date()) for d in calendar
                                            if meta['checkpoint'] < str(d.date()) <= args.end]
        peak = account.initial_cash
        dd = 0.0
        for row in curve:
            peak = max(peak, row['equity'])
            dd = max(dd, 1 - row['equity'] / peak)
        evidence.update(completed=True, result={'equity_curve': curve, 'daily_replay_evidence': daily,
            'decision_trace': decisions, 'diagnostics': diagnostics, 'final_account': account.to_dict(),
            'final_wealth': curve[-1]['equity'] / account.initial_cash, 'max_drawdown': dd,
            'G': curve[-1]['equity'] / meta['equity_curve'][-1]['equity']})
    except Exception as exc:
        evidence['error'] = repr(exc)
        raise
    finally:
        encoded = gzip.compress(json.dumps(evidence, default=str, allow_nan=False).encode(), mtime=0)
        with args.output.open('xb') as stream:
            stream.write(encoded)
        assert args.output.read_bytes() == encoded
        print(json.dumps({k: evidence.get('result', {}).get(k) for k in ('G', 'final_wealth', 'max_drawdown')}
                         | {'completed': evidence['completed'], 'error': evidence.get('error')}), flush=True)


if __name__ == '__main__':
    main()
