"""A diagnostic cannot expand its window or overwrite prior evidence."""
from pathlib import Path

import pytest

from research.fixed_promotion_screen import diagnose_promotion_unit


def test_unregistered_unit_is_rejected_before_creating_evidence(tmp_path: Path):
    output = tmp_path / "unregistered"
    with pytest.raises(ValueError, match="unregistered diagnostic"):
        diagnose_promotion_unit(case="future_holdout", output=output)
    assert not output.exists()


def test_existing_diagnostic_result_is_not_overwritten(tmp_path: Path):
    result = tmp_path / "bull-result.json"
    result.write_text("prior failed evidence\n")
    with pytest.raises(RuntimeError, match="preserve it"):
        diagnose_promotion_unit(case="bull", output=tmp_path)
    assert result.read_text() == "prior failed evidence\n"
