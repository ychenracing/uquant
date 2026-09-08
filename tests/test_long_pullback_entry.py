"""Fixed long-pullback proof; original input excerpts are not new economics."""
from __future__ import annotations

import copy
import io
import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.features import compute_features
from uquant.ordinary_pullback import current_pullback_proof, pullback_quality
from uquant.types import LeaderScore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/ordinary_pullback_original_inputs.json'
EPISODES = (
    ('production-no_optical-h1_2024', 'sh688041', '2024-01-10'),
)


def test_original_losing_snapshot_is_not_excluded_by_the_quality_conjunction():
    # The complete old leader object is missing; assert only preserved raw fields,
    # rather than manufacturing a full original entry certificate.
    record = json.loads(FIXTURE.read_text())['records'][0]
    assert record['leader'] is None
    snapshot = record['qualification_snapshot']
    assert pullback_quality(snapshot, DEFAULT_CONFIG)
    assert DEFAULT_CONFIG.tactical_rebound_min_ret60 <= snapshot['ret60']
    assert snapshot['ret60'] < DEFAULT_CONFIG.tactical_rebound_oversold_min_ret60
    assert snapshot['ret20'] <= DEFAULT_CONFIG.tactical_rebound_breadth_max_ret20
    assert snapshot['ret120'] <= DEFAULT_CONFIG.tactical_rebound_max_ret120


@lru_cache(maxsize=2)
def _native_inputs(case, symbol, session):
    record = next(r for r in json.loads(FIXTURE.read_text())['records']
                  if (r['symbol'], r['date']) == (symbol, session))
    score = record['leader']
    lines = []
    with (ROOT / 'data/frozen' / (symbol + '.csv')).open() as handle:
        lines.append(next(handle))
        for line in handle:
            if line.split(',', 1)[0] > session:
                break  # No future/holdout rows loaded into the test frame.
            lines.append(line)
    frame = compute_features(pd.read_csv(io.StringIO(''.join(lines)), parse_dates=['date'])
                             .set_index('date'), DEFAULT_CONFIG)
    snapshot = record['qualification_snapshot']
    return symbol, pd.Timestamp(session), frame, LeaderScore(**score), snapshot


def _proof(inputs):
    symbol, date, frame, leader, _ = inputs
    return current_pullback_proof(symbol=symbol, date=date, frame=frame,
                                  leader=leader, cfg=DEFAULT_CONFIG)


@pytest.mark.parametrize('case,symbol,session', EPISODES)
def test_original_complete_positive_input_qualifies(case, symbol, session):
    inputs = _native_inputs(case, symbol, session)
    snapshot = inputs[-1]
    result = _proof(inputs)
    assert result['block'] == 'READY'
    # Provenance/value contract: no manufactured score/price or boolean READY.
    for key in ('ret20', 'ret60', 'ret120', 'secular_score', 'momentum60',
                'momentum120', 'relative_strength'):
        assert result['values'][key] == pytest.approx(snapshot[key])
    assert snapshot['ret120'] < 0
    assert inputs[3].mature is False  # No rewriting maturity to obtain proof.


@pytest.mark.parametrize('field', ['close', 'ma120', 'ret20', 'ret60', 'ret120'])
@pytest.mark.parametrize('invalid', [None, float('nan'), float('inf'), -float('inf'), True, False])
def test_invalid_current_price_or_return_cannot_certify(field, invalid):
    inputs = list(_native_inputs(*EPISODES[0]))
    inputs[2] = inputs[2].copy()
    inputs[2][field] = inputs[2][field].astype(object)
    inputs[2].loc[inputs[1], field] = invalid
    assert _proof(inputs)['block'] != 'READY'


@pytest.mark.parametrize('field', ['secular_score', 'secular_confidence', 'momentum60',
                                    'momentum120', 'relative_strength', 'industry_inference_confidence'])
@pytest.mark.parametrize('invalid', [None, float('nan'), float('inf'), True, False])
def test_missing_or_non_numeric_quality_is_rejected(field, invalid):
    inputs = list(_native_inputs(*EPISODES[0]))
    inputs[3] = copy.deepcopy(inputs[3])
    if invalid is None:
        inputs[3].components.pop(field, None)
    else:
        inputs[3].components[field] = invalid
    assert _proof(inputs)['block'] != 'READY'


@pytest.mark.parametrize('invalid', [None, float('nan'), float('inf'), True, False])
def test_invalid_confidence_is_not_numeric_evidence(invalid):
    inputs = list(_native_inputs(*EPISODES[0]))
    inputs[3] = copy.deepcopy(inputs[3])
    inputs[3] = replace(inputs[3], confidence=invalid)
    assert _proof(inputs)['block'] != 'READY'


@pytest.mark.parametrize('restriction', ['absent_session', 'short_history', 'no_amount',
                                         'illiquid', 'too_few_positive_amounts', 'unknown_symbol',
                                         'leader_identity_mismatch', 'unknown_industry'])
def test_history_liquidity_and_current_identity_boundaries(restriction):
    inputs = list(_native_inputs(*EPISODES[0]))
    if restriction == 'absent_session':
        inputs[2] = inputs[2].drop(index=inputs[1])
    elif restriction == 'short_history':
        inputs[2] = inputs[2].tail(120)
    elif restriction == 'no_amount':
        inputs[2] = inputs[2].drop(columns='amount')
    elif restriction == 'illiquid':
        inputs[2] = inputs[2].assign(amount=DEFAULT_CONFIG.minimum_median_amount / 2)
    elif restriction == 'too_few_positive_amounts':
        inputs[2] = inputs[2].copy()
        inputs[2].loc[inputs[2].index[-20:-9], 'amount'] = 0
    elif restriction == 'unknown_symbol':
        inputs[0] = 'not_a_pit_member'
        inputs[3] = replace(inputs[3], symbol=inputs[0])
    elif restriction == 'leader_identity_mismatch':
        inputs[0] = 'sh688072'
    else:
        inputs[3] = replace(inputs[3], industry='unknown', components=dict(inputs[3].components))
        inputs[3].components['unknown_industry'] = 1.
    assert _proof(inputs)['block'] != 'READY'
