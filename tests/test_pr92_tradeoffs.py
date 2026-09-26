"""Fixed-anchor relative budgets cannot waive retained native hard obligations."""
from types import SimpleNamespace

import pytest

from uquant.validation import pr92_tradeoffs as budget
from uquant.validation.generalization_policy.evaluation_stages import _record_valid_economics
from uquant.validation.generalization_reference import (
    load_generalization_baseline,
    load_generalization_policy,
)


def test_all_frozen_c3_accounts_compare_equal_without_final_acceptance():
    reference = budget.load_fixed_c3()
    assert len(reference) == 307
    assert len({row["request_id"] for row in reference.values()}) == 272
    for alias, row in reference.items():
        compared = budget.compare_c3_account(alias, row["metrics"], reference=reference)
        assert compared["classification"] == "unchanged"
        assert compared["per_account_budget_passed"]
        assert not compared["final_acceptance"]


@pytest.mark.parametrize(("ratio", "delta", "classification", "accepted"), (
    (.94, -.01, "controlled_exchange", False),
    (1.1, .02, "controlled_exchange", False),
    (.96, -.01, "controlled_exchange", True),
    (.99, 0., "pure_degradation", True),
))
def test_cumulative_budget_and_classification(ratio, delta, classification, accepted):
    reference = budget.load_fixed_c3()
    alias = "economic:continuous_ai_era/full"
    before = reference[alias]["metrics"]
    metrics = {"final_wealth": before["final_wealth_multiple"] * ratio,
               "max_drawdown": before["max_drawdown"] + delta}
    compared = budget.compare_c3_account(alias, metrics, reference=reference)
    assert compared["classification"] == classification
    assert compared["per_account_budget_passed"] is accepted


@pytest.mark.parametrize("value", (None, float("nan"), float("inf"), True, "1.0"))
def test_nonfinite_or_untyped_candidate_metrics_fail_closed(value):
    with pytest.raises(ValueError):
        budget.compare_c3_account("economic:continuous_ai_era/full",
                                  {"final_wealth": value, "max_drawdown": .1},
                                  reference=budget.load_fixed_c3())


def test_unknown_request_and_changed_contract_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="no fixed C3 reference"):
        budget.compare_c3_account("economic:unknown", {}, reference=budget.load_fixed_c3())
    path = tmp_path / "contract.json"
    path.write_bytes(budget.CONTRACT_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(budget, "CONTRACT_PATH", path)
    with pytest.raises(ValueError, match="differs from preregistration"):
        budget.load_fixed_c3()


def test_native_directional_floor_survives_relative_policy_replacement():
    baseline, policy = load_generalization_baseline(), load_generalization_policy()
    reference = budget.load_fixed_c3()
    identifier = "h1_2024/tradable-no-optical"
    anchor = reference[f"economic:{identifier}"]["metrics"]
    metrics = {**baseline.cells[identifier].metrics,
               "final_wealth": anchor["final_wealth_multiple"], "max_drawdown": anchor["max_drawdown"]}
    state = SimpleNamespace(economic_valid=0, failures=[], equality_differences=[],
                            c3_reference=reference, c3_comparisons=[], legacy_relative_failures=[],
                            intrinsic_results=[], policy=policy)
    _record_valid_economics(state, identifier, baseline.cells[identifier], metrics)
    assert state.c3_comparisons[0]["per_account_budget_passed"]
    assert any("intrinsic directional failed" in failure for failure in state.failures)
    assert state.intrinsic_results[0]["passed"] is False


def _matrix():
    return [{"alias": alias, "metrics": dict(row["metrics"]),
             "economic_source_sha256": "a" * 64, "raw_sha256": row["raw_sha256"]}
            for alias, row in budget.load_fixed_c3().items()]


def test_complete_native_membership_deduplicates_aliases_and_never_implies_final_acceptance():
    report = budget.evaluate_c3_matrix(_matrix())
    assert report["relative_budget_passed"]
    assert report["input_accounts"] == 307
    assert report["unique_accounts"] == 272
    assert report["aggregate"]["geometric_wealth_ratio"] == 1.
    assert not report["final_acceptance"]
    incomplete = budget.evaluate_c3_matrix(_matrix()[:1])
    assert not incomplete["relative_budget_passed"]
    assert incomplete["aggregate"] is None


def test_duplicate_neutral_accounts_cannot_dilute_degraded_families():
    reference = budget.load_fixed_c3()
    accounts = _matrix()
    for row in accounts:
        if reference[row["alias"]]["family"] in {"full", "stress"}:
            row["metrics"]["final_wealth_multiple"] *= .99
    neutral = next(row for row in accounts if reference[row["alias"]]["family"] == "random")
    report = budget.evaluate_c3_matrix(accounts + [neutral] * 100)
    assert report["unique_accounts"] == 272
    assert report["aggregate"]["pure_degradation_weight"] == pytest.approx(1 / 3)
    assert not report["relative_budget_passed"]


def test_conflicting_duplicate_and_missing_causal_evidence_are_rejected():
    accounts = _matrix()
    forged = {**accounts[0], "metrics": {**accounts[0]["metrics"], "max_drawdown": .01}}
    with pytest.raises(ValueError, match="aliases disagree"):
        budget.evaluate_c3_matrix([*accounts, forged])
    account = accounts[0]
    account["metrics"]["final_wealth_multiple"] *= 1.01
    report = budget.evaluate_c3_matrix([account])
    assert any("event evidence" in reason for reason in report["failures"])


def test_history_exception_is_bound_to_its_independent_fixed_audit(tmp_path, monkeypatch):
    basis = budget.historical_crowning_basis("remove-sz300502")
    assert basis["historical_two_owner_coverage"] == "INSUFFICIENT"
    assert budget.historical_crowning_basis("remove-sh600487") is None
    original = budget.CONTRACT_PATH.parent / "C3_OPPORTUNITY_AUDIT.json"
    altered = tmp_path / original.name
    altered.write_bytes(original.read_bytes() + b"\n")
    monkeypatch.setattr(budget, "CONTRACT_PATH", tmp_path / "CONTRACT_V1.json")
    with pytest.raises(ValueError, match="opportunity audit differs"):
        budget.historical_crowning_basis("remove-sz300502")


def test_budget_command_rejects_partial_membership_and_keeps_native_validation_separate(tmp_path):
    import json

    from uquant.validation.ci_artifacts import main

    accounts, output = tmp_path / "accounts.json", tmp_path / "budget.json"
    accounts.write_text(json.dumps({"accounts": _matrix()[:2]}))
    assert main(["c3-budget", "--accounts", str(accounts), "--report-output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["aggregate"] is None
    assert report["native_evidence_validation"] == "REQUIRED_SEPARATELY"
    assert report["final_acceptance"] is False
