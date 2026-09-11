from copy import deepcopy

import pytest

from research.industry_input_audit import cohort_record


def sample():
    features = [{'symbol': s, 'industry': 'materials', 'score': score, 'ret120': score}
                for s, score in zip('abcd', [4, 3, 2, 1], strict=True)]
    labels = {s: {'entry_date': '2023-01-04', 'exit_date': '2023-02-01',
                  'gross_return': outcome} for s, outcome in zip('abcd', [9, 1, 0, -1], strict=True)}
    mapping = {s: {'research_industry': 'optical' if s == 'a' else 'materials',
                   'conservative_known_by': '2022-12-31'} for s in 'abcd'}
    return {'date': '2023-01-03', 'cohort': 'all', 'horizon': 20,
            'features': features, 'labels': labels}, mapping


def test_reclassification_removes_optical_winner_without_changing_saved_data():
    row, mapping = sample()
    original = deepcopy(row)
    result, error = cohort_record(row, mapping, corrected=True)
    assert error is None
    assert result['members'] == list('bcd')
    assert result['pool_return'] == 0
    assert result['methods']['score']['selected'] == list('bcd')
    assert row == original
    # Selection is independent of the forward label.
    row['labels']['d']['gross_return'] = 100
    changed, _ = cohort_record(row, mapping, corrected=True)
    assert changed['methods']['score']['selected'] == result['methods']['score']['selected']


def test_unknown_release_day_and_future_endpoint_fail_closed():
    row, mapping = sample()
    mapping['a']['conservative_known_by'] = row['date']
    result, error = cohort_record(row, mapping, corrected=True)
    assert result is None
    assert error['symbols'] == ['a']
    mapping['a']['conservative_known_by'] = '2022-12-31'
    row['labels']['b']['exit_date'] = '2026-08-06'
    with pytest.raises(ValueError, match='protected'):
        cohort_record(row, mapping, corrected=True)
