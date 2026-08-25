from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization_matrix import (
    _provenance,
    _runner_payload,
    _scenarios,
)

from uquant.attribution import build_economic_attribution
from uquant.config import (
    config_fingerprint,
)
from uquant.engine import code_fingerprint
from uquant.types import (
    AccountState,
)
from uquant.validation.generalization_matrix import (
    execute_generalization_matrix,
    validate_matrix_artifact,
)


def test_matrix_validator_rejects_replay_error_with_fabricated_metrics_or_missing_cell(
    matrix_data_dir: Path,
) -> None:
    """Catches error evidence being converted to metrics or silently dropped."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    failing = next(item for item in scenarios if item.name == "random__20__0000")

    def runner(scenario: Any) -> dict[str, Any]:
        if scenario is failing:
            raise RuntimeError("fixed replay failure")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=provenance,
        data_dir=matrix_data_dir,
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
        data_dir=matrix_data_dir,
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
        data_dir=matrix_data_dir,
    )
    assert any("missing cell records" in failure for failure in missing_failures)

@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nonfinite", "stale"])
def test_matrix_validation_fails_closed_on_incomplete_or_stale_artifacts(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    """Catches matrix aggregation that accepts missing/duplicate/invalid evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
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
        data_dir=matrix_data_dir,
    )
    assert failures
    assert any(mutation in failure or "cell" in failure for failure in failures)

def test_zero_symbol_pnl_has_defined_non_fabricated_zero_concentration(
    matrix_data_dir: Path,
) -> None:
    """Catches NaN or invented attribution when exact symbol PnL has no mass."""
    scenarios = _scenarios()

    def zero_runner(scenario: Any) -> dict[str, Any]:
        raw = _runner_payload(scenario)
        account = AccountState.empty(100.0)
        ledger = [
            {
                "date": date,
                "cash": 100.0,
                "equity": 100.0,
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "cash_weight": 1.0,
                "position_weights": {},
                "daily_pnl": 0.0,
                "target_weights": {},
                "target_gross": 0.0,
                "caps": {"risk_gross": 0.9, "system_gross": 1.0},
                "binding_owner": "STRATEGY",
                "risk_state": "NORMAL",
                "opportunity": "CHOPPY",
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["attribution"] = build_economic_attribution(
            account=account,
            final_prices={},
            sessions=(scenario.window.start, scenario.window.end),
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            final_equity=100.0,
            daily_ledger=ledger,
            benchmark_close={scenario.window.start: 100.0, scenario.window.end: 100.0},
        )
        account.last_successful_run = scenario.window.end
        account.data_hash = "a" * 64
        account.data_hash_as_of = scenario.window.end
        account.code_hash = code_fingerprint()
        raw["final_account"] = account.to_dict()
        raw["final_equity"] = 100.0
        raw["final_wealth"] = 1.0
        raw["account_orders"] = 0
        raw["gross_turnover"] = 0.0
        raw["annual_turnover"] = 0.0
        raw["symbol_pnl"] = {}
        raw["equity_curve"] = [
            {"date": date, "equity": 100.0}
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["daily_replay_evidence"] = [
            {
                "date": date,
                "cash": 100.0,
                "position_shares": {},
                "close_marks": {},
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["decision_trace"] = [
            {
                "schema": "uquant.decision-control-plane.v2",
                "date": date,
                "opportunity": "CHOPPY",
                "risk": {
                    "state": "NORMAL",
                    "target_gross_cap": 0.9,
                    "system_gross_cap": 1.0,
                },
                "target_gross": 0.0,
                "targets": [],
                "orders": [],
                "effective_config_sha256": config_fingerprint(),
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["decision_digests"] = [
            hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for trace in raw["decision_trace"]
        ]
        raw["legacy_decision_digests"] = [
            hashlib.sha256(
                json.dumps(
                    {
                        "date": trace["date"],
                        "opportunity": "CHOPPY",
                        "risk": "NORMAL",
                        "targets": [],
                        "orders": [],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            for trace in raw["decision_trace"]
        ]
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=zero_runner,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    metrics = next(cell["metrics"] for cell in artifact["cells"] if cell["economic"])
    assert metrics["top1_concentration"] == 0.0
    assert metrics["top3_concentration"] == 0.0
    assert metrics["pnl_hhi"] == 0.0

@pytest.mark.parametrize(
    "mutation",
    ["schema", "gate", "concentration", "aggregate", "aggregate_nonfinite", "state"],
)
def test_matrix_validator_recomputes_top_level_contract(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    """Catches forged top-level gate state, definitions, or aggregate evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
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
        data_dir=matrix_data_dir,
    )
    assert failures
    assert any(mutation.split("_")[0] in failure or "gate state" in failure for failure in failures)
