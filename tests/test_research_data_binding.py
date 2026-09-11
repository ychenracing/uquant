"""Alternative research bytes must carry an explicit immutable identity."""
import hashlib
import json

import pytest

from research.cross_ai_strategy import ROOT, research_data_root
from uquant.validation.manifest import verify_data_manifest


def dataset(tmp_path, *, research_only=True):
    body = 'date,open,high,low,close,volume,amount\n2023-01-03,10,10,10,10,100,1000\n'
    (tmp_path / 'sh600000.csv').write_text(body)
    digest = hashlib.sha256(body.encode()).hexdigest()
    (tmp_path / 'SHA256SUMS').write_text(f'{digest}  sh600000.csv\n')
    manifest = {'snapshot_id': 'research-test', 'research_only': research_only,
                'production_ready': False, 'parent_frozen_identity':
                verify_data_manifest(ROOT / 'data/frozen'),
                'results': [{'symbol': 'sh600000', 'sha256': digest}]}
    raw = json.dumps(manifest).encode()
    (tmp_path / 'DATA_MANIFEST.json').write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_default_input_stays_frozen():
    assert research_data_root(None, None) == ROOT / 'data/frozen'


def test_research_manifest_requires_an_object(tmp_path):
    raw = b'[]'
    (tmp_path / 'DATA_MANIFEST.json').write_bytes(raw)
    with pytest.raises(ValueError, match='object'):
        research_data_root(tmp_path, hashlib.sha256(raw).hexdigest())


def test_research_input_requires_both_path_and_seal(tmp_path):
    with pytest.raises(ValueError, match='together'):
        research_data_root(tmp_path, None)
    with pytest.raises(ValueError, match='together'):
        research_data_root(None, '0' * 64)


def test_verified_research_dataset_is_bound(tmp_path):
    seal = dataset(tmp_path)
    assert research_data_root(tmp_path, seal) == tmp_path.resolve()


def test_research_metadata_cannot_be_silently_replaced(tmp_path):
    dataset(tmp_path)
    with pytest.raises(ValueError, match='SHA'):
        research_data_root(tmp_path, '0' * 64)


def test_ordinary_data_cannot_impersonate_research_input(tmp_path):
    seal = dataset(tmp_path, research_only=False)
    with pytest.raises(ValueError, match='research-only'):
        research_data_root(tmp_path, seal)


def test_changed_data_bytes_are_detected(tmp_path):
    seal = dataset(tmp_path)
    (tmp_path / 'sh600000.csv').write_text('altered')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        research_data_root(tmp_path, seal)


def test_research_input_cannot_rebind_its_parent(tmp_path):
    dataset(tmp_path)
    path = tmp_path / 'DATA_MANIFEST.json'
    manifest = json.loads(path.read_text())
    manifest['parent_frozen_identity'] = {'snapshot_id': 'different'}
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='parent frozen identity'):
        research_data_root(tmp_path, hashlib.sha256(raw).hexdigest())
