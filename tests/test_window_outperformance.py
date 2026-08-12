from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_window_outperformance.py"
SPEC = importlib.util.spec_from_file_location("window_outperformance_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
outperformance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = outperformance
SPEC.loader.exec_module(outperformance)


def _row(system: str, pool: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "system": system,
        "pool": pool,
        "start": outperformance.TARGET_START,
        "end": outperformance.TARGET_END,
        "final_wealth": 2.0 if system == "uquant" else 1.5,
        "max_drawdown": 0.10 if system == "uquant" else 0.15,
        "account_orders": 5 if system == "uquant" else 6,
        "gross_turnover": 1.0,
        "acute_return": 0.01 if system == "uquant" else 0.0,
        "evidence_sha256": "a" * 64,
    }
    row.update(overrides)
    return row


def _complete_rows() -> list[dict[str, object]]:
    return [
        _row(system, pool)
        for system in outperformance.SYSTEMS
        for pool in outperformance.POOLS
    ]


def test_acute_return_requires_both_common_boundaries() -> None:
    curve = [
        {"date": outperformance.ACUTE_START, "equity": 100.0},
        {"date": "2026-07-15", "equity": 90.0},
        {"date": outperformance.ACUTE_END, "equity": 110.0},
    ]

    assert outperformance._acute_return(curve) == pytest.approx(0.10)
    with pytest.raises(RuntimeError, match="acute interval boundaries"):
        outperformance._acute_return(curve[1:])


def test_evaluate_requires_exactly_twenty_unique_cells() -> None:
    rows = _complete_rows()

    report = outperformance.evaluate(rows)

    assert report["summary"] == {"cells": 20, "pairwise_comparisons": 15}
    assert report["passed"]
    with pytest.raises(RuntimeError, match="missing outperformance cells"):
        outperformance.evaluate(rows[:-1])
    with pytest.raises(RuntimeError, match="duplicate outperformance cell"):
        outperformance.evaluate([*rows, dict(rows[0])])


@pytest.mark.parametrize(
    ("field", "value", "predicate"),
    [
        ("final_wealth", 1.49, "final_wealth"),
        ("max_drawdown", 0.151, "max_drawdown"),
        ("account_orders", 7, "account_orders"),
        ("acute_return", -0.01, "acute_return"),
    ],
)
def test_each_dominance_predicate_fails_independently(
    field: str,
    value: float | int,
    predicate: str,
) -> None:
    rows = _complete_rows()
    for row in rows:
        if row["system"] == "uquant" and row["pool"] == "a":
            row[field] = value

    report = outperformance.evaluate(rows)

    assert not report["passed"]
    comparison = report["comparisons"]["a/aquant"]
    assert not comparison["predicates"][predicate]
    assert f"a/aquant:{predicate}" in report["failures"]


def test_equal_on_all_four_metrics_is_not_full_outperformance() -> None:
    rows = _complete_rows()
    competitor = next(row for row in rows if row["system"] == "aquant" and row["pool"] == "a")
    candidate = next(row for row in rows if row["system"] == "uquant" and row["pool"] == "a")
    for field in ("final_wealth", "max_drawdown", "account_orders", "acute_return"):
        candidate[field] = competitor[field]

    report = outperformance.evaluate(rows)

    assert not report["passed"]
    assert "a/aquant:strictly_better" in report["failures"]
