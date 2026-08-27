from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from research.strategic_evidence.absolute_policy import evaluate_absolute_policy
from research.strategic_evidence.contract import load_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")


def _forced_owner_summary(status: str = "SUCCESS") -> dict[str, Any]:
    controls = [
        {
            "control_id": f"POSITIVE_CONTROL:{owner}",
            "owner": owner,
            "owner_role": "POSITIVE_CONTROL",
        }
        for owner in CONTRACT.positive_controls
    ]
    controls.extend(
        {"control_id": control_id, "owner": owner, "owner_role": control_id}
        for control_id, owner in (
            ("LOWEST_LIQUID_LEADER_SCORE", "sh688037"),
            ("NEGATIVE_RET120_AND_WEAK_TREND", "sh603986"),
            ("LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE", "sh688498"),
        )
    )
    cells = [
        {
            "cell_id": f"{control['control_id']}:{control['owner']}:{mode}",
            "status": status,
            "metrics": {
                "final_wealth": 2.0,
                "max_drawdown": 0.1,
                "longest_healthy_zero_target_streak": 1,
                "positive_target_sessions": 1,
            },
        }
        for control in controls
        for mode in ("COMMON_ACTIVATION_DATE", "NATIVE_ELIGIBILITY_DATE")
    ]
    return {
        "controls": controls,
        "required_cell_ids": [cell["cell_id"] for cell in cells],
        "cells": cells,
        "status_counts": {status: len(cells)},
    }


def _witness_summary() -> dict[str, Any]:
    cells = [
        {
            "cell_id": f"CANONICAL_LEAVE_ONE_OUT:{symbol}:FULL_REMOVAL",
            "status": "SUCCESS",
            "spec": {
                "scope": "CANONICAL_LEAVE_ONE_OUT",
                "axis": "FULL_REMOVAL",
                "subject": symbol,
                "evidence_class": "ECONOMIC",
            },
            "metrics": {
                "final_wealth": 2.0,
                "max_drawdown": 0.1,
                "longest_healthy_zero_target_streak": 1,
                "positive_target_sessions": 1,
            },
        }
        for symbol in CONTRACT.canonical_universe
    ]
    return {"initial_cells": cells, "search_cells": []}


def _reachability_rows() -> list[dict[str, Any]]:
    return [
        {
            "state_id": state_id,
            "path_id": path_id,
            "status": "SUCCESS",
            "evidence_class": "DIAGNOSTIC_ONLY",
            "analysis": {
                "metrics": {
                    "budget_repair_healthy_sessions": {
                        "1_to_0": 1,
                        "2_to_1": 1,
                        "3_to_2": 1,
                        "4_to_3": 1,
                    },
                    "failed_grant_retry_healthy_sessions": 1,
                    "longest_healthy_zero_target_streak": 1,
                    "terminal_scc_healthy_zero_target_duration": 1,
                    "witness_missing_recovery_fraction": 1.0,
                },
                "repeated_crowning": {
                    "distinct_owners": ["sz300308", "sz300502"],
                    "strategic_epochs": [1, 2],
                    "satisfied": True,
                },
                "findings": [{"observation_id": f"R{index}", "observed": True} for index in range(1, 9)],
            },
        }
        for state_id in CONTRACT.initial_state_ids
        for path_id in CONTRACT.path_ids
    ]


def test_policy_separates_runner_success_from_capability_pass() -> None:
    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=_forced_owner_summary(),
        witness=_witness_summary(),
        reachability_rows=_reachability_rows(),
    )

    assert result.runner_success is True
    assert result.capability_pass is False
    assert "canonical.percentile_method_preregistered" in result.failed_check_ids
    assert result.check("canonical.positive_return_fraction").passed is True
    assert result.check("canonical.worst_healthy_zero_target_streak").passed is True
    assert result.check("critical_removal.literal_thresholds").passed is True


def test_forced_owner_coverage_cannot_be_self_declared() -> None:
    forced = _forced_owner_summary()
    for index, cell in enumerate(forced["cells"]):
        cell["cell_id"] = f"SELF_DECLARED:{index}"
    forced["required_cell_ids"] = [cell["cell_id"] for cell in forced["cells"]]

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=forced,
        witness=_witness_summary(),
        reachability_rows=_reachability_rows(),
    )

    assert result.runner_success is False
    assert result.check("forced_owner.exact_coverage").passed is False


def test_critical_removal_thresholds_are_evaluated_without_percentiles() -> None:
    witness = _witness_summary()
    target = next(cell for cell in witness["initial_cells"] if cell["spec"]["subject"] == "sz300308")
    target["metrics"]["final_wealth"] = 0.99

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=_forced_owner_summary(),
        witness=witness,
        reachability_rows=_reachability_rows(),
    )

    check = result.check("critical_removal.literal_thresholds")
    assert check.passed is False
    assert check.actual["failures"] == [
        {
            "actual": 0.99,
            "metric": "final_wealth",
            "symbol": "sz300308",
            "threshold": 1.0,
        }
    ]


@pytest.mark.parametrize("status", ["REPLAY_ERROR", "INSUFFICIENT_SAMPLE"])
def test_required_terminal_failure_is_preserved_and_fails_capability(status: str) -> None:
    forced = _forced_owner_summary()
    forced["cells"][0]["status"] = status
    forced["cells"][0]["metrics"] = None

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=forced,
        witness=_witness_summary(),
        reachability_rows=_reachability_rows(),
    )

    assert result.runner_success is True
    check = result.check("forced_owner.required_cells_success")
    assert check.passed is False
    assert status in check.actual["status_counts"]


def test_missing_required_cell_fails_runner_and_capability() -> None:
    forced = _forced_owner_summary()
    forced["cells"].pop()

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=forced,
        witness=_witness_summary(),
        reachability_rows=_reachability_rows(),
    )

    assert result.runner_success is False
    assert result.capability_pass is False
    assert result.check("forced_owner.exact_coverage").passed is False


def test_null_literal_metric_fails_closed_without_numeric_coercion() -> None:
    witness = _witness_summary()
    witness["initial_cells"][0]["metrics"]["longest_healthy_zero_target_streak"] = None

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=_forced_owner_summary(),
        witness=witness,
        reachability_rows=_reachability_rows(),
    )

    check = result.check("canonical.literal_metrics_complete")
    assert check.passed is False
    assert check.actual["missing_or_null"] == [
        "CANONICAL_LEAVE_ONE_OUT:sh600487:FULL_REMOVAL:longest_healthy_zero_target_streak"
    ]


def test_non_finite_reachability_metrics_fail_closed() -> None:
    rows = _reachability_rows()
    metrics = rows[0]["analysis"]["metrics"]
    metrics["failed_grant_retry_healthy_sessions"] = float("nan")
    metrics["terminal_scc_healthy_zero_target_duration"] = float("inf")
    metrics["witness_missing_recovery_fraction"] = float("nan")

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=_forced_owner_summary(),
        witness=_witness_summary(),
        reachability_rows=rows,
    )

    assert result.check("reachability.failed_grant_retry").passed is False
    assert result.check("reachability.terminal_scc_healthy_zero_target").passed is False
    assert result.check("reachability.witness_missing_recovery_fraction").passed is False


def test_reachability_does_not_turn_84_successes_into_capability_pass() -> None:
    rows = _reachability_rows()
    target = next(row for row in rows if row["state_id"] == "S09" and row["path_id"] == "P05")
    target["analysis"]["metrics"]["witness_missing_recovery_fraction"] = 0.0
    for row in rows:
        finding = next(
            finding for finding in row["analysis"]["findings"] if finding["observation_id"] == "R7"
        )
        finding["observed"] = False
    rows[0]["analysis"]["repeated_crowning"] = {
        "distinct_owners": ["sz300308"],
        "strategic_epochs": [1],
        "satisfied": False,
    }

    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=_forced_owner_summary(),
        witness=_witness_summary(),
        reachability_rows=rows,
    )

    assert result.check("reachability.execution_coverage").passed is True
    assert result.check("reachability.witness_missing_recovery_fraction").passed is False
    assert result.check("reachability.R7_coverage").passed is False
    assert result.check("reachability.repeated_crowning_all_cells").passed is False
    assert result.capability_pass is False


def test_mutating_caller_payload_after_evaluation_does_not_change_result() -> None:
    forced = _forced_owner_summary()
    result = evaluate_absolute_policy(
        CONTRACT,
        forced_owner=forced,
        witness=_witness_summary(),
        reachability_rows=_reachability_rows(),
    )
    compact = copy.deepcopy(result.compact())

    forced["cells"][0]["status"] = "REPLAY_ERROR"

    assert result.compact() == compact
