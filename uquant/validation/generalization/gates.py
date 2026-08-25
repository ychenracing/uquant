"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from statistics import median
from typing import Any

from .metrics import (
    aggregate_metrics,
    industry_pnl_shares,
    observation_from_result,
    prior_dependence,
)
from .metrics import (
    quantile as _quantile,
)
from .models import (
    GeneralizationBaseline,
    GeneralizationObservation,
    GeneralizationPolicy,
    GeneralizationScenario,
)


def _reference_aggregate(
    cases: Sequence[GeneralizationScenario],
    references: Mapping[str, Mapping[str, float | int]],
) -> dict[str, float]:
    names = [case.name for case in cases if case.family != "baseline"]
    if not names:
        raise ValueError("generalization gate requires stress scenarios")
    wealth = [float(references[name]["final_wealth"]) for name in names]
    drawdown = [float(references[name]["max_drawdown"]) for name in names]
    orders = [float(references[name]["account_orders"]) for name in names]
    return {
        "p10_wealth": _quantile(wealth, 0.10),
        "median_wealth": float(median(wealth)),
        "p90_drawdown": _quantile(drawdown, 0.90),
        "worst_drawdown": max(drawdown),
        "median_orders": float(median(orders)),
        "p90_orders": _quantile(orders, 0.90),
    }


def _relative_change(candidate: float, reference: float) -> float:
    if abs(reference) <= 1e-12:
        return candidate - reference
    return candidate / reference - 1.0


def _aggregate_gate_results(
    current: Mapping[str, float],
    reference: Mapping[str, float],
    policy: GeneralizationPolicy,
) -> dict[str, dict[str, Any]]:
    """Evaluate aggregate dominance and Pareto conditions against references."""
    wealth_change = _relative_change(current["median_wealth"], reference["median_wealth"])
    drawdown_change = current["worst_drawdown"] - reference["worst_drawdown"]
    order_change = _relative_change(current["median_orders"], reference["median_orders"])
    dominated = bool(
        wealth_change < -policy.dominance_wealth_regression
        and drawdown_change > policy.dominance_drawdown_regression
        and order_change > policy.dominance_order_regression
    )
    improvements = {
        "wealth": wealth_change >= policy.pareto_wealth_improvement,
        "drawdown": -drawdown_change >= policy.pareto_drawdown_improvement,
        "orders": -order_change >= policy.pareto_order_improvement,
    }
    acceptable = {
        "wealth": wealth_change >= -policy.pareto_wealth_regression,
        "drawdown": -drawdown_change >= -policy.pareto_drawdown_regression,
        "orders": -order_change >= -policy.pareto_order_regression,
    }
    pareto_passed = any(improvements.values()) and all(acceptable.values())
    deltas = {
        "median_wealth_relative": wealth_change,
        "worst_drawdown_additive": drawdown_change,
        "median_orders_relative": order_change,
    }
    return {
        "dominance": {
            "passed": not dominated,
            "deltas": deltas,
            "thresholds": {
                "wealth_regression": policy.dominance_wealth_regression,
                "drawdown_regression": policy.dominance_drawdown_regression,
                "order_regression": policy.dominance_order_regression,
            },
        },
        "pareto": {
            "passed": pareto_passed,
            "deltas": deltas,
            "material_improvements": improvements,
            "acceptable_regressions": acceptable,
            "thresholds": {
                "wealth_improvement": policy.pareto_wealth_improvement,
                "drawdown_improvement": policy.pareto_drawdown_improvement,
                "order_improvement": policy.pareto_order_improvement,
                "wealth_regression": policy.pareto_wealth_regression,
                "drawdown_regression": policy.pareto_drawdown_regression,
                "order_regression": policy.pareto_order_regression,
            },
        },
    }


def _evaluate_removal_scenario_limits(
    *,
    add_scenario_violation: Callable[[str, str], None],
    baseline: GeneralizationBaseline,
    no_optical: Sequence[GeneralizationObservation],
    policy: GeneralizationPolicy,
    random_observations: Sequence[GeneralizationObservation],
    remove_all: Sequence[GeneralizationObservation],
    scenario_thresholds: dict[str, dict[str, float | int]],
) -> tuple[float, dict[str, dict[str, Any]]]:
    if len(remove_all) != 1 or len(no_optical) != 1 or not random_observations:
        raise ValueError("generalization gate requires remove-all, no-optical, and random scenarios")
    remove_all_item = remove_all[0]
    competitor_wealth_floor = float(baseline.competitor_best["value"]) * policy.remove_all_competitor_ratio
    scenario_thresholds[remove_all_item.name]["competitor_final_wealth_floor"] = competitor_wealth_floor
    if remove_all_item.final_wealth < policy.remove_all_min_wealth:
        add_scenario_violation(
            remove_all_item.name,
            f"final_wealth below positive-return floor {policy.remove_all_min_wealth:.6f}",
        )
    if remove_all_item.max_drawdown > policy.remove_all_max_drawdown:
        add_scenario_violation(
            remove_all_item.name,
            f"max_drawdown above removal ceiling {policy.remove_all_max_drawdown:.6f}",
        )
    if remove_all_item.final_wealth < competitor_wealth_floor:
        add_scenario_violation(
            remove_all_item.name,
            f"final_wealth below 95%+ reviewed competitor-best floor {competitor_wealth_floor:.6f}",
        )
    no_optical_item = no_optical[0]
    if no_optical_item.final_wealth < policy.no_optical_min_wealth:
        add_scenario_violation(
            no_optical_item.name,
            f"final_wealth below positive-return floor {policy.no_optical_min_wealth:.6f}",
        )
    if no_optical_item.max_drawdown > policy.no_optical_max_drawdown:
        add_scenario_violation(
            no_optical_item.name,
            f"max_drawdown above no-optical ceiling {policy.no_optical_max_drawdown:.6f}",
        )

    deployment_gate: dict[str, dict[str, Any]] = {}
    return competitor_wealth_floor, deployment_gate


def _summarize_generalization_gates(
    *,
    base: GeneralizationObservation,
    baseline: GeneralizationBaseline,
    by_name: Mapping[str, GeneralizationObservation],
    failures: list[str],
    gate_results: dict[str, dict[str, Any]],
    industries: Mapping[str, str],
    policy: GeneralizationPolicy,
) -> tuple[
    list[str],
    bool,
    float,
    dict[str, dict[str, float]],
    dict[str, dict[str, float | int]],
]:
    if not bool(gate_results["dominance"]["passed"]):
        failures.append("dominance: wealth, drawdown, and orders all materially regressed")
    if not bool(gate_results["pareto"]["passed"]):
        failures.append("pareto: no material improvement without material regression")

    pnl_shares = industry_pnl_shares(base, industries)
    optical_share = float(pnl_shares.get("optical", {}).get("share_of_net_pnl", 0.0))
    high_optical_dependency = optical_share > policy.optical_dependency_share_threshold
    diagnostics = (
        [
            "high industry dependency: optical PnL share "
            f"{optical_share:.6f} exceeds {policy.optical_dependency_share_threshold:.6f}"
        ]
        if high_optical_dependency
        else []
    )
    reference_deltas = {
        name: {
            "final_wealth": by_name[name].final_wealth - float(baseline.references[name]["final_wealth"]),
            "max_drawdown": by_name[name].max_drawdown - float(baseline.references[name]["max_drawdown"]),
            "account_orders": by_name[name].account_orders - int(baseline.references[name]["account_orders"]),
        }
        for name in sorted(by_name)
    }
    return diagnostics, high_optical_dependency, optical_share, pnl_shares, reference_deltas


def _evaluate_dependency_gate(
    *,
    add_scenario_violation: Callable[[str, str], None],
    dominated_scenarios: list[str],
    gate_results: dict[str, dict[str, Any]],
    observations: Sequence[GeneralizationObservation],
    policy: GeneralizationPolicy,
) -> dict[str, float | str]:
    aggregate_dominance_passed = bool(gate_results["dominance"]["passed"])
    gate_results["dominance"]["aggregate_passed"] = aggregate_dominance_passed
    gate_results["dominance"]["dominated_scenarios"] = dominated_scenarios
    gate_results["dominance"]["passed"] = aggregate_dominance_passed and not dominated_scenarios

    dependency = prior_dependence(observations)
    pdi_1 = float(dependency["PDI_1"])
    if pdi_1 > policy.remove_one_max_dependency:
        worst_case = str(dependency["PDI_1_worst_case"])
        add_scenario_violation(
            worst_case,
            f"remove-one dependency {pdi_1:.6f} exceeds {policy.remove_one_max_dependency:.6f}",
        )
    return dependency


def _evaluate_scenario_thresholds(
    *,
    by_name: Mapping[str, GeneralizationObservation],
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    add_scenario_violation: Callable[[str, str], None],
) -> tuple[dict[str, dict[str, float | int]], list[str]]:
    scenario_thresholds: dict[str, dict[str, float | int]] = {}
    dominated_scenarios: list[str] = []
    for name in sorted(by_name):
        item = by_name[name]
        reference = baseline.references[name]
        wealth_floor = float(reference["final_wealth"]) * policy.wealth_floor_ratio
        drawdown_ceiling = min(1.0, float(reference["max_drawdown"]) + policy.drawdown_tolerance)
        reference_orders = int(reference["account_orders"])
        order_ceiling = max(
            reference_orders + policy.order_tolerance,
            math.ceil(reference_orders * policy.order_ceiling_ratio),
        )
        scenario_thresholds[name] = {
            "final_wealth_floor": wealth_floor,
            "max_drawdown_ceiling": drawdown_ceiling,
            "account_orders_ceiling": order_ceiling,
        }
        if item.final_wealth < wealth_floor:
            add_scenario_violation(name, f"final_wealth below {wealth_floor:.6f}")
        if item.max_drawdown > drawdown_ceiling:
            add_scenario_violation(name, f"max_drawdown above {drawdown_ceiling:.6f}")
        if item.account_orders > order_ceiling:
            add_scenario_violation(name, f"account_orders above {order_ceiling}")
        wealth_change = _relative_change(item.final_wealth, float(reference["final_wealth"]))
        drawdown_change = item.max_drawdown - float(reference["max_drawdown"])
        order_change = _relative_change(item.account_orders, reference_orders)
        if (
            wealth_change < -policy.dominance_wealth_regression
            and drawdown_change > policy.dominance_drawdown_regression
            and order_change > policy.dominance_order_regression
        ):
            dominated_scenarios.append(name)
            add_scenario_violation(
                name,
                "dominance violation: wealth fell while drawdown and orders rose materially",
            )
    return scenario_thresholds, dominated_scenarios


def _evaluate_deployment_gates(
    *,
    observations: Sequence[GeneralizationObservation],
    case_by_name: Mapping[str, GeneralizationScenario],
    industries: Mapping[str, str],
    deployment_gate: dict[str, dict[str, Any]],
    add_scenario_violation: Callable[[str, str], None],
) -> None:
    for item in observations:
        case = case_by_name[item.name]
        if item.family not in {"no_optical", "industry_only"}:
            continue
        expected_industries = (
            {industry for industry in set(industries.values()) if industry != "optical"}
            if item.family == "no_optical"
            else set(case.source_industries)
        )
        qualifying = tuple(
            (symbol, lifecycle)
            for symbol, lifecycle in item.deployed_exposure
            if lifecycle in {"CORE", "STRATEGIC"} and industries.get(symbol) in expected_industries
        )
        deployment_gate[item.name] = {
            "passed": bool(qualifying),
            "expected_industries": sorted(expected_industries),
            "qualifying_exposure": [
                {"symbol": symbol, "lifecycle": lifecycle} for symbol, lifecycle in qualifying
            ],
        }
        if not qualifying:
            expected_label = "non-optical" if item.family == "no_optical" else "expected-industry"
            add_scenario_violation(
                item.name,
                f"no deployed {expected_label} Core or Strategic exposure",
            )


def _evaluate_random_gate(
    *,
    random_observations: Sequence[GeneralizationObservation],
    policy: GeneralizationPolicy,
    failures: list[str],
    scenario_violations: dict[str, list[str]],
) -> tuple[float, float, list[str]]:
    random_positive_fraction = sum(item.final_wealth > 1.0 for item in random_observations) / len(
        random_observations
    )
    random_p10_wealth = _quantile(
        [item.final_wealth for item in random_observations],
        0.10,
    )
    random_family_failures: list[str] = []
    if random_positive_fraction < policy.random_min_positive_fraction:
        violation = (
            f"positive fraction {random_positive_fraction:.6f} below "
            f"{policy.random_min_positive_fraction:.6f}"
        )
        random_family_failures.append(violation)
        failures.append(f"random: {violation}")
        for item in random_observations:
            if item.final_wealth <= 1.0:
                scenario_violations[item.name].append("random scenario is not profitable")
    if random_p10_wealth < policy.random_p10_min_wealth:
        violation = f"p10 wealth {random_p10_wealth:.6f} below {policy.random_p10_min_wealth:.6f}"
        random_family_failures.append(violation)
        failures.append(f"random: {violation}")
        for item in random_observations:
            if item.final_wealth < policy.random_p10_min_wealth:
                scenario_violations[item.name].append(
                    f"random tail wealth below {policy.random_p10_min_wealth:.6f}"
                )
    return random_positive_fraction, random_p10_wealth, random_family_failures


def _scenario_gate_rows(
    *,
    observations: Sequence[GeneralizationObservation],
    scenario_violations: Mapping[str, list[str]],
    scenario_thresholds: Mapping[str, dict[str, float | int]],
    case_by_name: Mapping[str, GeneralizationScenario],
) -> dict[str, dict[str, Any]]:
    return {
        item.name: {
            "passed": not scenario_violations[item.name],
            "violations": scenario_violations[item.name],
            "thresholds": scenario_thresholds[item.name],
            "family": item.family,
            "diagnostic": case_by_name[item.name].diagnostic,
            "source_industries": list(case_by_name[item.name].source_industries),
            "symbol_count": len(case_by_name[item.name].symbols),
            "removed_symbols": list(case_by_name[item.name].removed_symbols),
            "evidence_as_of": case_by_name[item.name].evidence_as_of,
            "final_wealth": item.final_wealth,
            "max_drawdown": item.max_drawdown,
            "account_orders": item.account_orders,
            "deployed_exposure": [
                {"symbol": symbol, "lifecycle": lifecycle} for symbol, lifecycle in item.deployed_exposure
            ],
        }
        for item in observations
    }


def _generalization_gate_report(
    *,
    failures: list[str],
    baseline: GeneralizationBaseline,
    evidence_as_of: str,
    evidence_eligible: Sequence[str],
    evidence_ineligible: Sequence[str],
    observations: Sequence[GeneralizationObservation],
    aggregate: Mapping[str, float],
    reference_aggregate: Mapping[str, float],
    gate_results: Mapping[str, Any],
    stress: Sequence[GeneralizationObservation],
    families: Sequence[str],
    dependency: Mapping[str, Any],
    pnl_shares: Mapping[str, Any],
    optical_share: float,
    high_optical_dependency: bool,
    diagnostics: list[str],
    competitor_wealth_floor: float,
    random_family_failures: list[str],
    random_positive_fraction: float,
    random_p10_wealth: float,
    deployment_gate: Mapping[str, Any],
    reference_deltas: Mapping[str, Any],
    scenario_violations: Mapping[str, list[str]],
    scenario_thresholds: Mapping[str, dict[str, float | int]],
    case_by_name: Mapping[str, GeneralizationScenario],
) -> dict[str, Any]:
    policy = baseline.policy
    return {
        "passed": not failures,
        "failures": failures,
        "baseline_sha256": baseline.sha256,
        "case_fingerprint": baseline.case_fingerprint,
        "pre_window_evidence": {
            "as_of": evidence_as_of,
            "eligible_symbols": list(evidence_eligible),
            "ineligible_symbols": list(evidence_ineligible),
        },
        "policy": policy.to_dict(),
        "validation_fingerprint": baseline.validation_fingerprint,
        "provenance": baseline.provenance,
        "competitor_best": {
            **baseline.competitor_best,
            "required_ratio": policy.remove_all_competitor_ratio,
            "remove_all_wealth_floor": competitor_wealth_floor,
        },
        "scenario_count": len(observations),
        "aggregate": aggregate,
        "reference_aggregate": reference_aggregate,
        "gate_results": gate_results,
        "by_family": {
            family: aggregate_metrics(tuple(item for item in stress if item.family == family))
            for family in families
        },
        "prior_dependence": dependency,
        "industry_pnl_share": pnl_shares,
        "dependency_diagnostics": {
            "optical_pnl_share": optical_share,
            "optical_high_dependency": high_optical_dependency,
            "optical_dependency_share_threshold": policy.optical_dependency_share_threshold,
            "diagnostics": diagnostics,
        },
        "random_gate": {
            "passed": not random_family_failures,
            "positive_fraction": random_positive_fraction,
            "p10_wealth": random_p10_wealth,
            "violations": random_family_failures,
        },
        "deployment_gate": deployment_gate,
        "reference_deltas": reference_deltas,
        "scenarios": _scenario_gate_rows(
            observations=observations,
            scenario_violations=scenario_violations,
            scenario_thresholds=scenario_thresholds,
            case_by_name=case_by_name,
        ),
    }


def evaluate_generalization(
    cases: Sequence[GeneralizationScenario],
    runner: Callable[[GeneralizationScenario], Mapping[str, Any]],
    *,
    industries: Mapping[str, str],
    baseline: GeneralizationBaseline,
) -> dict[str, Any]:
    """Run a validated case matrix and enforce its reviewed economic policy."""
    case_by_name = {case.name: case for case in cases}
    if len(case_by_name) != len(cases):
        raise ValueError("generalization case matrix contains duplicate names")
    evidence_memberships = {
        (
            case.evidence_as_of,
            case.evidence_eligible_symbols,
            case.evidence_ineligible_symbols,
        )
        for case in cases
    }
    if len(evidence_memberships) != 1:
        raise ValueError("generalization scenarios contain inconsistent pre-window evidence membership")
    evidence_as_of, evidence_eligible, evidence_ineligible = next(iter(evidence_memberships))
    observations = tuple(observation_from_result(case, runner(case)) for case in cases)
    by_name = {item.name: item for item in observations}
    if len(by_name) != len(observations):
        raise RuntimeError("generalization runner produced duplicate observation names")
    stress = tuple(item for item in observations if item.family != "baseline")
    families = sorted({item.family for item in stress})
    base = next(item for item in observations if item.family == "baseline")
    policy = baseline.policy
    aggregate = aggregate_metrics(stress)
    reference_aggregate = _reference_aggregate(cases, baseline.references)
    gate_results = _aggregate_gate_results(aggregate, reference_aggregate, policy)
    failures: list[str] = []
    scenario_violations: dict[str, list[str]] = {name: [] for name in by_name}

    def add_scenario_violation(name: str, violation: str) -> None:
        """Record one scenario-local violation in both report indexes."""
        scenario_violations[name].append(violation)
        failures.append(f"{name}: {violation}")

    scenario_thresholds, dominated_scenarios = _evaluate_scenario_thresholds(
        by_name=by_name,
        baseline=baseline,
        policy=policy,
        add_scenario_violation=add_scenario_violation,
    )

    dependency = _evaluate_dependency_gate(
        add_scenario_violation=add_scenario_violation,
        dominated_scenarios=dominated_scenarios,
        gate_results=gate_results,
        observations=observations,
        policy=policy,
    )

    remove_all = [item for item in observations if item.family == "remove_all"]
    no_optical = [item for item in observations if item.family == "no_optical"]
    random_observations = [item for item in observations if item.family == "random"]
    competitor_wealth_floor, deployment_gate = _evaluate_removal_scenario_limits(
        add_scenario_violation=add_scenario_violation,
        baseline=baseline,
        no_optical=no_optical,
        policy=policy,
        random_observations=random_observations,
        remove_all=remove_all,
        scenario_thresholds=scenario_thresholds,
    )
    _evaluate_deployment_gates(
        observations=observations,
        case_by_name=case_by_name,
        industries=industries,
        deployment_gate=deployment_gate,
        add_scenario_violation=add_scenario_violation,
    )
    random_positive_fraction, random_p10_wealth, random_family_failures = _evaluate_random_gate(
        random_observations=random_observations,
        policy=policy,
        failures=failures,
        scenario_violations=scenario_violations,
    )

    diagnostics, high_optical_dependency, optical_share, pnl_shares, reference_deltas = (
        _summarize_generalization_gates(
            base=base,
            baseline=baseline,
            by_name=by_name,
            failures=failures,
            gate_results=gate_results,
            industries=industries,
            policy=policy,
        )
    )
    return _generalization_gate_report(
        failures=failures,
        baseline=baseline,
        evidence_as_of=evidence_as_of,
        evidence_eligible=evidence_eligible,
        evidence_ineligible=evidence_ineligible,
        observations=observations,
        aggregate=aggregate,
        reference_aggregate=reference_aggregate,
        gate_results=gate_results,
        stress=stress,
        families=families,
        dependency=dependency,
        pnl_shares=pnl_shares,
        optical_share=optical_share,
        high_optical_dependency=high_optical_dependency,
        diagnostics=diagnostics,
        competitor_wealth_floor=competitor_wealth_floor,
        random_family_failures=random_family_failures,
        random_positive_fraction=random_positive_fraction,
        random_p10_wealth=random_p10_wealth,
        deployment_gate=deployment_gate,
        reference_deltas=reference_deltas,
        scenario_violations=scenario_violations,
        scenario_thresholds=scenario_thresholds,
        case_by_name=case_by_name,
    )


aggregate_gate_results = _aggregate_gate_results
reference_aggregate = _reference_aggregate
relative_change = _relative_change
