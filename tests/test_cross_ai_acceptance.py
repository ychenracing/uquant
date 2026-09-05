"""Fail-closed checks of the preregistered economic gate, not strategy proof."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.cross_ai_acceptance import CONTRACT_PATH, check_metrics, evaluate, number


def test_champion_floor_is_relative_wealth_and_cannot_pass_on_return_points() -> None:
    t = json.loads(CONTRACT_PATH.read_text())['thresholds']
    assert t['champion_minimum_final_wealth'] == 23.28417871275582
    assert not check_metrics(case='champion', window='continuous_ai_era',
                             metrics={'final_wealth': t['champion_minimum_final_wealth'],
                                      'max_drawdown': 0.30, 'account_orders': 15},
                             baseline={}, benchmark={}, thresholds=t)
    assert check_metrics(case='champion', window='continuous_ai_era',
                         metrics={'final_wealth': 23.28, 'max_drawdown': 0.30, 'account_orders': 15},
                         baseline={}, benchmark={}, thresholds=t)


def test_missing_and_nonfinite_evidence_cannot_pass(tmp_path: Path) -> None:
    for value in (None, float('nan'), float('inf'), True, '1.50'):
        with pytest.raises(ValueError, match='numeric metric'):
            number({'final_wealth': value}, 'final_wealth')
    report = evaluate(tmp_path)
    assert report['status'] == 'FAIL'
    assert len(report['rows']) == 14
    assert all(row['status'] == 'FAIL' for row in report['rows'])
    assert not report['authoritative_promotion']
    assert len(report['cross_window_failures']) == 2
