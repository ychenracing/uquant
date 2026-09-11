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


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ('{"key": 1, "key": 2}', "duplicate key"),
        ('{"key": NaN}', "non-standard number"),
        ('[]', "must be a JSON object"),
    ),
)
def test_public_spec_loader_rejects_corrupt_baselines(
    tmp_path: Path, payload: str, message: str,
) -> None:
    from uquant.validation.promotion import load_promotion_spec

    baseline = tmp_path / "baseline.json"
    baseline.write_text(payload)
    with pytest.raises(RuntimeError, match=message):
        load_promotion_spec(baseline)
    assert baseline.read_text() == payload
