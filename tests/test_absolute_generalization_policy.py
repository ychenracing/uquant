from __future__ import annotations

import copy
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
        validate_cell_artifact(raw, contract) for shard in successful_manifests() for raw in shard["cells"]
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
    report = aggregate_acceptance(successful_manifests(), load_absolute_generalization_contract())
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
        provenance=report.provenance,
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
    assert champion.evidence["relative_policy_reference"] is True
    assert "relative_generalization_non_regression" not in champion.evidence
    assert champion.evidence["report_13_runner_success"] is True


def test_champion_uses_compile_anchored_relative_policy_reference() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = dict(champion_manifest["champion"])
    reference = dict(champion["relative_policy_reference"])
    reference["frozen_artifact_sha256"] = "0" * 64
    champion["relative_policy_reference"] = reference
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: value for key, value in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    with pytest.raises(ValueError, match="relative policy reference"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_champion_rejects_legacy_native_eligibility_summary() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = dict(champion_manifest["champion"])
    grant = dict(champion["strategic_grant_acceptance"])
    grant["native_eligibility"] = [{"status": "PASS", "owner": "sz300308"}]
    champion["strategic_grant_acceptance"] = grant
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: value for key, value in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    with pytest.raises(ValueError, match="strategic grant acceptance"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_champion_summary_must_be_recomputed_from_raw_runtime_facts() -> None:
    for section, name, value in (
        ("metrics", "final_wealth", 24.0),
        ("metrics", "max_drawdown", 0.2),
        ("path_sha256", "equity", "9" * 64),
        ("report_13", "cash", 900_000.0),
    ):
        manifests = successful_manifests()
        champion_manifest = manifest(manifests, "champion")
        champion = dict(champion_manifest["champion"])
        summary = dict(champion[section])
        summary[name] = value
        champion[section] = summary
        champion["evidence_sha256"] = canonical_json_sha256(
            {key: item for key, item in champion.items() if key != "evidence_sha256"}
        )
        champion_manifest["champion"] = champion
        reseal_manifest(champion_manifest)

        with pytest.raises(ValueError, match=r"champion runtime|report-13 runtime"):
            aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_report_runtime_binds_decisions_to_daily_observation_sessions() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = copy.deepcopy(champion_manifest["champion"])
    ownership = champion["strategic_ownership_acceptance"]
    report = ownership["report_13"]
    trace = report["decision_trace"]
    trace[0]["date"] = trace[1]["date"]
    report["trace_sha256"] = canonical_json_sha256(trace)
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: item for key, item in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    with pytest.raises(ValueError, match="report decision session differs"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_report_capital_authority_uses_contemporaneous_risk_caps() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    report = champion_manifest["champion"]["report_13"]
    assert report["maximum_target_gross"] == 0.95
    assert report["minimum_risk_target_gross_cap"] == 0.0

    result = aggregate_acceptance(manifests, load_absolute_generalization_contract())

    assert result.components[0].name == "champion_non_regression"
    assert result.components[0].passed is True


def test_report_capital_authority_rejects_same_session_cap_expansion() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = copy.deepcopy(champion_manifest["champion"])
    report = champion["strategic_ownership_acceptance"]["report_13"]
    trace = report["decision_trace"]
    positive_target = next(row for row in trace if row["target_gross"] > 0.0)
    positive_target["risk"]["target_gross_cap"] = positive_target["target_gross"] - 0.01
    report["trace_sha256"] = canonical_json_sha256(trace)
    champion["report_13"]["minimum_risk_target_gross_cap"] = min(
        row["risk"]["target_gross_cap"] for row in trace
    )
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: item for key, item in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    with pytest.raises(ValueError, match="capital authority"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_report_rejects_reference_only_capital_authority() -> None:
    manifests = successful_manifests()
    champion_manifest = manifest(manifests, "champion")
    champion = copy.deepcopy(champion_manifest["champion"])
    report = champion["strategic_ownership_acceptance"]["report_13"]
    trace = report["decision_trace"]
    positive_target = next(row for row in trace if row["target_gross"] > 0.0)
    positive_target["targets"][0]["symbol"] = "sh600487"
    report["trace_sha256"] = canonical_json_sha256(trace)
    champion["evidence_sha256"] = canonical_json_sha256(
        {key: item for key, item in champion.items() if key != "evidence_sha256"}
    )
    champion_manifest["champion"] = champion
    reseal_manifest(champion_manifest)

    with pytest.raises(ValueError, match="reference-only symbol"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


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
def test_champion_policy_rejects_unbound_summary_claims(
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

    del failure
    with pytest.raises(ValueError, match="champion runtime"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


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
        transitions = list(facts["transitions"])
        transitions.append(copy.deepcopy(transitions[-1]))
        facts["transitions"] = transitions
    elif payload == "terminal_scc":
        transitions = list(facts["transitions"])
        last = dict(transitions[-1])
        last["session"] = "2023-04-30"
        transitions.append(last)
        facts["transitions"] = transitions
    else:
        chains = [copy.deepcopy(item) for item in facts["chains"]]
        chains[1]["epoch"]["owner_symbol"] = "sh601869"
        facts["chains"] = chains
    recovery_manifest[payload] = facts
    reseal_manifest(recovery_manifest)

    del component, failure
    with pytest.raises(ValueError):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_all_four_repair_bounds_are_literal_and_off_by_one_closed() -> None:
    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    repairs = [dict(item) for item in recovery_manifest["repair_bounds"]]
    observations = [dict(item) for item in repairs[1]["observations"]]
    observations.append(copy.deepcopy(observations[-1]))
    repairs[1]["observations"] = observations
    recovery_manifest["repair_bounds"] = repairs
    reseal_manifest(recovery_manifest)

    with pytest.raises(ValueError, match="repair"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_repeated_crowning_requires_two_fill_gated_epochs_and_two_owners() -> None:
    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    historical = dict(recovery_manifest["historical_crowning"])
    chains = [copy.deepcopy(item) for item in historical["chains"]]
    chains[1]["epoch"]["owner_symbol"] = "sh600487"
    historical["chains"] = chains
    recovery_manifest["historical_crowning"] = historical
    reseal_manifest(recovery_manifest)

    with pytest.raises(ValueError, match="crowning"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_historical_crowning_rejects_a_different_complete_source_cell() -> None:
    """A valid recovery chain must come from its named Task 5 cell epochs."""

    manifests = successful_manifests()
    recovery_manifest = manifest(manifests, "recovery-and-reachability")
    historical = dict(recovery_manifest["historical_crowning"])
    historical["source_cell_id"] = "remove-sh600487"
    recovery_manifest["historical_crowning"] = historical
    reseal_manifest(recovery_manifest)

    report = aggregate_acceptance(manifests, load_absolute_generalization_contract())
    repeated = next(component for component in report.components if component.name == "repeated_crowning")

    assert repeated.passed is False
    assert "historical crowning source epochs differ" in repeated.failures
