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
