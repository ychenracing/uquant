from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from _absolute_generalization_acceptance_fixture import (
    _cell_raw,
    cell,
    checkout_identity,
    manifest,
    replay_error_cell,
    reseal_cell,
    reseal_manifest,
    successful_manifests,
)

from uquant.validation.absolute_generalization import (
    AcceptanceReport,
    aggregate_acceptance,
    build_error_shard_manifest,
    build_leave_one_out_scenarios,
    load_absolute_generalization_contract,
    seal_shard_manifest,
    validate_cell_artifact,
    validate_shard_manifest,
)
from uquant.validation.absolute_generalization.policy import evaluate_witness_resilience


def test_complete_static_manifests_recompute_one_exact_green_conjunction() -> None:
    contract = load_absolute_generalization_contract()
    report = aggregate_acceptance(successful_manifests(), contract)

    assert isinstance(report, AcceptanceReport)
    assert report.runner_success is True
    assert report.capability_pass is True
    assert report.passed is True
    assert report.expected_cells == 34
    assert report.valid_cells == 34
    assert report.replay_error_cells == 0
    assert report.missing_cells == 0
    assert report.duplicate_cells == 0
    assert report.complete_metric_cells == 34
    assert dict(report.statistics) == {
        "accounting_reconciliation_fraction": 1.0,
        "actual_strategic_epoch_cells": 34.0,
        "intervention_free_fraction": 1.0,
        "p10_final_wealth": 1.1,
        "p90_healthy_zero_total_target_streak": 1.0,
        "p90_max_drawdown": 0.0,
        "positive_return_fraction": 1.0,
        "positive_strategic_target_cells": 34.0,
        "witness_missing_recovery_fraction": 1.0,
        "worst_healthy_zero_total_target_streak": 1.0,
    }
    raw = report.to_dict()
    assert raw["passed"] is True
    assert raw["canonical_sha256"] == report.canonical_sha256
    with pytest.raises(FrozenInstanceError):
        report.passed = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="conjunction"):
        replace(report, passed=False)


def test_aggregation_accepts_a_single_pass_manifest_iterable() -> None:
    contract = load_absolute_generalization_contract()

    report = aggregate_acceptance(iter(successful_manifests()), contract)

    assert report.runner_success is True
    assert report.capability_pass is True
    assert report.passed is True


def test_witness_resilience_accepts_causal_unavailable_reference_sessions() -> None:
    contract = load_absolute_generalization_contract()
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(contract)
        if item.removed_symbol == contract.required_witnesses[0]
    )
    base = validate_cell_artifact(_cell_raw(scenario), contract)
    assert base.metrics is not None
    cells = tuple(
        replace(
            base,
            cell_id=f"remove-{symbol}",
            removed_symbol=symbol,
            metrics=replace(
                base.metrics,
                intentional_role_absent_symbols=(symbol,),
                expected_but_unavailable_symbols=("prelisting-reference",),
                qualification_coverage=0.98,
                risk_coverage=0.98,
            ),
        )
        for symbol in contract.required_witnesses
    )

    result = evaluate_witness_resilience(cells, contract)

    assert result.passed is True
    assert result.failures == ()
    assert result.evidence["fraction"] == 1.0


def test_strict_error_manifest_encodes_upstream_failure_in_the_report() -> None:
    contract = load_absolute_generalization_contract()
    manifests = successful_manifests()
    failed = manifest(manifests, "loo-a")
    replacement = build_error_shard_manifest(
        shard="loo-a",
        mode="canonical",
        error="trusted workflow result: failure",
        run_id=failed["run_id"],
        run_attempt=failed["run_attempt"],
        head=failed["head"],
        tree=failed["tree"],
        contract=contract,
    )
    manifests[manifests.index(failed)] = replacement

    report = aggregate_acceptance(manifests, contract)

    assert report.runner_success is False
    assert report.capability_pass is False
    assert report.passed is False
    assert report.missing_cells == 6
    assert report.valid_cells == 28
    assert report.statistics == ()
    assert any("loo-a upstream failure" in item for item in report.runner_failures)


def test_error_manifest_is_the_only_non_self_asserted_upstream_failure_path() -> None:
    contract = load_absolute_generalization_contract()
    manifests = successful_manifests()
    failed = manifest(manifests, "loo-a")
    failed["upstream_success"] = False
    reseal_manifest(failed)

    with pytest.raises(ValueError, match="status/upstream"):
        aggregate_acceptance(manifests, contract)


def test_workflow_upstream_gate_is_downgrade_only_and_preserves_capability() -> None:
    contract = load_absolute_generalization_contract()
    manifests = successful_manifests()

    report = aggregate_acceptance(
        manifests,
        contract,
        upstream_success=False,
        upstream_failure_codes=("matrix-result=failure",),
    )

    assert report.runner_success is False
    assert report.capability_pass is True
    assert report.passed is False
    assert report.statistics == ()
    assert report.runner_failures == (
        "workflow upstream failure: matrix-result=failure",
    )


@pytest.mark.parametrize("upstream_success", (1, "true", None))  # type: ignore[untyped-decorator]
def test_workflow_upstream_gate_requires_an_exact_boolean(upstream_success: object) -> None:
    with pytest.raises(ValueError, match="upstream result"):
        aggregate_acceptance(
            successful_manifests(),
            load_absolute_generalization_contract(),
            upstream_success=upstream_success,  # type: ignore[arg-type]
            upstream_failure_codes=("matrix-result=failure",),
        )


def test_upstream_true_cannot_override_a_strict_error_manifest() -> None:
    contract = load_absolute_generalization_contract()
    manifests = successful_manifests()
    failed = manifest(manifests, "loo-a")
    manifests[manifests.index(failed)] = build_error_shard_manifest(
        shard="loo-a",
        mode="canonical",
        error="shard failure",
        run_id=failed["run_id"],
        run_attempt=failed["run_attempt"],
        head=failed["head"],
        tree=failed["tree"],
        contract=contract,
    )

    report = aggregate_acceptance(manifests, contract, upstream_success=True)

    assert report.runner_success is False
    assert report.passed is False


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("mutation", "message"),
    (
        ("missing_shard", "missing shard"),
        ("duplicate_shard", "duplicate shard"),
        ("unexpected_shard", "unexpected shard"),
        ("missing_cell", "canonical cell coverage"),
        ("duplicate_cell", "duplicate cell"),
        ("unexpected_cell", "artifact cell identity"),
    ),
)
def test_static_shard_and_cell_coverage_fails_closed(mutation: str, message: str) -> None:
    manifests = successful_manifests()
    if mutation == "missing_shard":
        manifests.pop(1)
    elif mutation == "duplicate_shard":
        manifests.append(manifests[1])
    elif mutation == "unexpected_shard":
        manifests[1]["shard"] = "loo-z"
        reseal_manifest(manifests[1])
    else:
        shard = manifest(manifests, "loo-a")
        cells = list(shard["cells"])
        if mutation == "missing_cell":
            cells.pop()
        elif mutation == "duplicate_cell":
            cells.append(cells[0])
        else:
            cells[0] = dict(cells[0])
            cells[0]["cell_id"] = "remove-not-canonical"
            reseal_cell(cells[0])
        shard["cells"] = cells
        shard["summary"] = {
            "cell_count": len(cells),
            "complete_cell_count": len(cells),
            "replay_error_cell_count": 0,
        }
        reseal_manifest(shard)

    with pytest.raises(ValueError, match=message):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("mutation", "message"),
    (
        ("manifest_seal", "manifest seal"),
        ("summary", "summary"),
        ("head", "current checkout"),
        ("source", "production source"),
        ("run_id", "run identity"),
        ("targeted", "canonical mode"),
        ("self_pass", "self-asserted pass"),
        ("cache_pass", "self-asserted pass"),
    ),
)
def test_manifest_provenance_summary_mode_and_pass_claims_fail_closed(
    mutation: str, message: str
) -> None:
    manifests = successful_manifests()
    shard = manifest(manifests, "loo-a")
    if mutation == "manifest_seal":
        shard["canonical_sha256"] = "0" * 64
    elif mutation == "summary":
        shard["summary"] = {**dict(shard["summary"]), "complete_cell_count": 0}
        reseal_manifest(shard)
    elif mutation == "head":
        shard["head"] = "0" * 40
        reseal_manifest(shard)
    elif mutation == "source":
        shard["production_source_sha256"] = "0" * 64
        reseal_manifest(shard)
    elif mutation == "run_id":
        shard["run_id"] = "different-run"
        reseal_manifest(shard)
    elif mutation == "targeted":
        shard["mode"] = "targeted"
        reseal_manifest(shard)
    elif mutation == "self_pass":
        shard["passed"] = True
        reseal_manifest(shard)
    else:
        shard["cache"] = {"passed": True}
        reseal_manifest(shard)

    with pytest.raises(ValueError, match=message):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_every_raw_cell_is_revalidated_instead_of_trusting_metrics_or_seal() -> None:
    manifests = successful_manifests()
    raw = cell(manifests, "sz300308")
    metrics = dict(raw["metrics"])
    metrics["final_wealth"] = 9.0
    raw["metrics"] = metrics
    reseal_cell(raw)
    reseal_manifest(manifest(manifests, "loo-f"))

    with pytest.raises(ValueError, match=r"metric identity|derived metrics"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_metric_free_replay_error_is_retained_and_cannot_go_green() -> None:
    manifests = successful_manifests()
    shard = manifest(manifests, "loo-f")
    cells = list(shard["cells"])
    index = next(i for i, item in enumerate(cells) if item["removed_symbol"] == "sz300308")
    cells[index] = replay_error_cell()
    shard["cells"] = cells
    shard["summary"] = {
        "cell_count": len(cells),
        "complete_cell_count": len(cells) - 1,
        "replay_error_cell_count": 1,
    }
    reseal_manifest(shard)

    report = aggregate_acceptance(manifests, load_absolute_generalization_contract())

    assert report.runner_success is False
    assert report.capability_pass is False
    assert report.passed is False
    assert report.replay_error_cells == 1
    assert report.statistics == ()


def test_public_manifest_seal_validation_and_error_builder_share_one_schema() -> None:
    contract = load_absolute_generalization_contract()
    head, tree = checkout_identity()
    error = build_error_shard_manifest(
        shard="loo-a",
        mode="canonical",
        error="upstream cancelled",
        run_id="run-1",
        run_attempt=2,
        head=head,
        tree=tree,
        contract=contract,
    )

    assert validate_shard_manifest(error, contract).to_dict() == error
    unsealed = dict(error)
    unsealed.pop("canonical_sha256")
    assert seal_shard_manifest(unsealed, contract) == error


def test_manifest_rejects_raw_analyzer_pass_and_hand_reconciliation_claims() -> None:
    manifests = successful_manifests()
    recovery = manifest(manifests, "recovery-and-reachability")
    facts = dict(recovery["failed_grant_recovery"])
    facts["passed"] = True
    facts["outlet_reconciled"] = True
    recovery["failed_grant_recovery"] = facts
    reseal_manifest(recovery)

    with pytest.raises(ValueError, match="self-asserted pass"):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


@pytest.mark.parametrize(
    "mutation",
    (
        "failed_grant_session",
        "failed_grant_digest",
        "empty_scc",
        "crowning_source",
        "repair_session",
        "champion_evidence",
    ),
)
def test_special_shard_recomputes_strict_raw_literal_facts(mutation: str) -> None:
    manifests = successful_manifests()
    recovery = manifest(manifests, "recovery-and-reachability")
    if mutation == "failed_grant_session":
        recovery["failed_grant_recovery"]["transitions"][0]["session"] = "not-a-session"
    elif mutation == "failed_grant_digest":
        recovery["failed_grant_recovery"]["transitions"][0]["runtime_state"][
            "capital_budget_level"
        ] = 4
    elif mutation == "empty_scc":
        recovery["terminal_scc"]["transitions"] = []
    elif mutation == "crowning_source":
        recovery["historical_crowning"]["source_cell_id"] = "not-a-canonical-cell"
    elif mutation == "champion_evidence":
        champion = manifest(manifests, "champion")
        champion["champion"]["evidence_sha256"] = "0" * 64
        reseal_manifest(champion)
    else:
        recovery["repair_bounds"][0]["observations"][-1]["session"] = "not-a-session"
    reseal_manifest(recovery)

    with pytest.raises(
        ValueError,
        match=r"evidence|session|transition|state|SCC|crowning|repair",
    ):
        aggregate_acceptance(manifests, load_absolute_generalization_contract())


def test_report_statistics_are_derived_cell_counts_not_complete_placeholders() -> None:
    report = aggregate_acceptance(
        successful_manifests(), load_absolute_generalization_contract()
    )
    statistics = dict(report.statistics)
    robustness = next(
        item for item in report.components if item.name == "absolute_strategic_robustness"
    )

    assert statistics["positive_strategic_target_cells"] == float(
        robustness.evidence["positive_strategic_target_cells"]
    )
    assert statistics["actual_strategic_epoch_cells"] == float(
        robustness.evidence["actual_strategic_epoch_cells"]
    )
