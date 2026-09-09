"""Bounded checks of benchmark execution and fail-closed evidence."""
from __future__ import annotations

import gzip
import hashlib

import pandas as pd
import pytest

import research.cross_ai_benchmark as benchmark
from research.cross_ai_benchmark import correlation_groups, submit_targets
from uquant.account import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.engine import ProductionEngine
from uquant.types import AccountState, Target


def test_benchmark_partial_fill_survives_account_roundtrip_without_duplicate_order():
    engine = ProductionEngine('data/frozen')
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    date = pd.Timestamp('2023-01-03')
    target = Target('sz300308', 0.30, 'CORE', 0.0, 1.0, 'research monthly trend',
                    origin_subsystem='LEADER', mechanism='LEADER_SELECTION', origin_lifecycle='CORE')
    submit_targets(engine, account, date, (target,), {'sz300308': 10.0})
    assert account.pending_orders and not account.fills
    frame = pd.DataFrame({'open': [10.0] * 3, 'high': [10.2] * 3, 'low': [9.8] * 3,
                          'close': [10.0] * 3, 'volume': [10_000_000.0] * 3,
                          'amount': [100_000_000.0] * 3},
                         index=pd.to_datetime(['2023-01-03', '2023-01-04', '2023-01-05']))
    engine.execution.execute_open(date=pd.Timestamp('2023-01-04'), account=account,
                                  panel={'sz300308': frame})
    assert len(account.fills) == 1 and account.positions['sz300308'].shares > 0
    restored = account_from_dict(account.to_dict(), require_hashes=False)
    engine.execution.execute_open(date=pd.Timestamp('2023-01-05'), account=restored,
                                  panel={'sz300308': frame})
    assert len(restored.fills) == 2
    assert len(restored.order_ledger) == 1
    assert restored.fills[0].order_id == restored.fills[1].order_id
    assert restored.cash < account.cash
    assert not restored.strategic_epochs and restored.strategic_grant is None


def test_missing_correlation_is_explicit_and_nonfinite_price_fails():
    dates = pd.bdate_range('2023-01-03', periods=4)
    panel = {'sz300308': pd.DataFrame({'close': [10.0, 10.1, 10.3, 10.2]}, index=dates),
             'sz300502': pd.DataFrame({'close': [20.0, 20.2, 20.6, 20.4]}, index=dates)}
    groups, missing = correlation_groups(panel, list(panel), dates[-1], DEFAULT_CONFIG)
    assert missing == {('sz300308', 'sz300502')}
    assert groups == [{'sz300308'}, {'sz300502'}]
    panel['sz300308'].loc[dates[-1], 'close'] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        correlation_groups(panel, list(panel), dates[-1], DEFAULT_CONFIG)


@pytest.fixture(scope='module')
def excluded_replay(tmp_path_factory):
    output = tmp_path_factory.mktemp('simple-benchmark') / 'excluded'
    result = benchmark.run_benchmark_case(
        case_id='full', start='2023-01-03', end='2023-01-10', output_dir=output,
        extra_excluded_symbols=('sz300666',),
    )
    assert result['status'] == 'COMPLETE', result['error']
    return output


def test_extra_exclusion_reaches_every_daily_role_and_sealed_readback(excluded_replay):
    result = benchmark.read_benchmark_case(
        excluded_replay, case_id='full', start='2023-01-03', end='2023-01-10',
        extra_excluded_symbols=('sz300666',),
    )
    assert result['sessions'] == 6
    with gzip.open(excluded_replay / 'observations.jsonl.gz', 'rt') as stream:
        rows = [strict_json_loads(line) for line in stream]
    for row in rows:
        for role in ('tradable', 'qualification', 'risk'):
            assert 'sz300666' not in row['roles'][role]
            assert 'sz300308' in row['roles'][role]
        assert row['roles']['indexes'] == ['sh000300', 'sh000682']
    fills = [fill for row in rows for fill in row['new_fills']]
    assert fills
    assert all(fill['signal_date'] < fill['fill_date'] for fill in fills)


@pytest.mark.parametrize('exclusions', [('sh000300',), ('bad',)])
def test_invalid_exclusions_are_rejected_before_evidence_creation(tmp_path, exclusions):
    output = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='exclusions'):
        benchmark.run_benchmark_case(case_id='full', start='2023-01-03', end='2023-01-10',
                                     output_dir=output, extra_excluded_symbols=exclusions)
    assert not output.exists()


def test_explicit_producer_seal_preserves_other_identity_checks(excluded_replay, monkeypatch):
    identity_fn = benchmark.benchmark_identity
    producer = identity_fn('full', '2023-01-03', '2023-01-10',
                           extra_excluded_symbols=('sz300666',))['benchmark_sha256']

    def changed_reader(*args, **kwargs):
        identity = identity_fn(*args, **kwargs)
        identity['benchmark_sha256'] = '0' * 64
        return identity

    monkeypatch.setattr(benchmark, 'benchmark_identity', changed_reader)
    kwargs = dict(case_id='full', start='2023-01-03', end='2023-01-10',
                  extra_excluded_symbols=('sz300666',))
    with pytest.raises(ValueError, match='identity'):
        benchmark.read_benchmark_case(excluded_replay, **kwargs)
    assert benchmark.read_benchmark_case(excluded_replay, producer_sha256=producer, **kwargs)['status'] == 'COMPLETE'
    with pytest.raises(ValueError, match='identity'):
        benchmark.read_benchmark_case(excluded_replay, producer_sha256='1' * 64, **kwargs)

    def changed_config(*args, **kwargs):
        identity = changed_reader(*args, **kwargs)
        identity['config_sha256'] = '0' * 64
        return identity

    monkeypatch.setattr(benchmark, 'benchmark_identity', changed_config)
    with pytest.raises(ValueError, match='identity'):
        benchmark.read_benchmark_case(excluded_replay, producer_sha256=producer, **kwargs)


@pytest.mark.parametrize('mutation', ['metric', 'raw', 'account', 'state', 'scenario', 'sessions'])
def test_benchmark_readback_rejects_tampered_evidence(excluded_replay, tmp_path, mutation):
    import shutil

    output = tmp_path / 'tampered'
    shutil.copytree(excluded_replay, output)
    if mutation in {'raw', 'account', 'state'}:
        name = {'raw': 'observations.jsonl.gz', 'account': 'final_account.json',
                'state': 'benchmark_state.json'}[mutation]
        with (output / name).open('ab') as stream:
            stream.write(b' ')
    else:
        path = output / 'result.json'
        result = strict_json_loads(path.read_bytes())
        result.pop('canonical_sha256')
        if mutation == 'metric':
            result['metrics']['final_wealth'] += 1.0
        elif mutation == 'scenario':
            result['identity']['extra_excluded_symbols'] = []
        else:
            result['sessions'] -= 1
        result['canonical_sha256'] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
        path.write_bytes(canonical_json_bytes(result))
    with pytest.raises(ValueError):
        benchmark.read_benchmark_case(output, case_id='full', start='2023-01-03',
                                      end='2023-01-10', extra_excluded_symbols=('sz300666',))


@pytest.mark.parametrize('mutation', ['daily_pnl', 'ledger_equity', 'roles', 'chronology', 'terminal_state'])
def test_readback_rejects_resealed_semantic_mutations(excluded_replay, tmp_path, mutation):
    import shutil

    output = tmp_path / 'resealed'
    shutil.copytree(excluded_replay, output)
    raw = output / 'observations.jsonl.gz'
    with gzip.open(raw, 'rt') as stream:
        rows = [strict_json_loads(line) for line in stream]
    if mutation == 'daily_pnl':
        rows[1]['ledger']['daily_pnl'] += 100
        rows[2]['ledger']['daily_pnl'] -= 100
    elif mutation == 'ledger_equity':
        rows[1]['ledger']['equity'] += 100
    elif mutation == 'roles':
        rows[1]['roles']['tradable'].append('sz300666')
    elif mutation == 'chronology':
        row = next(row for row in rows if row['new_fills'])
        row['new_fills'][0]['signal_date'] = row['date']
    else:
        rows[-1]['state']['tampered'] = True
    with gzip.open(raw, 'wb') as stream:
        for row in rows:
            stream.write(canonical_json_bytes(row) + b'\n')
    path = output / 'result.json'
    result = strict_json_loads(path.read_bytes())
    result.pop('canonical_sha256')
    result['raw_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
    result['canonical_sha256'] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    path.write_bytes(canonical_json_bytes(result))
    with pytest.raises(ValueError):
        benchmark.read_benchmark_case(output, case_id='full', start='2023-01-03',
                                      end='2023-01-10', extra_excluded_symbols=('sz300666',))
