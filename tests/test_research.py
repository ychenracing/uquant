from __future__ import annotations

import inspect
import random
import sys
from pathlib import Path

import pandas as pd
import pytest

# The project distribution intentionally packages only ``uquant*``. Research
# remains a repository-local harness, so make that isolation explicit in tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.ablation import build_ablations, compare_ablations
from research.candidate_search import (
    CandidateEvaluation,
    GateMateriality,
    ObjectiveWeights,
    ReplayObservation,
    dominance_gate,
    enumerate_candidates,
    evaluate_candidate,
    pareto_gate,
    search_candidates,
    validate_shared_config,
)
from research.parameter_stress import (
    factorial_perturbations,
    one_at_a_time_perturbations,
)
from research.trade_attribution import (
    ExitRecord,
    aggregate_by_reason,
    attribute_exits,
)
from research.universe_stress import (
    balanced_industry_case,
    exclude_industry_case,
    industry_only_cases,
    leave_top_k_out_cases,
    random_universe_cases,
    remove_core_cases,
)


def _observation(
    *,
    universe: str = "a",
    scenario: str = "bull",
    wealth: float = 2.0,
    drawdown: float = 0.20,
    turnover: float = 1.0,
    orders: int = 10,
    urgent_return: float = -0.05,
    pdi: float = 0.10,
) -> ReplayObservation:
    return ReplayObservation(
        universe=universe,
        scenario=scenario,
        final_wealth=wealth,
        max_drawdown=drawdown,
        annual_turnover=turnover,
        account_orders=orders,
        urgent_return=urgent_return,
        prior_dependence=pdi,
    )


def _evaluation(
    *,
    wealth: float,
    drawdown: float,
    orders: int,
) -> CandidateEvaluation:
    return evaluate_candidate(
        {"threshold": 0.5},
        [_observation(wealth=wealth, drawdown=drawdown, orders=orders)],
    )


def test_candidate_enumeration_is_deterministic_and_forbids_pool_profiles() -> None:
    first = enumerate_candidates(
        {"zeta": [2, 1, 2], "alpha": [0.2, 0.1]},
        base={"enabled": True},
        pool_names=("a", "b"),
    )
    second = enumerate_candidates(
        {"alpha": [0.1, 0.2], "zeta": [1, 2]},
        base={"enabled": True},
        pool_names=("b", "a"),
    )
    assert first == second
    assert len(first) == 4
    assert all(tuple(candidate) == tuple(sorted(candidate)) for candidate in first)

    with pytest.raises(ValueError, match="per-pool"):
        validate_shared_config({"profiles": "a-specific"})
    with pytest.raises(ValueError, match="per-pool"):
        validate_shared_config({"a": 0.9}, pool_names=("a", "b"))
    with pytest.raises(ValueError, match="scalar"):
        validate_shared_config({"thresholds": {"a": 0.9}})  # type: ignore[dict-item]


def test_search_uses_one_config_across_every_matrix_cell_and_full_objective() -> None:
    calls: list[tuple[tuple[tuple[str, object], ...], str, str]] = []

    def runner(config: dict[str, object], pool: str, scenario: str) -> ReplayObservation:
        calls.append((tuple(sorted(config.items())), pool, scenario))
        return _observation(
            universe=pool,
            scenario=scenario,
            wealth=1.5 + float(config["edge"]),
            drawdown=0.10,
            turnover=1.0,
            orders=5,
            urgent_return=-0.02,
            pdi=0.05,
        )

    results = search_candidates(
        parameter_grid={"edge": [0.1, 0.2]},
        pools=("b", "a"),
        scenarios=("bear", "bull"),
        runner=runner,  # type: ignore[arg-type]
        weights=ObjectiveWeights(
            calmar=0.0,
            crash_protection=0.0,
            max_drawdown=1.0,
            turnover=0.1,
            prior_dependence=1.0,
            universe_variance=1.0,
        ),
    )

    assert len(results) == 2
    assert len(calls) == 8
    assert all(result.dominance_passed is None for result in results)
    assert all(result.pareto_passed is None for result in results)
    assert not any(result.accepted for result in results)
    for config in {call[0] for call in calls}:
        assert {(pool, scenario) for seen, pool, scenario in calls if seen == config} == {
            ("a", "bear"),
            ("a", "bull"),
            ("b", "bear"),
            ("b", "bull"),
        }
    best = results[0].evaluation
    assert best.config()["edge"] == 0.2
    assert best.score == pytest.approx(
        best.median_log_wealth
        - best.worst_drawdown
        - 0.1 * best.median_turnover
        - best.prior_dependence
        - best.universe_variance
    )

    scenario_shift_only = evaluate_candidate(
        {"edge": 0.1},
        [
            _observation(universe="a", scenario="bull", wealth=2.0),
            _observation(universe="b", scenario="bull", wealth=2.0),
            _observation(universe="a", scenario="bear", wealth=1.0),
            _observation(universe="b", scenario="bear", wealth=1.0),
        ],
    )
    assert scenario_shift_only.universe_variance == 0.0


def test_dominance_and_pareto_gates_reject_bad_tradeoffs() -> None:
    baseline = _evaluation(wealth=2.0, drawdown=0.20, orders=10)
    dominated = _evaluation(wealth=1.9, drawdown=0.21, orders=11)
    assert not dominance_gate(dominated, baseline)
    assert not pareto_gate(dominated, baseline)

    higher_return = _evaluation(wealth=2.1, drawdown=0.20, orders=10)
    assert dominance_gate(higher_return, baseline)
    assert pareto_gate(higher_return, baseline)

    lower_risk_and_orders = _evaluation(wealth=2.0, drawdown=0.17, orders=9)
    assert pareto_gate(lower_risk_and_orders, baseline)

    weak_gain_bad_tradeoff = _evaluation(wealth=2.02, drawdown=0.22, orders=12)
    assert not pareto_gate(
        weak_gain_bad_tradeoff,
        baseline,
        materiality=GateMateriality(),
    )


def test_universe_stress_is_deterministic_complete_and_does_not_touch_global_rng() -> None:
    symbols = tuple(f"s{index:02d}" for index in range(30))
    industries = {
        symbol: ("optical" if index < 5 else "memory" if index < 15 else "equipment")
        for index, symbol in enumerate(symbols)
    }
    random.seed(99)
    expected = random.random()
    random.seed(99)
    first = random_universe_cases(symbols, sizes=(6, 12, 24), seeds=range(5))
    observed = random.random()
    second = random_universe_cases(reversed(symbols), sizes=(24, 12, 6), seeds=reversed(range(5)))
    assert observed == expected
    assert first == second
    assert len(first) == 15
    assert {len(case.symbols) for case in first} == {6, 12, 24}

    dependency = remove_core_cases(symbols, symbols[:3])
    assert len(dependency) == 7
    assert all(not set(case.symbols) >= set(symbols[:3]) for case in dependency)
    assert not any(
        industries[symbol] == "optical"
        for symbol in exclude_industry_case(symbols, industries, "optical").symbols
    )
    assert {case.name for case in industry_only_cases(symbols, industries)} == {
        "industry_equipment",
        "industry_memory",
        "industry_optical",
    }
    balanced = balanced_industry_case(symbols, industries, per_industry=2)
    assert len(balanced.symbols) == 6
    leave = leave_top_k_out_cases(symbols, symbols[:10], values=(1, 2, 3, 5))
    assert [len(case.symbols) for case in leave] == [29, 28, 27, 25]


def test_parameter_stress_and_ablation_are_stable_and_shared() -> None:
    base = {"enabled": True, "threshold": 0.5, "days": 10}
    stress = one_at_a_time_perturbations(
        base,
        parameters=("threshold", "days"),
        relative_deltas=(-0.1, 0.1),
        bounds={"threshold": (0.0, 1.0), "days": (1.0, None)},
    )
    assert [case.name for case in stress] == [
        "baseline",
        "days_minus_0.1",
        "days_plus_0.1",
        "threshold_minus_0.1",
        "threshold_plus_0.1",
    ]
    assert all("profiles" not in case.config() for case in stress)

    factorial = factorial_perturbations(base, {"threshold": (0.4, 0.6)})
    assert [case.config()["threshold"] for case in factorial] == [0.4, 0.6]
    with pytest.raises(ValueError, match="limit"):
        factorial_perturbations(base, {"threshold": (0.4, 0.5, 0.6)}, max_cases=2)

    ablations = build_ablations(
        base,
        ("enabled", "threshold"),
        disabled_values={"threshold": 0.0},
    )
    assert [case.name for case in ablations] == [
        "baseline",
        "without_enabled",
        "without_threshold",
    ]
    baseline = _evaluation(wealth=2.0, drawdown=0.2, orders=10)
    variant = _evaluation(wealth=1.9, drawdown=0.19, orders=9)
    delta = compare_ablations(baseline, [("without_enabled", variant)])[0]
    assert delta.wealth == pytest.approx(-0.1)
    assert delta.drawdown == pytest.approx(-0.01)


def test_trade_attribution_measures_regret_avoided_loss_and_missing_horizons() -> None:
    dates = pd.bdate_range("2026-01-02", periods=12)
    prices = {
        "winner": pd.Series(range(100, 112), index=dates, dtype=float),
        "loser": pd.Series(range(100, 88, -1), index=dates, dtype=float),
        "benchmark": pd.Series([100.0] * 12, index=dates),
    }
    records = [
        ExitRecord(
            "winner",
            str(dates[0].date()),
            100.0,
            "risk_off",
            "CORE",
            80.0,
            0.30,
            -0.05,
            "benchmark",
        ),
        ExitRecord(
            "loser",
            str(dates[0].date()),
            100.0,
            "risk_off",
            "ADD2",
            105.0,
            0.02,
            -0.15,
            "benchmark",
        ),
    ]
    attributed = attribute_exits(records, prices, horizons=(5, 10, 20))
    winner = next(item for item in attributed if item.record.symbol == "winner")
    loser = next(item for item in attributed if item.record.symbol == "loser")
    assert dict(winner.regret)[5] == pytest.approx(0.05)
    assert dict(winner.avoided_loss)[5] == 0.0
    assert dict(loser.avoided_loss)[5] == pytest.approx(0.05)
    assert dict(loser.regret)[5] == 0.0
    assert dict(winner.post_exit_returns)[20] is None
    summary = aggregate_by_reason(attributed)["risk_off"]
    assert summary["count"] == 2
    assert summary["mean_regret"] > 0
    assert summary["mean_avoided_loss"] > 0
    assert summary["mean_regret_5d"] == pytest.approx(0.025)
    assert summary["mean_avoided_loss_5d"] == pytest.approx(0.025)


def test_research_harness_is_not_imported_by_production_modules() -> None:
    repo = Path(__file__).parents[1]
    for path in (repo / "uquant").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import research" not in source
        assert "from research" not in source

    research_modules = (
        "candidate_search",
        "ablation",
        "universe_stress",
        "parameter_stress",
        "trade_attribution",
    )
    for name in research_modules:
        module = __import__(f"research.{name}", fromlist=[name])
        source = inspect.getsource(module)
        assert "ProductionEngine" not in source
