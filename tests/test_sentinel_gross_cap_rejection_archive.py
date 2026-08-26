from __future__ import annotations

import json
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG


def test_gross_cap_rejection_is_compact_exact_and_non_production() -> None:
    path = Path("artifacts/sentinel/phase6/phase5_gross_cap_rejection.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.stat().st_size < 4_096
    assert payload["status"] == "REJECTED"
    assert payload["production_mode"] == "FREEZE_ONLY"
    assert payload["first_blocking_cell"] == "a/h1_2024"
    assert payload["gate_diagnostics"] == {
        "wealth_retention": 0.9184841626984643,
        "max_drawdown_improvement_percentage_points": 0.0,
        "acute_return_delta": -0.01097916003499022,
        "acute_return_delta_percentage_points": -1.097916003499022,
        "account_order_delta": 4,
        "gross_turnover_delta": 1.2085078385,
    }
    assert payload["large_equity_curves_copied"] is False
    assert payload["phase5_production_code_merged"] is False


def test_legacy_gross_cap_value_fails_with_the_archived_reason() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "LIMITED_GROSS_CAP was rejected by the economic gate; "
            "use FREEZE_ONLY or SHADOW"
        ),
    ):
        DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP")  # type: ignore[arg-type]
