from pathlib import Path

import pytest

from uquant.contracts.universe import decision_ai_universe, default_ai_universe, research_historical_cohort

LEDGER = Path(__file__).resolve().parents[1] / 'benchmarks/industry_input_v2/historical_business_ledger.json'

def test_fixed_historical_membership_is_isolated_and_dated():
    frozen = default_ai_universe()
    with research_historical_cohort(LEDGER) as cohort:
        assert len(cohort.members) == 23
        assert cohort.symbols_as_of('2022-08-31') == ()
        assert len(cohort.symbols_as_of('2022-09-01')) == 23
        assert default_ai_universe() is frozen
        assert decision_ai_universe() is cohort
        with pytest.raises(RuntimeError), research_historical_cohort(LEDGER):
            pass
    assert decision_ai_universe() is frozen

def test_modified_source_ledger_is_rejected(tmp_path):
    altered = tmp_path / 'ledger.json'
    altered.write_bytes(LEDGER.read_bytes() + b' ')
    with pytest.raises(ValueError), research_historical_cohort(altered):
        pass


def test_historical_leader_reference_uses_members_outside_production():
    import numpy as np
    import pandas as pd

    from uquant.config import DEFAULT_CONFIG
    from uquant.features import compute_features
    from uquant.leader import compute_structural_leaders

    dates = pd.bdate_range('2022-01-03', periods=300)
    close = np.linspace(10, 25, len(dates))
    raw = pd.DataFrame({'open': close, 'high': close * 1.01, 'low': close * .99,
                        'close': close, 'volume': 1e7, 'amount': 2e8}, index=dates)
    features = compute_features(raw, DEFAULT_CONFIG)
    with research_historical_cohort(LEDGER) as cohort:
        symbols = set(cohort.symbols) - set(default_ai_universe().symbols)
        assert len(symbols) == 20
        scores = compute_structural_leaders({s: features for s in sorted(symbols)},
                                            as_of=dates[-1], tech=features, cfg=DEFAULT_CONFIG)
        assert set(scores) == symbols


def test_reviewed_coverage_addition_is_fixed_and_separate(tmp_path):
    supplement = LEDGER.parents[1] / 'historical_coverage_followup_20260911.json'
    with research_historical_cohort(LEDGER) as original:
        original_symbols = set(original.symbols)
    with research_historical_cohort(LEDGER, coverage_supplement=supplement) as extended:
        assert set(extended.symbols) == original_symbols | {'sz300188'}
        assert extended.symbols_as_of('2022-08-31') == ()
        assert extended.sha256 != original.sha256
        assert decision_ai_universe() is extended
    assert decision_ai_universe() is default_ai_universe()
    altered = tmp_path / 'review.json'
    altered.write_bytes(supplement.read_bytes() + b' ')
    with pytest.raises(ValueError), research_historical_cohort(LEDGER, coverage_supplement=altered):
        pass
