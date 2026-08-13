from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from uquant.validation.ai_era import AI_ERA_WINDOWS


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_pareto_evidence.py"
    assert path.is_file(), "Pareto evidence runner is missing"
    spec = importlib.util.spec_from_file_location("run_pareto_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _metric() -> dict[str, float | int]:
    return {"final_wealth": 1.0, "max_drawdown": 0.0, "account_orders": 0}


def _windows() -> dict[str, dict[str, str]]:
    return {name: {"start": start, "end": end} for name, (start, end) in AI_ERA_WINDOWS.items()}


def test_reference_audit_reports_missing_full_gates_without_promoting_partial_evidence(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "benchmarks" / "competitor_bull_reference.json",
        {"results": {str(index): _metric() for index in range(15)}},
    )
    report = _module().audit_references(tmp_path)

    assert report["competitor"]["required_cells"] == 90
    assert report["competitor"]["reviewed_reference"] == "missing"
    assert report["competitor"]["partial_bull_cells"] == 15
    assert report["generalization"]["reviewed_reference"] == "missing"
    assert report["generalization"]["diagnostic_smoke_cases"] == 0
    assert report["can_run_fail_closed_gates"] is False


def test_reference_audit_recognizes_complete_reviewed_files(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "benchmarks" / "competitor_matrix_reference.json",
        {
            "windows": _windows(),
            "results": {str(index): _metric() for index in range(90)},
        },
    )
    _write_json(
        tmp_path / "benchmarks" / "generalization_baseline.json",
        {"references": {str(index): {} for index in range(20)}},
    )

    report = _module().audit_references(tmp_path)

    assert report["competitor"]["reviewed_reference"] == "present"
    assert report["competitor"]["reviewed_cells"] == 90
    assert report["generalization"]["reviewed_reference"] == "present"
    assert report["can_run_fail_closed_gates"] is True


def test_reference_audit_rejects_non_ai_era_competitor_windows(tmp_path: Path) -> None:
    legacy_windows = _windows()
    legacy_windows.pop("h1_2023")
    legacy_windows["bear_2022"] = {"start": "2022-01-04", "end": "2022-12-30"}
    _write_json(
        tmp_path / "benchmarks" / "competitor_matrix_reference.json",
        {
            "windows": legacy_windows,
            "results": {str(index): _metric() for index in range(90)},
        },
    )
    _write_json(
        tmp_path / "benchmarks" / "generalization_baseline.json",
        {"references": {"reviewed": {}}},
    )

    report = _module().audit_references(tmp_path)

    assert report["competitor"]["reviewed_reference"] == "incomplete"
    assert report["can_run_fail_closed_gates"] is False


def test_smoke_inputs_reuse_frozen_pool_e_and_point_in_time_industries() -> None:
    root = Path(__file__).resolve().parents[1]

    payload = _module().smoke_inputs(root)

    assert len(payload["universe"]) == 32
    assert payload["prior_symbols"] == ("sz300308", "sz300394", "sz300502")
    assert payload["start"] == AI_ERA_WINDOWS["continuous_ai_era"][0]
    assert payload["end"] == AI_ERA_WINDOWS["continuous_ai_era"][1]
    assert set(payload["industries"]) == set(payload["universe"])
    assert all(industry != "unknown" for industry in payload["industries"].values())
