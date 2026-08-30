from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from _absolute_generalization_acceptance_fixture import (
    manifest,
    reseal_manifest,
    successful_manifests,
)

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.validation.absolute_generalization import (
    AcceptanceReport,
    ComponentResult,
    aggregate_acceptance,
    load_absolute_generalization_contract,
    validate_cell_artifact,
)
from uquant.validation.absolute_generalization.policy import (
    evaluate_absolute_strategic_robustness,
    evaluate_complete_literal_metrics,
    evaluate_witness_resilience,
)


def _validated_cells():
    contract = load_absolute_generalization_contract()
    return tuple(
        validate_cell_artifact(raw, contract)
        for shard in successful_manifests()
        for raw in shard["cells"]
    )


def test_component_results_are_literal_immutable_facts() -> None:
    result = ComponentResult(
        name="champion_non_regression",
        passed=True,
        failures=(),
        evidence_sha256=canonical_json_sha256({}),
    )

    assert result.name == "champion_non_regression"
    assert result.passed is True
    with pytest.raises(FrozenInstanceError):
        result.passed = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="component result"):
        ComponentResult(
            name=result.name,
            passed=True,
            failures=("failure",),
            evidence_sha256=result.evidence_sha256,
        )
    with pytest.raises(ValueError, match="component result"):
        replace(result, evidence_sha256="a" * 64)


def test_public_acceptance_report_cannot_exist_unsealed() -> None:
    report = aggregate_acceptance(
        successful_manifests(), load_absolute_generalization_contract()
    )
    rebuilt = AcceptanceReport(
        schema_version=report.schema_version,
        runner_success=report.runner_success,
        capability_pass=report.capability_pass,
        passed=report.passed,
        runner_failures=report.runner_failures,
        components=report.components,
        expected_cells=report.expected_cells,
        valid_cells=report.valid_cells,
        replay_error_cells=report.replay_error_cells,
        missing_cells=report.missing_cells,
        duplicate_cells=report.duplicate_cells,
        complete_metric_cells=report.complete_metric_cells,
        statistics=report.statistics,
        canonical_sha256="",
    )

    assert rebuilt.canonical_sha256 == report.canonical_sha256
    with pytest.raises(ValueError, match="report seal"):
        replace(rebuilt, canonical_sha256="0" * 64)


def test_policy_emits_exactly_seven_fixed_components_in_contract_order() -> None:
    contract = load_absolute_generalization_contract()
    report = aggregate_acceptance(successful_manifests(), contract)

    assert tuple(component.name for component in report.components) == contract.components
    assert all(component.passed for component in report.components)
    assert len({component.evidence_sha256 for component in report.components}) == 7
    champion = report.components[0]
    assert champion.evidence["strategic_grant_acceptance"] is True
    assert champion.evidence["strategic_ownership_acceptance"] is True
    assert champion.evidence["relative_generalization_non_regression"] is True
    assert champion.evidence["report_13_runner_success"] is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "value", "failure"),
    (
        (("metrics", "final_wealth"), 23.284178712755819, "wealth"),
        (("metrics", "max_drawdown"), 0.30000000000000004, "drawdown"),
        (("path_sha256", "equity"), "9" * 64, "path"),
        (("duplicate_grant_count",), 1, "duplicate grant"),
        (("successor_capital_before_incumbent_exit_count",), 1, "lifecycle"),
    ),
)
def test_champion_policy_uses_frozen_floor_drawdown_path_and_lifecycle(
    path: tuple[str, ...], value: object, failure: str
) -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = dict(champion_manifest["champion"])
    if len(path) == 1:
        champion[path[0]] = value
    else:
        nested = dict(champion[path[0]])
        nested[path[1]] = value
        champion[path[0]] = nested
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: item for key, item in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    components = {
        item.name: item
        for item in aggregate_acceptance(
            manifests, load_absolute_generalization_contract()
        ).components
    }
    assert components["champion_non_regression"].passed is False
    assert any(failure in item for item in components["champion_non_regression"].failures)


def test_absolute_policy_keeps_all_cells_and_uses_strict_positive_wealth() -> None:
    contract = load_absolute_generalization_contract()
    cells = _validated_cells()
    boundary = cells[0]
    assert boundary.metrics is not None
    changed = replace(
        boundary,
        metrics=replace(boundary.metrics, final_wealth=1.0, total_return=0.0),
    )

    result = evaluate_absolute_strategic_robustness((changed, *cells[1:]), contract)

    assert result.passed is True
    assert result.evidence["cell_count"] == 34
    assert result.evidence["positive_return_count"] == 33
    assert result.evidence["positive_return_fraction"] == pytest.approx(33 / 34)


def test_critical_removal_literal_gates_do_not_use_baseline_relaxation() -> None:
    contract = load_absolute_generalization_contract()
    cells = list(_validated_cells())
    index = next(i for i, item in enumerate(cells) if item.removed_symbol == "sz300308")
    critical = cells[index]
    assert critical.metrics is not None
    cells[index] = replace(
        critical,
        metrics=replace(critical.metrics, final_wealth=1.0, total_return=0.0),
    )

    result = evaluate_absolute_strategic_robustness(tuple(cells), contract)

    assert result.passed is False
    assert any("critical sz300308 wealth" in item for item in result.failures)


def test_witness_policy_uses_the_fixed_five_cell_denominator() -> None:
    contract = load_absolute_generalization_contract()
    cells = list(_validated_cells())
    index = next(i for i, item in enumerate(cells) if item.removed_symbol == "sh603688")
    witness = cells[index]
    assert witness.metrics is not None
    cells[index] = replace(
        witness,
        metrics=replace(witness.metrics, final_wealth=1.0, total_return=0.0),
    )

    result = evaluate_witness_resilience(tuple(cells), contract)

    assert result.passed is False
    assert result.evidence["numerator"] == 4
    assert result.evidence["denominator"] == 5
    assert result.evidence["fraction"] == 0.8


def test_complete_literal_metrics_is_independent_of_economic_thresholds() -> None:
    contract = load_absolute_generalization_contract()
    result = evaluate_complete_literal_metrics(_validated_cells(), contract)

    assert result.passed is True
    assert result.evidence == {
        "expected_cells": 34,
        "complete_metric_cells": 34,
        "accounting_reconciled_cells": 34,
        "attribution_reconciled_cells": 34,
        "intervention_free_cells": 34,
    }


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("payload", "component", "failure"),
    (
        (
            "failed_grant_recovery",
            "failed_grant_recovery",
            "20",
        ),
        (
            "terminal_scc",
            "bounded_healthy_cash_vacancy",
            "terminal SCC",
        ),
        (
            "cross_industry_crowning",
            "repeated_crowning",
            "cross-industry",
        ),
    ),
)
def test_recovery_scc_and_cross_industry_literals_are_independent(
    payload: str,
    component: str,
    failure: str,
) -> None:
    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    facts = dict(recovery_manifest[payload])
    if payload == "failed_grant_recovery":
        observations = list(facts["observations"])
        predicates = [
            {"code": "FLAT_ALL_CASH", "satisfied": True},
            {"code": "REFERENCE_AVAILABLE", "satisfied": True},
            {"code": "QUALIFICATION_OPPORTUNITY", "satisfied": True},
        ]
        observations.append(
            {
                "session": "2023-01-31",
                "phase": "POST_DECISION",
                "edge_kind": "OBSERVED",
                "state_sha256": canonical_json_sha256(
                    {
                        "session": "2023-01-31",
                        "phase": "POST_DECISION",
                        "predicate_results": predicates,
                    }
                ),
                "predicate_results": predicates,
            }
        )
        facts["observations"] = observations
    elif payload == "terminal_scc":
        transitions = list(facts["transitions"])
        last = dict(transitions[-1])
        last["session"] = "2023-04-30"
        transitions.append(last)
        facts["transitions"] = transitions
    else:
        epochs = [dict(item) for item in facts["epochs"]]
        epochs[1]["owner_symbol"] = "sh601869"
        facts["epochs"] = epochs
    recovery_manifest[payload] = facts
    reseal_manifest(recovery_manifest)

    components = {
        item.name: item
        for item in aggregate_acceptance(
            manifests, load_absolute_generalization_contract()
        ).components
    }
    assert components[component].passed is False
    assert any(failure in item for item in components[component].failures)


def test_all_four_repair_bounds_are_literal_and_off_by_one_closed() -> None:
    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    repairs = [dict(item) for item in recovery_manifest["repair_bounds"]]
    observations = [dict(item) for item in repairs[1]["observations"]]
    previous = observations[-1]
    previous["repair_status"] = "ACCUMULATING"
    previous["state_sha256"] = canonical_json_sha256(
        {
            "session": previous["session"],
            "repair_status": previous["repair_status"],
            "predicate_results": previous["predicate_results"],
            "persisted_damage_level": 2,
            "target_budget_level": 1,
        }
    )
    final = dict(previous)
    final["session"] = "2023-05-11"
    final["repair_status"] = "READY"
    final["state_sha256"] = canonical_json_sha256(
        {
            "session": final["session"],
            "repair_status": final["repair_status"],
            "predicate_results": final["predicate_results"],
            "persisted_damage_level": 2,
            "target_budget_level": 1,
        }
    )
    observations.append(final)
    repairs[1]["observations"] = observations
    recovery_manifest["repair_bounds"] = repairs
    reseal_manifest(recovery_manifest)

    component = {
        item.name: item
        for item in aggregate_acceptance(
            manifests, load_absolute_generalization_contract()
        ).components
    }["bounded_healthy_cash_vacancy"]

    assert component.passed is False
    assert any("2->1/40" in item for item in component.failures)


def test_repeated_crowning_requires_two_fill_gated_epochs_and_two_owners() -> None:
    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    historical = dict(recovery_manifest["historical_crowning"])
    epochs = [dict(item) for item in historical["epochs"]]
    epochs[1]["owner_symbol"] = "sh600487"
    historical["epochs"] = epochs
    recovery_manifest["historical_crowning"] = historical
    reseal_manifest(recovery_manifest)

    component = {
        item.name: item
        for item in aggregate_acceptance(
            manifests, load_absolute_generalization_contract()
        ).components
    }["repeated_crowning"]

    assert component.passed is False
    assert any("distinct owners" in item for item in component.failures)
