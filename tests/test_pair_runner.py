"""Orchestrator failures, retained retries and identity-checked reuse."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('run_pairs', ROOT / 'artifacts/branch-integration/run_pairs.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize('existing', [None, b'', b'not gzip'])
def test_failed_child_retry_and_completed_reuse(tmp_path, monkeypatch, existing):
    output = tmp_path / 'candidate/full.json.gz'
    output.parent.mkdir()
    if existing is not None:
        output.write_bytes(existing)
    calls = []
    returns = iter((7, 0))
    def child(cmd, **kwargs):
        path = Path(cmd[cmd.index('--output') + 1])
        if '--identity-only' in cmd:
            path.write_text(json.dumps({'source_head': 'expected'}))
            return subprocess.CompletedProcess(cmd, 0)
        calls.append(cmd)
        code = next(returns)
        if not code:
            path.write_bytes(b'validated-result')
        return subprocess.CompletedProcess(cmd, code)
    def validate(path, checkout, expected):
        assert expected == {'source_head': 'expected'}
        if path.read_bytes() != b'validated-result':
            raise ValueError('invalid result')
    monkeypatch.setattr(runner.subprocess, 'run', child)
    monkeypatch.setattr(runner, 'validate', validate)
    row = ('candidate', ROOT, ('full', ['sz300308'], '2023-01-03', 1))
    kwargs = dict(runs=tmp_path, data_dir=tmp_path, contract=tmp_path/'contract', end='2023-01-04')
    failed = runner.run(row, **kwargs)
    assert failed['exit_code'] == 7 and not output.exists()
    assert runner.run(row, **kwargs)['exit_code'] == 0
    assert runner.run(row, **kwargs)['existing'] is True
    assert len(calls) == 2
    attempts = list((output.parent/'attempts/full').iterdir())
    assert len(attempts) == 3
    assert json.loads((Path(failed['attempt'])/'status.json').read_text())['exit_code'] == 7
    if existing is not None:
        assert (Path(failed['attempt'])/'rejected.json.gz').read_bytes() == existing


def test_main_propagates_failed_task(monkeypatch, tmp_path):
    monkeypatch.setattr('sys.argv', ['run_pairs', '--candidate-only', '--only', 'full', '--runs', str(tmp_path)])
    monkeypatch.setattr(runner, 'run', lambda *a, **k: {'exit_code': 7})
    assert runner.main() == 1


@pytest.mark.parametrize('frozen,settling,recovery,reason', [
    (False, True, True, 'FAILED_DEPLOYMENT_UNSETTLED'),
    (True, False, True, 'NEW_RISK_FROZEN'),
])
def test_recovery_gate_keeps_orders_blocked(monkeypatch, frozen, settling, recovery, reason):
    from dataclasses import replace

    from test_ordinary_trend_budget import _decide, _scenario

    from uquant.leader import apply_leader_tenure
    from uquant.portfolio import pipeline

    policy, account, dates, panel, base, risk = _scenario()
    for _ in range(policy.cfg.leader_tenure_days):
        leaders = apply_leader_tenure(base, account=account, cfg=policy.cfg)
    risk = replace(risk, freeze_new_risk=frozen)
    monkeypatch.setattr(pipeline, 'allocate_confirmed_recovery', lambda *a, **k: recovery)
    monkeypatch.setattr(pipeline, '_failed_deployment_awaits_settlement', lambda a: settling)
    monkeypatch.setattr(pipeline, '_core_candidates', lambda *a, **k: list(base))
    _decide(policy, account, dates[0], panel, leaders, risk)
    trace = risk.evidence['core_allocation']['symbols']
    assert all(trace[s]['entry_gate'] == reason for s in base)
    assert not account.pending_orders and not account.positions
    assert account.cash == account.initial_cash
