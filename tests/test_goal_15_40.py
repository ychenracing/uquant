"""Latest explicit user bounds, distinct from original frozen judgments."""
import json

from research.cross_ai_acceptance import CONTRACT_PATH, check_metrics
from research.cross_ai_robustness import metric_failures
from uquant.validation.absolute_generalization._acceptance_evidence import _candidate_metric_violations
from uquant.validation.promotion import AI_ERA_POLICY, _hard_violations


def test_principal_15_is_hard_and_40_is_inclusive():
    t = json.loads(CONTRACT_PATH.read_text())["thresholds"]
    for case in ("full", "champion"):
        args = dict(case=case, window="continuous_ai_era", baseline={}, benchmark={}, thresholds=t)
        metrics = {"final_wealth": 15., "max_drawdown": .25, "account_orders": 40}
        assert check_metrics(metrics=metrics, **args) == []
        assert "champion/full wealth floor" in check_metrics(metrics={**metrics, "final_wealth": 14.999999}, **args)
        assert "champion/full order ceiling" in check_metrics(metrics={**metrics, "account_orders": 41}, **args)
        assert len(check_metrics(metrics=metrics, authorized=False, **args)) == 2


def test_performance_cannot_discount_15_to_13_5():
    gate = AI_ERA_POLICY["official"]["continuous_ai_era"]
    m = {"final_wealth": 15., "max_drawdown": .2, "account_orders": 40, "acute_return": 0.}
    assert not _hard_violations(name="e/continuous_ai_era", metrics=m, gate=gate)
    assert _hard_violations(name="e/continuous_ai_era", metrics={**m, "final_wealth": 14.99}, gate=gate)
    assert _hard_violations(name="e/continuous_ai_era", metrics={**m, "max_drawdown": .31}, gate=gate)


def test_absolute_current_champion_uses_same_user_bounds():
    contract = json.loads(CONTRACT_PATH.read_text())
    claims = {"incumbent_epoch_count": 1, "duplicate_grant_count": 0, "duplicate_order_count": 0, "duplicate_epoch_count": 0}
    m = {"final_wealth": 15., "max_drawdown": .25, "account_orders": 40}
    assert not _candidate_metric_violations(contract=contract, claims=claims, metrics=m)
    assert _candidate_metric_violations(contract=contract, claims=claims, metrics={**m, "final_wealth": 14.99})
    assert _candidate_metric_violations(contract=contract, claims=claims, metrics={**m, "account_orders": 41})


def test_removal_wealth_is_not_replaced_by_principal_15():
    t = json.loads(CONTRACT_PATH.read_text())["thresholds"]
    m = {"final_wealth": 2., "max_drawdown": .2, "account_orders": 40, "annual_turnover": 1., "fees": 100., "slippage_cost": 100.}
    args = dict(case="remove_all_three", window="continuous_ai_era", baseline={"final_wealth": 1.}, benchmark={"final_wealth": 1.}, thresholds=t)
    assert not check_metrics(metrics=m, **args)
    assert "removal order ceiling" in check_metrics(metrics={**m, "account_orders": 41}, **args)


def test_best_contributor_removal_keeps_its_separate_frozen_floor():
    t = json.loads(CONTRACT_PATH.read_text())["thresholds"]
    m = {"final_wealth": 1.05, "max_drawdown": .2, "account_orders": 40}
    args = dict(spec={"case": "champion", "group": "best_contributor_removal"},
                nominal={"final_wealth": 25.}, paired={"final_wealth": 1.}, thresholds=t, cash=2_000_000.)
    assert not metric_failures(metrics=m, **args)
    assert "best contributor removal wealth floor" in metric_failures(metrics={**m, "final_wealth": .9}, **args)
    assert "absolute order ceiling" in metric_failures(metrics={**m, "account_orders": 41}, **args)


def test_principal_nominal_still_has_the_hard_15_floor():
    t = json.loads(CONTRACT_PATH.read_text())["thresholds"]
    m = {"final_wealth": 14.99, "max_drawdown": .2, "account_orders": 40}
    failures = metric_failures(spec={"case": "champion", "group": "nominal"},
                               metrics=m, nominal={"final_wealth": 16.}, paired=None, thresholds=t, cash=2_000_000.)
    assert "champion absolute wealth floor" in failures


def test_stress_cases_retain_their_own_frozen_relative_requirements():
    t = json.loads(CONTRACT_PATH.read_text())["thresholds"]
    m = {"final_wealth": 3.63, "max_drawdown": .2, "account_orders": 40}
    args = dict(spec={"case": "champion", "group": "paired_initial_conditions"},
                metrics=m, nominal={"final_wealth": 25.}, thresholds=t, cash=2_000_000.)
    assert not metric_failures(paired={"final_wealth": 4.}, **args)
    assert metric_failures(paired={"final_wealth": 5.}, **args) == ["paired initial wealth retention"]
    args.update(spec={"case": "champion", "group": "parameter_neighbors"})
    assert metric_failures(paired=None, **args) == ["neighbor wealth retention"]
