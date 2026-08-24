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
)

from uquant.validation.generalization import (
    GeneralizationScenario,
    evaluate_generalization,
    load_generalization_baseline,
    reference_payload,
)


def test_no_optical_and_industry_only_require_deployed_core_or_strategic_exposure(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_reference_payload(cases, observations)), encoding="utf-8")
    baseline = load_generalization_baseline(path, cases)

    report = evaluate_generalization(
        cases,
        lambda case: {
            "final_wealth": 2.0,
            "max_drawdown": 0.08,
            "account_orders": 4,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": (
                [] if case.family in {"no_optical", "industry_only"} else _deployed_exposure(case)
            ),
        },
        industries=_industries(),
        baseline=baseline,
    )

    assert not report["passed"]
    assert not report["deployment_gate"]["no_optical"]["passed"]
    assert report["scenarios"]["no_optical"]["deployed_exposure"] == []
    assert any(
        "no deployed non-optical Core or Strategic exposure" in item
        for item in report["scenarios"]["no_optical"]["violations"]
    )
    industry_names = [case.name for case in cases if case.family == "industry_only"]
    assert all(not report["deployment_gate"][name]["passed"] for name in industry_names)
    assert all(
        any("expected-industry Core or Strategic" in item for item in report["scenarios"][name]["violations"])
        for name in industry_names
    )

def test_remove_all_requires_95_percent_of_reviewed_competitor_best(tmp_path: Path) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    policy = _policy()
    policy["wealth_floor_ratio"] = 0.80
    payload = reference_payload(
        cases,
        observations,
        policy=policy,
        provenance=_provenance(),
        competitor_best=_competitor_best(value=2.0),
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    baseline = load_generalization_baseline(path, cases)

    report = evaluate_generalization(
        cases,
        lambda case: {
            "final_wealth": 1.89 if case.family == "remove_all" else 2.0,
            "max_drawdown": 0.08,
            "account_orders": 4,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        },
        industries=_industries(),
        baseline=baseline,
    )

    assert not report["passed"]
    assert report["competitor_best"]["remove_all_wealth_floor"] == pytest.approx(1.90)
    assert any(
        "reviewed competitor-best floor" in item
        for item in report["scenarios"]["remove_all_priors"]["violations"]
    )

def test_baseline_policy_is_complete_strict_and_cannot_weaken_remove_one_gate(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    payload = _reference_payload(cases, observations)
    path = tmp_path / "baseline.json"

    missing = json.loads(json.dumps(payload))
    missing["policy"].pop("pareto_order_regression")
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(RuntimeError, match="policy is missing fields"):
        load_generalization_baseline(path, cases)

    unexpected = json.loads(json.dumps(payload))
    unexpected["policy"]["silent_typo"] = 0.0
    path.write_text(json.dumps(unexpected), encoding="utf-8")
    with pytest.raises(RuntimeError, match="policy has unexpected fields"):
        load_generalization_baseline(path, cases)

    weak_dependency_gate = json.loads(json.dumps(payload))
    weak_dependency_gate["policy"]["remove_one_max_dependency"] = 0.26
    path.write_text(json.dumps(weak_dependency_gate), encoding="utf-8")
    with pytest.raises(RuntimeError, match="dependency ceiling"):
        load_generalization_baseline(path, cases)

    nonstandard = json.dumps(payload).replace(
        '"drawdown_tolerance": 0.02',
        '"drawdown_tolerance": NaN',
    )
    path.write_text(nonstandard, encoding="utf-8")
    with pytest.raises(RuntimeError, match="non-standard number"):
        load_generalization_baseline(path, cases)

def test_dominance_and_pareto_reject_even_when_scenario_tolerances_allow_regression(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case, wealth=2.0, drawdown=0.10, orders=5) for case in cases)
    policy = _policy()
    policy.update(
        wealth_floor_ratio=0.80,
        drawdown_tolerance=0.10,
        order_tolerance=10,
        order_ceiling_ratio=2.0,
    )
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(_reference_payload(cases, observations, policy=policy)),
        encoding="utf-8",
    )
    baseline = load_generalization_baseline(path, cases)

    report = evaluate_generalization(
        cases,
        lambda case: {
            "final_wealth": 1.90,
            "max_drawdown": 0.11,
            "account_orders": 6,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        },
        industries=_industries(),
        baseline=baseline,
    )

    assert all(
        item["violations"] == ["dominance violation: wealth fell while drawdown and orders rose materially"]
        for item in report["scenarios"].values()
    )
    assert report["gate_results"]["dominance"]["dominated_scenarios"] == [
        case.name for case in sorted(cases, key=lambda item: item.name)
    ]
    assert not report["gate_results"]["dominance"]["passed"]
    assert not report["gate_results"]["pareto"]["passed"]
    assert not report["passed"]
    assert any(item.startswith("dominance:") for item in report["failures"])
    assert any(item.startswith("pareto:") for item in report["failures"])

def test_pareto_accepts_materially_lower_drawdown_and_orders_without_wealth_loss(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case, wealth=2.0, drawdown=0.10, orders=10) for case in cases)
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(_reference_payload(cases, observations)),
        encoding="utf-8",
    )
    baseline = load_generalization_baseline(path, cases)

    report = evaluate_generalization(
        cases,
        lambda case: {
            "final_wealth": 2.0,
            "max_drawdown": 0.07,
            "account_orders": 8,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        },
        industries=_industries(),
        baseline=baseline,
    )

    assert report["passed"]
    assert report["gate_results"]["pareto"]["passed"]
    assert report["gate_results"]["pareto"]["material_improvements"] == {
        "wealth": False,
        "drawdown": True,
        "orders": True,
    }

def test_dependency_removal_and_random_family_policies_fail_closed(tmp_path: Path) -> None:
    cases, _ = _matrix(random_seeds=range(3))
    observations = tuple(_observation(case, wealth=2.0, drawdown=0.10, orders=5) for case in cases)
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(_reference_payload(cases, observations)),
        encoding="utf-8",
    )
    baseline = load_generalization_baseline(path, cases)
    worst_remove_one = next(case.name for case in cases if case.family == "remove_one")

    def runner(case: GeneralizationScenario) -> dict[str, Any]:
        wealth = 1.80
        drawdown = 0.12
        if case.name == "base":
            wealth = 2.0
        elif case.name == worst_remove_one:
            wealth = 1.40
        elif case.family == "remove_all":
            wealth, drawdown = 1.0, 0.30
        elif case.family == "no_optical":
            wealth, drawdown = 0.99, 0.30
        elif case.family == "random":
            wealth = 0.90
        return {
            "final_wealth": wealth,
            "max_drawdown": drawdown,
            "account_orders": 5,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        }

    report = evaluate_generalization(
        cases,
        runner,
        industries=_industries(),
        baseline=baseline,
    )

    assert not report["passed"]
    assert report["prior_dependence"]["PDI_1"] == pytest.approx(0.30)
    assert any(
        "remove-one dependency" in item for item in report["scenarios"][worst_remove_one]["violations"]
    )
    assert any(
        "positive-return floor" in item for item in report["scenarios"]["remove_all_priors"]["violations"]
    )
    assert any("no-optical ceiling" in item for item in report["scenarios"]["no_optical"]["violations"])
    assert report["random_gate"] == {
        "passed": False,
        "positive_fraction": 0.0,
        "p10_wealth": pytest.approx(0.90),
        "violations": [
            "positive fraction 0.000000 below 0.510000",
            "p10 wealth 0.900000 below 1.010000",
        ],
    }
    assert all(report["scenarios"][case.name]["violations"] for case in cases if case.family == "random")
