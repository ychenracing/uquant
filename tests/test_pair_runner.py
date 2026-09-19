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
