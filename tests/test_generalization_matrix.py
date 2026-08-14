from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from uquant.validation import generalization_matrix as matrix_module
from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import (
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from uquant.validation.generalization_matrix import (
    _head_and_source,
    evidence_contract_fingerprint,
    execute_generalization_matrix,
    validate_matrix_artifact,
    window_contract_fingerprint,
)
from uquant.validation.generalization_reference import (
    evaluate_cell_non_regression,
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
    load_generalization_policy,
)
from uquant.validation.universe import load_ai_universe


def _scenarios() -> tuple[Any, ...]:
    universe = load_ai_universe()
    symbols = universe.symbols_as_of("2022-12-30")
    evidence = PreWindowEvidence(
        as_of="2022-12-30",
        scores=tuple((symbol, float(index)) for index, symbol in enumerate(symbols)),
    )
    return build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=evidence,
    )


def _provenance(scenarios: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "head": "a" * 40,
        "source_sha256": "b" * 64,
        "effective_config_sha256": "c" * 64,
        "data": {
            "snapshot_id": "fixture-snapshot",
            "files_verified": 36,
            "manifest_sha256": "d" * 64,
            "checksums_sha256": "e" * 64,
        },
        "runtime": {
            "python_full_version": "3.12.11",
            "numpy_version": "2.2.6",
            "pandas_version": "2.3.1",
            "uv_version": "0.8.4",
            "uv_lock_sha256": "f" * 64,
        },
        "universe_sha256": load_ai_universe().sha256,
        "industry_sha256": "1" * 64,
        "window_fingerprint": window_contract_fingerprint(scenarios),
        "scenario_fingerprint": scenario_contract_fingerprint(scenarios),
        "evidence_fingerprint": evidence_contract_fingerprint(scenarios),
        "lookback_sessions": 120,
    }


def _runner_payload(scenario: Any) -> dict[str, Any]:
    first, second = scenario.symbols[:2]
    sequence = sum(ord(character) for character in scenario.name) % 20
    return {
        "final_wealth": 1.0 + sequence / 100.0,
        "max_drawdown": 0.05 + sequence / 1000.0,
        "account_orders": sequence,
        "gross_turnover": 0.2 + sequence / 100.0,
        "annual_turnover": 0.4 + sequence / 100.0,
        "symbol_pnl": {first: 3.0, second: -1.0},
        "opaque_raw_cell": {"scenario": scenario.name, "values": [1, 2, 3]},
    }


def test_matrix_preserves_every_raw_cell_and_reports_required_aggregates() -> None:
    """Catches dropped raw results or aggregates that omit tail/turnover/concentration."""
    scenarios = _scenarios()
    observed_raw: dict[str, Mapping[str, Any]] = {}

    def runner(scenario: Any) -> Mapping[str, Any]:
        raw = _runner_payload(scenario)
        observed_raw[scenario.name] = raw
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=_provenance(scenarios),
    )

    economic = [cell for cell in artifact["cells"] if cell["economic"]]
    insufficient = [cell for cell in artifact["cells"] if not cell["economic"]]
    assert len(economic) == 32
    assert len(insufficient) == 7
    assert len(observed_raw) == 32
    assert all(cell["raw"] == observed_raw[cell["scenario"]] for cell in economic)
    assert all(
        cell["status"] == "INSUFFICIENT_SAMPLE"
        and cell["raw"] is None
        and cell["metrics"] is None
        for cell in insufficient
    )
    assert set(artifact["aggregates"]["all"]) >= {
        "median_wealth",
        "worst_wealth",
        "p10_wealth",
        "p90_drawdown",
        "worst_drawdown",
        "median_orders",
        "p90_orders",
        "median_gross_turnover",
        "p90_gross_turnover",
        "worst_gross_turnover",
        "median_top1_concentration",
        "worst_top1_concentration",
        "median_top3_concentration",
        "worst_top3_concentration",
        "median_pnl_hhi",
        "worst_pnl_hhi",
    }
    assert economic[0]["metrics"]["top1_concentration"] == pytest.approx(0.75)
    assert economic[0]["metrics"]["top3_concentration"] == pytest.approx(1.0)
    assert economic[0]["metrics"]["pnl_hhi"] == pytest.approx(0.625)
    assert economic[0]["evidence"] == {
        "as_of": "2022-12-30",
        "eligible_symbols": list(load_ai_universe().symbols_as_of("2022-12-30")),
        "ineligible_symbols": [],
        "lookback_sessions": 120,
        "scores": [
            [symbol, float(index)]
            for index, symbol in enumerate(load_ai_universe().symbols_as_of("2022-12-30"))
        ],
        "sha256": economic[0]["evidence"]["sha256"],
    }
    assert len(economic[0]["evidence"]["sha256"]) == 64
    assert artifact["concentration_definition"]["denominator"] == "sum(abs(symbol_pnl))"


def test_champion_exact_equality_passes_but_mutation_fails() -> None:
    """Catches a default comparison that rejects equality or tolerates a regression."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
    )
    champion = {
        f"{cell['window']}/{cell['scenario']}": copy.deepcopy(cell["metrics"])
        for cell in artifact["cells"]
        if cell["economic"]
    }

    assert validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        champion_cells=champion,
    ) == ()
    mutated = copy.deepcopy(artifact)
    first = next(cell for cell in mutated["cells"] if cell["economic"])
    first["metrics"]["final_wealth"] -= 0.01
    failures = validate_matrix_artifact(
        mutated,
        scenarios=scenarios,
        expected_provenance=provenance,
        champion_cells=champion,
    )
    assert any("champion equality" in failure for failure in failures)


def test_matrix_preserves_replay_error_continues_and_excludes_it_from_quantiles() -> None:
    """Catches one engine exception aborting the matrix or becoming a fake metric."""
    scenarios = _scenarios()
    failing = next(item for item in scenarios if item.name == "random__20__0000")
    executed: list[str] = []

    def runner(scenario: Any) -> dict[str, Any]:
        executed.append(scenario.name)
        if scenario is failing:
            raise RuntimeError("allocator failed\n  without a finite result")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=_provenance(scenarios),
    )

    assert len(executed) == 32
    assert executed[-1] == "random__20__0004"
    error_cell = next(cell for cell in artifact["cells"] if cell["scenario"] == failing.name)
    assert error_cell["raw"] is None
    assert error_cell["metrics"] is None
    assert error_cell["replay_error"] == {
        "exception_type": "RuntimeError",
        "message": "allocator failed without a finite result",
    }
    assert artifact["aggregates"]["all"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["all"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["all"]["replay_error_cells"] == 1
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["by_window"]["h1_2023"]["replay_error_cells"] == 1
    valid_wealth = [
        float(cell["metrics"]["final_wealth"])
        for cell in artifact["cells"]
        if cell["metrics"] is not None
    ]
    assert artifact["aggregates"]["all"]["worst_wealth"] == min(valid_wealth)
    assert artifact["passed"] is False
    assert artifact["failures"] == [
        "cell replay failed: h1_2023/random__20__0000: RuntimeError: "
        "allocator failed without a finite result"
    ]


def test_matrix_validator_rejects_replay_error_with_fabricated_metrics_or_missing_cell() -> None:
    """Catches error evidence being converted to metrics or silently dropped."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios)
    failing = next(item for item in scenarios if item.name == "random__20__0000")

    def runner(scenario: Any) -> dict[str, Any]:
        if scenario is failing:
            raise RuntimeError("fixed replay failure")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=provenance,
    )
    fabricated = copy.deepcopy(artifact)
    error_cell = next(
        cell for cell in fabricated["cells"] if cell["scenario"] == failing.name
    )
    error_cell["raw"] = _runner_payload(failing)
    error_cell["metrics"] = next(
        cell["metrics"] for cell in artifact["cells"] if cell["metrics"] is not None
    )
    fabricated_failures = validate_matrix_artifact(
        fabricated,
        scenarios=scenarios,
        expected_provenance=provenance,
    )
    assert any("replay error" in failure for failure in fabricated_failures)

    missing = copy.deepcopy(artifact)
    missing["cells"] = [
        cell for cell in missing["cells"] if cell["scenario"] != failing.name
    ]
    missing_failures = validate_matrix_artifact(
        missing,
        scenarios=scenarios,
        expected_provenance=provenance,
    )
    assert any("missing cell records" in failure for failure in missing_failures)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nonfinite", "stale"])
def test_matrix_validation_fails_closed_on_incomplete_or_stale_artifacts(mutation: str) -> None:
    """Catches matrix aggregation that accepts missing/duplicate/invalid evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
    )
    changed = copy.deepcopy(artifact)
    if mutation == "missing":
        changed["cells"].pop()
    elif mutation == "duplicate":
        changed["cells"].append(copy.deepcopy(changed["cells"][0]))
    elif mutation == "nonfinite":
        next(cell for cell in changed["cells"] if cell["economic"])["raw"][
            "final_wealth"
        ] = float("nan")
    else:
        changed["provenance"]["head"] = "9" * 40

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
    )
    assert failures
    assert any(mutation in failure or "cell" in failure for failure in failures)


def test_zero_symbol_pnl_has_defined_non_fabricated_zero_concentration() -> None:
    """Catches NaN or invented attribution when exact symbol PnL has no mass."""
    scenarios = _scenarios()

    def zero_runner(scenario: Any) -> dict[str, Any]:
        raw = _runner_payload(scenario)
        raw["symbol_pnl"] = {}
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=zero_runner,
        provenance=_provenance(scenarios),
    )
    metrics = next(cell["metrics"] for cell in artifact["cells"] if cell["economic"])
    assert metrics["top1_concentration"] == 0.0
    assert metrics["top3_concentration"] == 0.0
    assert metrics["pnl_hhi"] == 0.0


@pytest.mark.parametrize(
    "mutation",
    ["schema", "gate", "concentration", "aggregate", "aggregate_nonfinite", "state"],
)
def test_matrix_validator_recomputes_top_level_contract(mutation: str) -> None:
    """Catches forged top-level gate state, definitions, or aggregate evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
    )
    changed = copy.deepcopy(artifact)
    if mutation == "schema":
        changed["schema_version"] = 99
    elif mutation == "gate":
        changed["gate"] = "not-the-generalization-gate"
    elif mutation == "concentration":
        changed["concentration_definition"]["denominator"] = "signed PnL"
    elif mutation == "aggregate":
        changed["aggregates"]["all"]["median_wealth"] = 999.0
    elif mutation == "aggregate_nonfinite":
        changed["aggregates"]["all"]["median_wealth"] = float("nan")
    else:
        changed["passed"] = False
        changed["failures"] = ["fabricated"]

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
    )
    assert failures
    assert any(mutation.split("_")[0] in failure or "gate state" in failure for failure in failures)


def _write_source_fixture(root: Path) -> None:
    paths = {
        "pyproject.toml": "[project]\nname='fixture'\n",
        "requirements.txt": "pandas==3.0.5\n",
        "uv.lock": "version = 1\n",
        "benchmarks/reference_registry.json": '{"reference_symbols":["a"]}\n',
        "uquant/module.py": "VALUE = 1\n",
        "uquant/validation/resources/ai_universe_manifest.json": '{"members":[]}\n',
    }
    for relative, content in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


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


def test_untouched_champion_has_exact_equality_but_records_frozen_gate_failures() -> None:
    """Catches Pareto-only equality or dishonest suppression of champion failures."""
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
    assert result["passed"] is False
    assert result["economic_cells_expected"] == 192
    assert result["economic_cells_valid"] == 191
    assert result["replay_error_cells"] == 1
    assert any("continuous_ai_era/random__20__0000" in item for item in result["failures"])
    assert any("random tail" in item for item in result["failures"])
    assert not any("exact equality differs" in item for item in result["failures"])
    assert not any("intrinsic directional" in item for item in result["failures"])


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
