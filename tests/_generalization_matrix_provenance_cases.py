from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization_matrix import (
    _write_source_fixture,
)

from uquant.validation import generalization_matrix as matrix_module
from uquant.validation.generalization_matrix import (
    _head_and_source,
)
from uquant.validation.generalization_reference import (
    evaluate_cell_non_regression,
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
    load_generalization_policy,
)


def test_matrix_source_provenance_rejects_dirty_reference_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a dirty reference context being outside the HEAD guard."""
    _write_source_fixture(tmp_path)
    observed_status: tuple[str, ...] = ()

    def fake_git(root: Path, arguments: Any) -> str:
        nonlocal observed_status
        args = tuple(arguments)
        if args[0] == "status":
            observed_status = args
            return " M benchmarks/reference_registry.json\n" if "benchmarks/reference_registry.json" in args else ""
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="committed source"):
        _head_and_source(tmp_path)
    assert "benchmarks/reference_registry.json" in observed_status

def test_matrix_source_provenance_rejects_dirty_config_governance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches dirty parameter governance being outside the exact HEAD guard."""
    _write_source_fixture(tmp_path)
    observed_status: tuple[str, ...] = ()

    def fake_git(root: Path, arguments: Any) -> str:
        nonlocal observed_status
        args = tuple(arguments)
        if args[0] == "status":
            observed_status = args
            return (
                " M benchmarks/config_parameter_governance.json\n"
                if "benchmarks/config_parameter_governance.json" in args
                else ""
            )
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="committed source"):
        _head_and_source(tmp_path)
    assert "benchmarks/config_parameter_governance.json" in observed_status

def test_matrix_source_provenance_rejects_committed_registry_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches current and HEAD hashes agreeing while decision registry differs."""
    _write_source_fixture(tmp_path)

    def fake_git(root: Path, arguments: Any) -> str:
        args = tuple(arguments)
        if args[0] == "status":
            return ""
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            if relative == "benchmarks/reference_registry.json":
                return '{"reference_symbols":["different"]}\n'
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="exact checked-out HEAD"):
        _head_and_source(tmp_path)

def test_matrix_source_provenance_rejects_committed_governance_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a governance artifact that differs byte-for-byte from exact HEAD."""
    _write_source_fixture(tmp_path)

    def fake_git(root: Path, arguments: Any) -> str:
        args = tuple(arguments)
        if args[0] == "status":
            return ""
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            if relative == "benchmarks/config_parameter_governance.json":
                return '{"artifact_sha256":"different"}\n'
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="exact checked-out HEAD"):
        _head_and_source(tmp_path)

def test_untouched_champion_exact_equality_is_an_accepted_policy_result() -> None:
    """Catches requiring a Pareto improvement over exact reviewed evidence."""
    baseline = load_generalization_baseline()
    policy = load_generalization_policy()
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
    )

    assert result["exact_equality_passed"] is True
    assert result["passed"] is True
    assert result["champion_equality_accepted"] is True
    assert result["failures"] == []
    assert result["economic_cells_expected"] == 192
    assert result["economic_cells_valid"] == 191
    assert result["replay_error_cells"] == 1
    assert any(
        not item["literal_passed"] for item in result["random_tail_results"]
    )
    assert all(item["passed"] for item in result["random_tail_results"])
    assert all(
        item["non_regression_passed"] == item["passed"]
        for item in result["random_tail_results"]
    )
    assert any(item["grandfathered"] for item in result["random_tail_results"])

def test_equal_champion_tail_bounds_survive_a_benign_non_tail_improvement() -> None:
    """Catches benign candidate drift reviving absolute floors the champion never met."""
    baseline = load_generalization_baseline()
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    cell = next(
        item
        for item in artifact["cells"]
        if item["family"] == "full" and item["metrics"] is not None
    )
    improved_wealth = float(cell["metrics"]["final_wealth"]) * 1.01
    cell["metrics"]["final_wealth"] = improved_wealth
    cell["raw"]["final_wealth"] = improved_wealth

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=load_generalization_policy(),
    )

    assert result["exact_equality_passed"] is False
    assert result["champion_equality_accepted"] is False
    assert result["passed"] is True
    assert result["failures"] == []

def test_grandfathered_random_tail_rejects_worsening_beyond_the_baseline() -> None:
    """Catches grandfathering turning a frozen tail ceiling into an unbounded waiver."""
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    group = [
        item
        for item in artifact["cells"]
        if item["window"] == "continuous_ai_era"
        and item["family"] == "random"
        and item["pool_size"] == 15
        and item["metrics"] is not None
    ]
    cell = max(group, key=lambda item: float(item["metrics"]["max_drawdown"]))
    worsened_drawdown = float(cell["metrics"]["max_drawdown"]) + 0.001
    cell["metrics"]["max_drawdown"] = worsened_drawdown
    cell["raw"]["max_drawdown"] = worsened_drawdown

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
    )

    assert result["exact_equality_passed"] is False
    assert result["champion_equality_accepted"] is False
    assert result["passed"] is False
    assert len(result["failures"]) == 1
    assert "continuous_ai_era/size-15: p90 drawdown" in result["failures"][0]
    failed_tail = next(
        item
        for item in result["random_tail_results"]
        if item["window"] == "continuous_ai_era" and item["pool_size"] == 15
    )
    assert failed_tail["non_regression_passed"] is False
    assert failed_tail["grandfathered"] is False

def test_champion_equality_acceptance_does_not_hide_a_genuine_cell_degradation() -> None:
    """Catches an equality exemption bypassing the frozen per-cell non-regression gate."""
    baseline = load_generalization_baseline()
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    cell = next(item for item in artifact["cells"] if item["metrics"] is not None)
    degraded_wealth = float(cell["metrics"]["final_wealth"]) * 0.94
    cell["metrics"]["final_wealth"] = degraded_wealth
    cell["raw"]["final_wealth"] = degraded_wealth

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=load_generalization_policy(),
    )

    assert result["exact_equality_passed"] is False
    assert result["champion_equality_accepted"] is False
    assert result["passed"] is False
    assert any("cell non-regression failed" in item for item in result["failures"])

def test_relative_cell_policy_accepts_equality_and_enforces_literal_boundaries() -> None:
    """Catches equality rejection or weakened wealth/risk/activity non-regression."""
    policy = load_generalization_policy()
    reference = {
        "final_wealth": 2.0,
        "max_drawdown": 0.10,
        "account_orders": 10,
        "gross_turnover": 4.0,
        "annual_turnover": 2.0,
        "top1_concentration": 0.5,
        "top3_concentration": 0.8,
        "pnl_hhi": 0.4,
    }

    assert evaluate_cell_non_regression(reference, reference, policy=policy) == ()
    assert evaluate_cell_non_regression(
        {**reference, "final_wealth": 1.899999}, reference, policy=policy
    ) == ("final_wealth 1.899999 is below 95% reference 1.9",)
    assert evaluate_cell_non_regression(
        {**reference, "max_drawdown": 0.120001}, reference, policy=policy
    ) == ("max_drawdown 0.120001 exceeds reference-plus-buffer 0.12",)
    assert evaluate_cell_non_regression(
        {**reference, "account_orders": 12}, reference, policy=policy
    ) == ("account_orders 12 exceeds reference activity limit 11",)
    assert evaluate_cell_non_regression(
        {**reference, "gross_turnover": 4.400001}, reference, policy=policy
    ) == ("gross_turnover 4.400001 exceeds 110% reference 4.4",)
    assert evaluate_cell_non_regression(
        {**reference, "annual_turnover": 2.200001}, reference, policy=policy
    ) == ("annual_turnover 2.200001 exceeds 110% reference 2.2",)

def test_zero_reference_turnover_requires_candidate_zero() -> None:
    """Catches a ratio fallback that permits activity where the champion had none."""
    policy = load_generalization_policy()
    reference = {
        "final_wealth": 1.0,
        "max_drawdown": 0.0,
        "account_orders": 0,
        "gross_turnover": 0.0,
        "annual_turnover": 0.0,
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    candidate = {**reference, "gross_turnover": 0.000001}

    assert evaluate_cell_non_regression(candidate, reference, policy=policy) == (
        "gross_turnover 1e-06 must remain zero because reference is zero",
    )

@pytest.mark.parametrize(
    "mutation",
    (
        "missing_cell",
        "metrics_removed",
        "fabricated_insufficient_evidence",
        "contract_mismatch",
        "duplicate_cell",
        "extra_cell",
        "malformed_cell",
        "nonfinite_metric",
        "provenance_mismatch",
        "aggregate_mismatch",
        "replay_error_mismatch",
        "finite_metrics_mismatch",
    ),
)
def test_exact_equality_fails_closed_for_every_incomplete_or_mismatched_binding(
    mutation: str,
) -> None:
    """Catches structural failures being reported while exact equality stays true."""
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "missing_cell":
        artifact["cells"].pop()
    elif mutation == "metrics_removed":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ] = None
    elif mutation == "fabricated_insufficient_evidence":
        next(cell for cell in artifact["cells"] if not cell["economic"])["raw"] = {
            "fabricated": True
        }
    elif mutation == "contract_mismatch":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "evidence"
        ]["sha256"] = "0" * 64
    elif mutation == "duplicate_cell":
        artifact["cells"].append(copy.deepcopy(artifact["cells"][0]))
    elif mutation == "extra_cell":
        extra = copy.deepcopy(artifact["cells"][0])
        extra["window"] = "extra-window"
        extra["scenario"] = "extra-scenario"
        artifact["cells"].append(extra)
    elif mutation == "malformed_cell":
        artifact["cells"].append({"window": "h1_2023"})
    elif mutation == "nonfinite_metric":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ]["final_wealth"] = float("nan")
    elif mutation == "provenance_mismatch":
        artifact["provenance"]["data"]["snapshot_id"] = "drifted-snapshot"
    elif mutation == "aggregate_mismatch":
        artifact["aggregates"]["all"]["median_wealth"] += 0.000001
    elif mutation == "replay_error_mismatch":
        next(cell for cell in artifact["cells"] if cell["replay_error"] is not None)[
            "replay_error"
        ]["message"] = "different canonical replay failure"
    else:
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ]["final_wealth"] += 0.000001

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
        require_exact_equality=True,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any("exact equality differs" in failure for failure in result["failures"])

@pytest.mark.parametrize(
    "mutation",
    (
        "insufficient_nullable_fields_deleted",
        "successful_replay_error_deleted",
        "replay_error_metrics_deleted",
        "replay_error_raw_deleted",
        "extra_top_level_field",
        "extra_provenance_field",
        "extra_runtime_field",
        "extra_data_field",
        "missing_universe_binding",
        "missing_scenario_binding",
        "missing_window_binding",
        "extra_cell_field",
        "extra_evidence_field",
        "successful_raw_tamper",
    ),
)
def test_exact_equality_rejects_schema_presence_and_raw_evidence_drift(
    mutation: str,
) -> None:
    """Catches absent nullable fields and unbound artifact structure or raw evidence."""
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    successful = next(cell for cell in artifact["cells"] if cell["metrics"] is not None)
    replay_error = next(
        cell for cell in artifact["cells"] if cell["replay_error"] is not None
    )
    if mutation == "insufficient_nullable_fields_deleted":
        insufficient = next(cell for cell in artifact["cells"] if not cell["economic"])
        for field in ("raw", "metrics", "replay_error"):
            del insufficient[field]
    elif mutation == "successful_replay_error_deleted":
        del successful["replay_error"]
    elif mutation == "replay_error_metrics_deleted":
        del replay_error["metrics"]
    elif mutation == "replay_error_raw_deleted":
        del replay_error["raw"]
    elif mutation == "extra_top_level_field":
        artifact["extra"] = None
    elif mutation == "extra_provenance_field":
        artifact["provenance"]["extra"] = None
    elif mutation == "extra_runtime_field":
        artifact["provenance"]["runtime"]["extra"] = None
    elif mutation == "extra_data_field":
        artifact["provenance"]["data"]["extra"] = None
    elif mutation == "missing_universe_binding":
        del artifact["provenance"]["universe_sha256"]
    elif mutation == "missing_scenario_binding":
        del artifact["provenance"]["scenario_fingerprint"]
    elif mutation == "missing_window_binding":
        del artifact["provenance"]["window_fingerprint"]
    elif mutation == "extra_cell_field":
        successful["extra"] = None
    elif mutation == "extra_evidence_field":
        successful["evidence"]["extra"] = None
    else:
        successful["raw"]["final_wealth"] += 0.000001

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
        require_exact_equality=True,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any("exact equality differs" in failure for failure in result["failures"])
