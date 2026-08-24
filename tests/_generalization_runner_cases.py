from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization import (
    _competitor_best,
    _deployed_exposure,
    _industries,
    _matrix,
    _observation,
    _policy,
    _provenance,
    _reference_payload,
    _universe,
)

from uquant.validation.generalization import (
    GeneralizationScenario,
    reference_payload,
    run_generalization,
)


def test_run_is_read_only_and_checks_reference_coverage_before_replay(tmp_path: Path) -> None:
    cases, prices = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case, wealth=1.9) for case in cases)
    payload = _reference_payload(cases, observations)
    baseline = tmp_path / "generalization.json"
    baseline.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    before = baseline.read_bytes()
    calls: list[str] = []

    def runner(case: GeneralizationScenario) -> dict[str, Any]:
        calls.append(case.name)
        return {
            "final_wealth": 2.0,
            "max_drawdown": 0.10,
            "account_orders": 5,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        }

    report = run_generalization(
        data_dir="unused",
        universe=_universe(),
        industries=_industries(),
        prior_symbols=_universe()[:3],
        start="2026-01-05",
        end="2026-07-20",
        baseline_path=baseline,
        pre_window_prices=prices,
        runner=runner,
        provenance=_provenance(),
        random_sizes=(6, 12, 24),
        random_seeds=range(1),
        leave_top_k=(1, 2, 3, 5),
    )
    assert calls == [case.name for case in cases]
    assert report["passed"]
    assert report["failures"] == []
    assert report["gate_results"]["dominance"]["passed"]
    assert report["gate_results"]["pareto"]["passed"]
    assert report["scenario_count"] == len(cases)
    assert report["prior_dependence"] == {
        "PDI_1": 0.0,
        "PDI_3": 0.0,
        "PDI_1_worst_case": f"remove_one__{_universe()[0]}",
        "PDI_3_case": "remove_all_priors",
    }
    assert set(report["aggregate"]) == {
        "p10_wealth",
        "median_wealth",
        "p90_drawdown",
        "worst_drawdown",
        "median_orders",
        "p90_orders",
    }
    assert report["industry_pnl_share"]["optical"] == pytest.approx({"pnl": 1.0, "share_of_net_pnl": 1.0})
    assert report["dependency_diagnostics"] == {
        "optical_pnl_share": 1.0,
        "optical_high_dependency": True,
        "optical_dependency_share_threshold": 0.70,
        "diagnostics": ["high industry dependency: optical PnL share 1.000000 exceeds 0.700000"],
    }
    assert report["scenarios"]["industry_only__design"]["diagnostic"] == "singleton"
    assert report["scenarios"]["industry_sparse_combined"] == {
        "passed": True,
        "violations": [],
        "thresholds": {
            "final_wealth_floor": pytest.approx(1.805),
            "max_drawdown_ceiling": pytest.approx(0.12),
            "account_orders_ceiling": 6,
        },
        "family": "industry_only",
        "diagnostic": "combined_sparse",
        "source_industries": ["design", "foundry"],
        "symbol_count": 2,
        "removed_symbols": [],
        "evidence_as_of": cases[0].evidence_as_of,
        "final_wealth": 2.0,
        "max_drawdown": 0.10,
        "account_orders": 5,
        "deployed_exposure": [{"symbol": "s28", "lifecycle": "CORE"}],
    }
    assert all(
        deltas == pytest.approx({"final_wealth": 0.1, "max_drawdown": 0.0, "account_orders": 0})
        for deltas in report["reference_deltas"].values()
    )
    assert baseline.read_bytes() == before

    mismatched_provenance = _provenance()
    mismatched_provenance["data"]["manifest_sha256"] = "0" * 64
    baseline.write_text(
        json.dumps(
            reference_payload(
                cases,
                observations,
                policy=_policy(),
                provenance=mismatched_provenance,
                competitor_best=_competitor_best(),
            )
        ),
        encoding="utf-8",
    )
    calls.clear()
    with pytest.raises(RuntimeError, match="provenance does not match"):
        run_generalization(
            data_dir="unused",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2026-01-05",
            end="2026-07-20",
            baseline_path=baseline,
            pre_window_prices=prices,
            runner=runner,
            provenance=_provenance(),
            random_sizes=(6, 12, 24),
            random_seeds=range(1),
            leave_top_k=(1, 2, 3, 5),
        )
    assert calls == []
    baseline.write_bytes(before)

    def tampering_runner(case: GeneralizationScenario) -> dict[str, Any]:
        baseline.write_bytes(before + b"\n")
        return runner(case)

    with pytest.raises(RuntimeError, match="changed during validation"):
        run_generalization(
            data_dir="unused",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2026-01-05",
            end="2026-07-20",
            baseline_path=baseline,
            pre_window_prices=prices,
            runner=tampering_runner,
            provenance=_provenance(),
            random_sizes=(6, 12, 24),
            random_seeds=range(1),
            leave_top_k=(1, 2, 3, 5),
        )
    baseline.write_bytes(before)

    broken = json.loads(baseline.read_text(encoding="utf-8"))
    broken["references"].pop(cases[-1].name)
    baseline.write_text(json.dumps(broken), encoding="utf-8")
    calls.clear()
    with pytest.raises(RuntimeError, match="missing references"):
        run_generalization(
            data_dir="unused",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2026-01-05",
            end="2026-07-20",
            baseline_path=baseline,
            pre_window_prices=prices,
            runner=runner,
            provenance=_provenance(),
            random_sizes=(6, 12, 24),
            random_seeds=range(1),
            leave_top_k=(1, 2, 3, 5),
        )
    assert calls == []
