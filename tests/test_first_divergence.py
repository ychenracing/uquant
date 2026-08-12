from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from research.candidate_runner import CandidateRunner, DecisionTrace
from research.first_divergence import first_economic_divergence, trace_backtest
from uquant.engine import ProductionEngine

SYMBOLS = ("sz300308", "sz300502", "sz300394")
START = "2026-06-25"
END = "2026-07-03"


def test_candidate_trace_exposes_reference_and_risk_evidence() -> None:
    names = {field.name for field in fields(DecisionTrace)}

    assert {"reference_evidence", "risk_evidence"} <= names


def test_candidate_trace_captures_immutable_reference_and_risk_values(data_dir) -> None:
    trace = CandidateRunner(data_dir).trace_cell(
        symbols=SYMBOLS,
        start=START,
        end=END,
    )

    assert trace.observations
    assert all(item.reference_evidence for item in trace.observations)
    assert all(dict(item.reference_evidence)["reference_coverage"] for item in trace.observations)
    assert all("family_votes" in dict(item.risk_evidence) for item in trace.observations)


def test_trace_backtest_preserves_production_backtest_result_and_captures_causal_rows(
    data_dir,
) -> None:
    """Replacing the temporary observe-only wrapper must not alter replay economics."""
    expected = ProductionEngine(data_dir).backtest(
        symbols=SYMBOLS,
        start=START,
        end=END,
    )
    traced_engine = ProductionEngine(data_dir)
    original_decide = traced_engine.decide
    result, trace = trace_backtest(
        traced_engine,
        symbols=SYMBOLS,
        start=START,
        end=END,
    )

    for field in (
        "decision_digests",
        "final_wealth",
        "max_drawdown",
        "order_ledger",
        "submission_ledger",
        "final_account",
    ):
        assert result[field] == expected[field]
    assert result["final_account"]["fills"] == expected["final_account"]["fills"]
    assert traced_engine.decide == original_decide

    assert tuple(row["date"] for row in trace) == tuple(sorted(row["date"] for row in trace))
    assert tuple(row["date"] for row in trace) == tuple(
        item["date"] for item in result["daily_risk_states"]
    )
    assert trace
    required = {
        "date",
        "opportunity",
        "risk",
        "family_votes",
        "sector_guard_active",
        "capital_damage",
        "capital_budget_level",
        "equity",
        "reference_evidence",
        "risk_evidence",
        "ranked_leaders",
        "strategic_targets",
        "target_gross",
        "actual_gross",
        "new_fills",
        "pending_orders",
    }
    assert all(required <= set(row) for row in trace)
    for row in trace:
        assert all(fill["fill_date"] == row["date"] for fill in row["new_fills"])


def test_trace_backtest_assigns_each_executed_fill_once_to_its_decision_date(data_dir) -> None:
    """Each next-open fill belongs to the following close decision row exactly once."""
    result, trace = trace_backtest(
        ProductionEngine(data_dir),
        symbols=SYMBOLS,
        start=START,
        end=END,
    )

    traced_fills = [fill for row in trace for fill in row["new_fills"]]
    final_fills = result["final_account"]["fills"]

    assert traced_fills == final_fills
    assert all(
        fill["fill_date"] == row["date"]
        for row in trace
        for fill in row["new_fills"]
    )


def test_first_economic_divergence_ignores_metrics_and_returns_first_action() -> None:
    """A diagnostic metric must not hide the earliest changed executable decision."""
    common: dict[str, Any] = {
        "opportunity": "TREND",
        "risk": "NORMAL",
        "strategic_targets": {"sz300308": 0.5},
        "target_gross": 0.5,
        "actual_gross": 0.4,
        "new_fills": (),
        "pending_orders": (),
    }
    left: tuple[Mapping[str, Any], ...] = (
        {"date": "2026-01-02", **common, "final_wealth": 1.0},
        {"date": "2026-01-05", **common, "final_wealth": 1.01},
    )
    right: tuple[Mapping[str, Any], ...] = (
        {"date": "2026-01-02", **common, "final_wealth": 0.1},
        {
            "date": "2026-01-05",
            **common,
            "pending_orders": (("BUY", "sz300502", 0.5),),
            "final_wealth": 1.01,
        },
    )

    divergence = first_economic_divergence(left, right)

    assert divergence is not None
    assert divergence["date"] == "2026-01-05"
    assert divergence["changed_fields"] == ("pending_orders",)
    assert divergence["left"] == left[1]
    assert divergence["right"] == right[1]


def test_first_economic_divergence_rejects_misaligned_trace_dates() -> None:
    with pytest.raises(ValueError, match="aligned dates"):
        first_economic_divergence(
            ({"date": "2026-01-02"},),
            ({"date": "2026-01-05"},),
        )


def test_installed_project_exposes_research_package_outside_checkout(tmp_path: Path) -> None:
    """A clean test invocation must import the installed research API, not this checkout."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    process = subprocess.run(
        [sys.executable, "-c", "from research import trace_backtest; print(trace_backtest.__name__)"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "trace_backtest"
