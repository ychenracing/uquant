"""Research cohort admission must remain explicit, dated and isolated from production."""
import hashlib
import json
from pathlib import Path

import pytest

from uquant.contracts.universe import (
    ai_universe_manifest_bytes,
    decision_ai_universe,
    default_ai_universe,
    research_cohort_input,
)
from uquant.leader import decision_reference_symbols
from uquant.reference_registry import resolve_reference_symbols


def payload():
    symbols = ['sh603019', 'sh603501', 'sz000938']
    return {
        'schema_version': 1, 'research_only': True, 'production_ready': False,
        'selection_policy': 'fixed_disclosed_entry_cohort', 'known_by': '2022-08-31',
        'parent_frozen_manifest_sha256': hashlib.sha256(ai_universe_manifest_bytes()).hexdigest(),
        'frame_dispositions': [{'symbol': s, 'status': 'supported'} for s in symbols],
        'members': [{'symbol': s, 'industry': 'compute', 'effective_from': '2022-09-01',
                     'source_disclosed_date': '2022-08-01', 'source_sha256': 'a' * 64,
                     'source_url': 'https://example.test/original.pdf'} for s in symbols],
    }


def save(tmp_path: Path, data):
    p = tmp_path / 'cohort.json'
    p.write_text(json.dumps(data))
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def test_default_and_context_restoration(tmp_path):
    before = default_ai_universe()
    p, sha = save(tmp_path, payload())
    with research_cohort_input(p, expected_sha256=sha) as cohort:
        assert decision_ai_universe() == cohort
        assert cohort.symbols_as_of('2022-08-31') == ()
        assert cohort.symbols_as_of('2022-09-01') == ('sh603019', 'sh603501', 'sz000938')
        assert decision_reference_symbols('2022-08-31') == ()
        assert decision_reference_symbols('2023-01-03') == cohort.symbols
        assert resolve_reference_symbols('2022-08-31') == ()
        assert resolve_reference_symbols('2023-01-03') == cohort.symbols
        assert default_ai_universe() == before
    assert decision_ai_universe() == before
    assert decision_reference_symbols('2023-01-03') == before.symbols
    assert resolve_reference_symbols('2023-01-03') == before.symbols_as_of('2023-01-03')


def test_bad_seal_rejected(tmp_path):
    p, _ = save(tmp_path, payload())
    with pytest.raises(ValueError, match='SHA-256'), research_cohort_input(p, expected_sha256='0' * 64):
        pass


@pytest.mark.parametrize('change', ['production', 'parent', 'duplicate', 'foreign', 'early', 'future_source', 'unsupported'])
def test_invalid_membership_rejected(tmp_path, change):
    data = payload()
    if change == 'production':
        data['production_ready'] = True
    if change == 'parent':
        data['parent_frozen_manifest_sha256'] = '0' * 64
    if change == 'duplicate':
        data['members'][1] = data['members'][0].copy()
    if change == 'foreign':
        data['members'][0]['symbol'] = 'sh600001'
    if change == 'early':
        data['members'][0]['effective_from'] = '2022-08-31'
    if change == 'future_source':
        data['members'][0]['source_disclosed_date'] = '2022-09-01'
    if change == 'unsupported':
        data['frame_dispositions'][0]['status'] = 'unresolved'
    p, sha = save(tmp_path, data)
    with pytest.raises(ValueError), research_cohort_input(p, expected_sha256=sha):
        pass
    assert decision_ai_universe() == default_ai_universe()


def test_context_is_not_nested_and_restores_after_error(tmp_path):
    p, sha = save(tmp_path, payload())
    with (
        pytest.raises(RuntimeError, match='nested'),
        research_cohort_input(p, expected_sha256=sha),
        research_cohort_input(p, expected_sha256=sha),
    ):
        pass
    assert decision_ai_universe() == default_ai_universe()


def test_real_dated_cohort_manifest():
    path = Path(__file__).resolve().parents[1] / "benchmarks/historical_cohort_input_v1/manifest.json"
    raw = path.read_bytes()
    data = json.loads(raw)
    assert len(data["frame_dispositions"]) == 90
    assert sum(row["status"] == "unresolved" for row in data["frame_dispositions"]) == 25
    with research_cohort_input(path, expected_sha256=hashlib.sha256(raw).hexdigest()) as cohort:
        assert len(cohort.symbols_as_of("2023-01-03")) == 23
        assert cohort.symbols_as_of("2022-08-31") == ()
        assert cohort.industry_of("sz300212", "2026-05-01") == "storage"
    assert decision_ai_universe() == default_ai_universe()
