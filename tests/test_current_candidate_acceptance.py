"""Historical raw fixtures test current acceptance mechanics, never candidate economics."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from uquant.validation.absolute_generalization import _acceptance_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]


def _raw():
    return json.loads(gzip.decompress((ROOT / 'tests/fixtures/absolute_champion_runtime_raw.json.gz').read_bytes()))


def test_current_candidate_acceptance_is_explicit_and_derived_from_raw_account() -> None:
    derive = getattr(evidence, 'current_candidate_champion_evidence', None)
    assert callable(derive), 'current candidate needs explicit raw-based acceptance'
    raw = _raw()
    summary = derive(raw)
    assert summary['acceptance_basis']['contract_id'] == 'cross-ai-core-strategy-20260905-v1'
    assert summary['acceptance_basis']['production_source_sha256'] == raw['final_account']['code_hash']
    assert summary['metrics']['final_wealth'] == 24.509661802900865
    assert summary['violations'] == []
    assert summary['physical_fills'] > 0
    assert set(summary['sha256']) == {'targets', 'orders', 'fills', 'positions', 'equity'}


@pytest.mark.parametrize('mutation', ['duplicate_fill', 'duplicate_order', 'missing_real_fills', 'same_session_fill', 'event_alias'])
def test_current_candidate_rejects_raw_execution_corruption(mutation: str) -> None:
    derive = getattr(evidence, 'current_candidate_champion_evidence', None)
    assert callable(derive)
    raw = _raw()
    account = raw['final_account']
    if mutation == 'duplicate_fill':
        account['fills'].append(copy.deepcopy(account['fills'][0]))
    elif mutation == 'duplicate_order':
        account['order_ledger'].append(copy.deepcopy(account['order_ledger'][0]))
    elif mutation == 'missing_real_fills':
        account['fills'] = []
    elif mutation == 'same_session_fill':
        account['fills'][0]['fill_date'] = account['fills'][0]['signal_date']
    else:
        row = next(row for row in raw['decision_trace'] if row['targets'])
        alias = {**row['targets'][0], 'symbol': 'sh688008', 'weight': 0.0}
        row['targets'].append(alias)
    with pytest.raises((ValueError, RuntimeError)):
        derive(raw)


def test_raw_source_and_contract_basis_cannot_be_relabelled() -> None:
    raw = _raw()
    summary = evidence.current_candidate_champion_evidence(raw)
    # Test-only source projection uses the unchanged historical raw source.
    source = raw['final_account']['code_hash']
    evidence._validate_grant_acceptance({'baseline': summary}, raw, expected_source=source)
    with pytest.raises(ValueError, match='raw account source'):
        evidence._validate_grant_acceptance({'baseline': summary}, raw, expected_source='f' * 64)
    summary['acceptance_basis']['contract_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='current candidate evidence differs'):
        evidence._validate_grant_acceptance({'baseline': summary}, raw, expected_source=source)


def test_date_session_conflict_is_rejected() -> None:
    raw = _raw()
    raw['decision_trace'][0]['session'] = '2023-01-04'
    with pytest.raises(ValueError, match='date/session conflict'):
        evidence.current_candidate_champion_evidence(raw)


def test_current_component_allows_new_paths_and_overlap_but_preserves_numeric_limits() -> None:
    from types import SimpleNamespace

    from uquant.validation.absolute_generalization.policy import _champion_component
    summary = evidence.current_candidate_champion_evidence(_raw())
    raw = {'metrics': summary['metrics'], 'path_sha256': {key: 'f' * 64 for key in summary['sha256']},
           'report_13': summary['accounting'], 'duplicate_grant_count': 0, 'duplicate_order_count': 0,
           'duplicate_epoch_count': 0, 'incumbent_epoch_count': 2,
           'successor_capital_before_incumbent_exit_count': 1}
    contract = SimpleNamespace(frozen_baseline=SimpleNamespace(
        champion_minimum_final_wealth=23.28417871275582, champion_maximum_drawdown=.30))
    # Only the pure component is under test; full acceptance still reconciles every raw claim.
    assert _champion_component(raw, contract).passed
    for key, value in [('final_wealth', 23.284178712755819), ('max_drawdown', .30000000000000004),
                       ('account_orders', 16)]:
        changed = copy.deepcopy(raw)
        changed['metrics'][key] = value
        assert not _champion_component(changed, contract).passed
    for key in ('duplicate_grant_count', 'duplicate_order_count', 'duplicate_epoch_count'):
        changed = copy.deepcopy(raw)
        changed[key] = 1
        assert not _champion_component(changed, contract).passed


def test_raw_checkpoint_is_retained_and_read_back_before_acceptance(tmp_path: Path) -> None:
    from uquant.validation.absolute_generalization.runtime import _retain_raw_replay
    raw = _raw()
    path = _retain_raw_replay(tmp_path, 'champion', raw)
    assert json.loads(path.read_text())['raw_replay'] == raw
    assert _retain_raw_replay(tmp_path, 'champion', raw) == path
    path.write_text('{}')
    with pytest.raises(ValueError, match='readback differs'):
        _retain_raw_replay(tmp_path, 'champion', raw)
