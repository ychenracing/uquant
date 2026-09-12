"""Reviewed historical input must be consumed and fail on missing source coverage."""
import hashlib
import json

import pytest

from uquant.data import DataStore
from uquant.validation.manifest import verify_data_manifest


def bundle(tmp_path):
    raw = (b'date,open,high,low,close,volume,amount,reported_change\n'
           b'2023-08-09,56,57,55,56,1000,56000,0\n'
           b'2023-08-10,57,58,56,57,1000,57000,1\n')
    (tmp_path / 'sh688008.csv').write_bytes(raw)
    (tmp_path / 'source.txt').write_text('unit fixture: two sessions with no corporate action')
    document = {
        'schema': 'historical-raw-shares', 'start': '2023-08-09', 'end': '2023-08-10',
        'sessions': ['2023-08-09', '2023-08-10'],
        'files': {'sh688008': hashlib.sha256(raw).hexdigest()},
        'sources': {'fixture': {'path': 'source.txt', 'url': 'https://example.org/unit-fixture',
                                'sha256': hashlib.sha256((tmp_path / 'source.txt').read_bytes()).hexdigest()}},
        'coverage': {'sh688008': {'reviewed_from': '2023-08-09', 'reviewed_through': '2023-08-10',
                                    'action_sources': ['fixture'], 'absent_sessions': {}}},
        'actions': [], 'tax_debits': [],
    }
    (tmp_path / 'ACCOUNT_INPUT.json').write_text(json.dumps(document))
    return document


def test_data_store_consumes_reviewed_raw_account_input(tmp_path):
    bundle(tmp_path)
    frame = DataStore(tmp_path).load('sh688008')
    assert frame['signal_close'].tolist() == [56, 57]
    assert frame['reference_close'].tolist() == [56, 56]
    assert frame['close'].tolist() == [56, 57]


def test_missing_source_and_unexplained_calendar_gap_are_rejected(tmp_path):
    document = bundle(tmp_path)
    (tmp_path / 'source.txt').write_text('changed source')
    with pytest.raises((RuntimeError, ValueError), match='source bytes'):
        DataStore(tmp_path).load('sh688008')
    document = bundle(tmp_path)
    path = tmp_path / 'sh688008.csv'
    path.write_text('\n'.join(path.read_text().splitlines()[:2]) + '\n')
    document['files']['sh688008'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / 'ACCOUNT_INPUT.json').write_text(json.dumps(document))
    with pytest.raises((RuntimeError, ValueError), match='calendar differs'):
        DataStore(tmp_path).load('sh688008')


def test_missing_amount_is_not_imputed_in_raw_account_input(tmp_path):
    document = bundle(tmp_path)
    path = tmp_path / 'sh688008.csv'
    path.write_text(path.read_text().replace(',56000,', ',,'))
    document['files']['sh688008'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / 'ACCOUNT_INPUT.json').write_text(json.dumps(document))
    with pytest.raises((RuntimeError, ValueError), match='amount'):
        DataStore(tmp_path).load('sh688008')


def test_sealed_snapshot_binds_account_terms_as_well_as_prices(tmp_path):
    document = bundle(tmp_path)
    raw_sha = document['files']['sh688008']
    (tmp_path / 'SHA256SUMS').write_text(f'{raw_sha}  sh688008.csv\n')
    manifest = {'snapshot_id': 'unit-raw-account',
                'results': [{'symbol': 'sh688008', 'sha256': raw_sha}]}
    path = tmp_path / 'DATA_MANIFEST.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='raw account input differs'):
        verify_data_manifest(tmp_path)
    manifest['account_input_sha256'] = hashlib.sha256((tmp_path / 'ACCOUNT_INPUT.json').read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))
    assert verify_data_manifest(tmp_path)['account_input_sha256'] == manifest['account_input_sha256']
    document['coverage']['sh688008']['action_sources'] = []
    (tmp_path / 'ACCOUNT_INPUT.json').write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match='raw account input differs'):
        verify_data_manifest(tmp_path)
