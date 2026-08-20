from __future__ import annotations

import copy
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from research.candidate_runner import CandidateRunner, DecisionTrace
from research.first_divergence import (
    first_economic_divergence,
    first_executable_divergence,
    trace_backtest,
)
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
        "reference_context",
        "leaders",
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
        "fills",
        "pending_orders",
        "orders",
        "order_ledger",
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


def test_trace_backtest_rejects_pre_ai_era_economic_start(data_dir) -> None:
    with pytest.raises(ValueError, match="2023-01-01"):
        trace_backtest(
            ProductionEngine(data_dir),
            symbols=SYMBOLS,
            start="2022-12-30",
            end="2023-01-05",
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
    assert divergence["changed_fields"] == ("orders",)
    assert divergence["first_stage"] == "orders"
    assert divergence["left"] == left[1]
    assert divergence["right"] == right[1]


def test_first_economic_divergence_orders_changes_by_causal_stage() -> None:
    """A downstream change must not be reported ahead of its upstream cause."""
    left: tuple[Mapping[str, Any], ...] = (
        {
            "date": "2026-01-05",
            "reference_context": {"breadth20": 0.25},
            "leaders": ({"symbol": "sz300308", "score": 0.8},),
            "risk": {"state": "NORMAL", "reduction_level": 0},
            "opportunity": "TREND",
            "targets": ({"symbol": "sz300308", "weight": 0.5},),
            "orders": ({"symbol": "sz300308", "side": "BUY", "target_weight": 0.5},),
            "fills": ({"symbol": "sz300308", "side": "BUY", "shares": 100},),
        },
    )
    right: tuple[Mapping[str, Any], ...] = (
        {
            "date": "2026-01-05",
            "reference_context": {"breadth20": 0.75},
            "leaders": ({"symbol": "sz300502", "score": 0.9},),
            "risk": {"state": "CAUTION", "reduction_level": 1},
            "opportunity": "CHOPPY",
            "targets": ({"symbol": "sz300502", "weight": 0.25},),
            "orders": ({"symbol": "sz300502", "side": "BUY", "target_weight": 0.25},),
            "fills": ({"symbol": "sz300502", "side": "BUY", "shares": 50},),
        },
    )

    divergence = first_economic_divergence(left, right)

    assert divergence is not None
    assert divergence["changed_fields"] == (
        "reference_context",
        "leaders",
        "risk",
        "opportunity",
        "targets",
        "orders",
        "fills",
    )
    assert divergence["first_stage"] == "reference_context"


def test_first_executable_divergence_skips_evidence_only_changes() -> None:
    left = (
        {"date": "2023-01-03", "risk": {"state": "NORMAL", "votes": 0}},
        {
            "date": "2023-01-04",
            "risk": {"state": "NORMAL", "votes": 0},
            "targets": [{"symbol": "a", "weight": 0.5}],
            "orders": [],
        },
    )
    right = (
        {"date": "2023-01-03", "risk": {"state": "NORMAL", "votes": 1}},
        {
            "date": "2023-01-04",
            "risk": {"state": "NORMAL", "votes": 1},
            "targets": [{"symbol": "a", "weight": 0.8}],
            "orders": [{"symbol": "a", "side": "BUY", "target_weight": 0.8}],
        },
    )

    divergence = first_executable_divergence(left, right)

    assert divergence is not None
    assert divergence["date"] == "2023-01-04"
    assert divergence["first_stage"] == "orders"
    assert divergence["changed_fields"] == ("risk", "targets", "orders")


def test_first_economic_divergence_ignores_free_text_and_entry_metadata() -> None:
    """Diagnostic prose and entry attribution must not manufacture a decision change."""
    row: dict[str, Any] = {
        "date": "2026-01-05",
        "reference_context": {"breadth20": 0.75},
        "leaders": ({"symbol": "sz300308", "score": 0.9},),
        "risk": {"state": "NORMAL", "shock_state": "NONE"},
        "risk_evidence": {"effective_config_sha256": "a" * 64},
        "opportunity": "TREND",
        "targets": (
            {
                "symbol": "sz300308",
                "weight": 0.5,
                "lifecycle": "CORE",
                "reduction_policy": "FIFO",
                "reason_code": "strategy_target",
                "exit_kind": "strategy",
                "reason": "first explanation",
                "alpha_score": 0.81,
                "confidence": 0.72,
                "entry_industry_strength": 0.65,
            },
        ),
        "orders": (
            {
                "order_id": "ord-1",
                "symbol": "sz300308",
                "side": "BUY",
                "target_weight": 0.5,
                "reduction_policy": "FIFO",
                "reason_code": "strategy_target",
                "exit_kind": "strategy",
                "reason": "first explanation",
                "entry_score": 0.81,
                "entry_confidence": 0.72,
                "entry_regime": "TREND",
                "entry_industry_strength": 0.65,
            },
        ),
        "fills": (
            {
                "fill_date": "2026-01-06",
                "symbol": "sz300308",
                "side": "BUY",
                "shares": 100,
                "price": 20.0,
                "reason_code": "strategy_target",
                "exit_kind": "strategy",
                "reason": "first explanation",
                "sold_tranches": [
                    {
                        "tranche_id": "t-1",
                        "shares": 100,
                        "lifecycle": "CORE",
                        "entry_score": 0.81,
                        "entry_confidence": 0.72,
                        "entry_regime": "TREND",
                        "entry_industry_strength": 0.65,
                    }
                ],
            },
        ),
    }
    metadata_only = copy.deepcopy(row)
    metadata_only["targets"][0]["reason"] = "rewritten explanation"
    metadata_only["targets"][0]["alpha_score"] = 0.99
    metadata_only["targets"][0]["confidence"] = 0.99
    metadata_only["targets"][0]["entry_industry_strength"] = 0.99
    metadata_only["orders"][0]["reason"] = "rewritten explanation"
    metadata_only["orders"][0]["entry_score"] = 0.99
    metadata_only["orders"][0]["entry_confidence"] = 0.99
    metadata_only["orders"][0]["entry_regime"] = "CHOPPY"
    metadata_only["orders"][0]["entry_industry_strength"] = 0.99
    metadata_only["fills"][0]["reason"] = "rewritten explanation"
    metadata_only["fills"][0]["sold_tranches"][0]["entry_score"] = 0.99
    metadata_only["fills"][0]["sold_tranches"][0]["entry_confidence"] = 0.99
    metadata_only["fills"][0]["sold_tranches"][0]["entry_regime"] = "CHOPPY"
    metadata_only["fills"][0]["sold_tranches"][0]["entry_industry_strength"] = 0.99
    metadata_only["risk_evidence"]["effective_config_sha256"] = "b" * 64

    assert first_economic_divergence((row,), (metadata_only,)) is None

    executable_change = copy.deepcopy(metadata_only)
    executable_change["orders"][0]["reason_code"] = "risk_gross_cap"
    divergence = first_economic_divergence((row,), (executable_change,))
    assert divergence is not None
    assert divergence["changed_fields"] == ("orders",)


def test_first_economic_divergence_rejects_pre_ai_era_dates() -> None:
    with pytest.raises(ValueError, match="2023-01-01"):
        first_economic_divergence(
            ({"date": "2022-12-30"},),
            ({"date": "2022-12-30"},),
        )


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
