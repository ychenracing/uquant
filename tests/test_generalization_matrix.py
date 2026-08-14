from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import pytest

from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import (
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from uquant.validation.generalization_matrix import (
    execute_generalization_matrix,
    validate_matrix_artifact,
    window_contract_fingerprint,
)
from uquant.validation.universe import load_ai_universe


def _scenarios() -> tuple[Any, ...]:
    universe = load_ai_universe()
    evidence = PreWindowEvidence(
        as_of="2022-12-30",
        scores=tuple((symbol, float(index)) for index, symbol in enumerate(universe.symbols)),
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
