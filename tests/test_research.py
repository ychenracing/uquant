from __future__ import annotations

import inspect
import random
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# The project distribution intentionally packages only ``uquant*``. Research
# remains a repository-local harness, so make that isolation explicit in tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.ablation import build_ablations, compare_ablations
from research.candidate_runner import CandidateRunner
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
from research.universe_stress import canonical_universe_cases
from uquant.validation.ai_era import AI_ERA_WINDOWS
from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.universe import load_ai_universe


def _observation(
    *,
    universe: str = "a",
    window: str = "h1_2023",
    wealth: float = 2.0,
    drawdown: float = 0.20,
    turnover: float = 1.0,
    orders: int = 10,
    urgent_return: float = -0.05,
    pdi: float = 0.10,
) -> ReplayObservation:
    start, end = AI_ERA_WINDOWS[window]
    return ReplayObservation(
        universe=universe,
        window=window,
        start=start,
        end=end,
        final_wealth=wealth,
        max_drawdown=drawdown,
        annual_turnover=turnover,
        account_orders=orders,
        urgent_return=urgent_return,
        prior_dependence=pdi,
    )


def test_replay_observation_binds_exact_official_ai_era_window() -> None:
    observation = _observation(window="h1_2023")

    assert (observation.window, observation.start, observation.end) == (
        "h1_2023",
        "2023-01-03",
        "2023-06-30",
    )


def test_replay_observation_derives_calmar_period_from_official_dates() -> None:
    observation = _observation(window="h1_2023", wealth=1.10, drawdown=0.10)
    years = (date(2023, 6, 30) - date(2023, 1, 3)).days / 365.25

    assert observation.years == pytest.approx(years)
    assert observation.calmar == pytest.approx(((1.10 ** (1.0 / years)) - 1.0) / 0.10)


@pytest.mark.parametrize(
    ("window", "start", "end", "message"),
    [
        ("bear_2022", "2022-01-04", "2022-12-30", "official AI-era window"),
        ("h1_2023", "2023-01-04", "2023-06-30", "does not match official interval"),
        ("h1_2023", "2023-01-03", "2023-06-29", "does not match official interval"),
    ],
)
def test_replay_observation_rejects_unofficial_or_mismatched_window(
    window: str,
    start: str,
    end: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ReplayObservation(
            universe="a",
            window=window,
            start=start,
            end=end,
            final_wealth=2.0,
            max_drawdown=0.2,
            annual_turnover=1.0,
            account_orders=10,
        )


def test_candidate_trace_rejects_pre_ai_era_economic_replay_before_data_access(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="2023-01-01"):
        CandidateRunner(tmp_path).trace_cell(
            symbols=("sz300308",),
            start="2022-01-04",
            end="2023-01-03",
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

    def runner(config: dict[str, object], pool: str, window: str) -> ReplayObservation:
        calls.append((tuple(sorted(config.items())), pool, window))
        return _observation(
            universe=pool,
            window=window,
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
        windows=("h2_2023", "h1_2023"),
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
        assert {(pool, window) for seen, pool, window in calls if seen == config} == {
            ("a", "h1_2023"),
            ("a", "h2_2023"),
            ("b", "h1_2023"),
            ("b", "h2_2023"),
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

    window_shift_only = evaluate_candidate(
        {"edge": 0.1},
        [
            _observation(universe="a", window="h1_2023", wealth=2.0),
            _observation(universe="b", window="h1_2023", wealth=2.0),
            _observation(universe="a", window="h2_2023", wealth=1.0),
            _observation(universe="b", window="h2_2023", wealth=1.0),
        ],
    )
    assert window_shift_only.universe_variance == 0.0


def test_search_rejects_unofficial_window_before_running_candidates() -> None:
    def runner(_config: dict[str, object], _pool: str, _window: str) -> ReplayObservation:
        raise AssertionError("unofficial window reached candidate runner")

    with pytest.raises(ValueError, match="official AI-era windows"):
        search_candidates(
            parameter_grid={"edge": [0.1]},
            pools=("a",),
            windows=("bear_2022",),
            runner=runner,  # type: ignore[arg-type]
        )


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
    universe = load_ai_universe()
    evidence = PreWindowEvidence(
        as_of="2022-12-30",
        scores=tuple((symbol, float(index)) for index, symbol in enumerate(universe.symbols)),
    )
    random.seed(99)
    expected_random = random.random()
    random.seed(99)
    first = canonical_universe_cases(evidence=evidence, window_name="h1_2023")
    observed_random = random.random()
    second = canonical_universe_cases(evidence=evidence, window_name="h1_2023")

    assert observed_random == expected_random
    assert first == second
    assert len(first) == 32
    assert sum(case.family == "random" for case in first) == 20
    assert {len(case.symbols) for case in first if case.family == "random"} == {
        5,
        9,
        15,
        20,
    }


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


def test_candidate_runner_emits_canonical_daily_trace_and_finds_first_divergence(
    data_dir: Path,
) -> None:
    from research.candidate_runner import CandidateRunner, first_divergence

    runner = CandidateRunner(data_dir)
    trace = runner.trace_cell(
        symbols=("sz300308", "sz300502", "sz300394"),
        start="2026-06-25",
        end="2026-07-03",
    )

    assert trace.observations
    first = trace.observations[0]
    assert first.date == "2026-06-25"
    assert first.opportunity
    assert first.risk
    assert isinstance(first.transition_damage, float)
    assert isinstance(first.family_votes, tuple)
    assert isinstance(first.sector_guard_active, bool)
    assert isinstance(first.capital_budget_level, int)
    assert isinstance(first.leaders, tuple)
    assert isinstance(first.strategic_tag, str)
    assert isinstance(first.targets, tuple)
    assert isinstance(first.orders, tuple)
    assert isinstance(first.fills, tuple)
    assert first.equity > 0
    assert first_divergence(trace, trace) is None

    changed = replace(first, opportunity="CHOPPY" if first.opportunity != "CHOPPY" else "TREND")
    other = replace(trace, observations=(changed, *trace.observations[1:]))
    divergence = first_divergence(trace, other)
    assert divergence is not None
    assert divergence.date == first.date
    assert divergence.changed_fields == ("opportunity",)


def test_candidate_runner_rejects_misaligned_traces() -> None:
    from research.candidate_runner import CellTrace, DecisionTrace, first_divergence

    base = DecisionTrace(
        date="2026-01-02",
        opportunity="CHOPPY",
        risk="NORMAL",
        transition_damage=0.0,
        family_votes=(),
        sector_guard_active=False,
        capital_budget_level=0,
        leaders=(),
        strategic_tag="",
        targets=(),
        orders=(),
        fills=(),
        equity=2_000_000.0,
    )
    with pytest.raises(ValueError, match="aligned dates"):
        first_divergence(
            CellTrace("a", "window", (base,)),
            CellTrace("a", "window", (replace(base, date="2026-01-05"),)),
        )


def test_walk_forward_folds_are_deterministic_and_purged() -> None:
    from research.statistics import walk_forward_folds

    first = walk_forward_folds(30, train_size=12, test_size=4, step=4, purge=2)
    second = walk_forward_folds(30, train_size=12, test_size=4, step=4, purge=2)

    assert first == second
    assert first[0].train == tuple(range(12))
    assert first[0].test == tuple(range(14, 18))
    assert all(max(fold.train) + 2 < min(fold.test) for fold in first)


def test_probability_of_backtest_overfitting_detects_unstable_winners() -> None:
    import numpy as np

    from research.statistics import probability_of_backtest_overfitting

    stable = np.column_stack(
        [
            np.linspace(0.01, 0.02, 24),
            np.linspace(0.00, 0.01, 24),
            np.linspace(-0.01, 0.00, 24),
        ]
    )
    unstable = stable.copy()
    unstable[:12, 0], unstable[12:, 0] = 0.10, -0.10
    unstable[:12, 1], unstable[12:, 1] = -0.10, 0.10

    assert probability_of_backtest_overfitting(stable, slices=6).probability <= 0.5
    assert probability_of_backtest_overfitting(unstable, slices=6).probability >= 0.5


def test_deflated_sharpe_is_bounded_and_rejects_insufficient_samples() -> None:
    from research.statistics import deflated_sharpe_ratio

    result = deflated_sharpe_ratio(
        observed_sharpe=1.5,
        trials=20,
        sample_count=252,
        skew=0.0,
        kurtosis=3.0,
    )

    assert 0.0 <= result.probability <= 1.0
    assert result.expected_max_sharpe > 0.0
    with pytest.raises(ValueError, match="sample_count"):
        deflated_sharpe_ratio(
            observed_sharpe=1.0,
            trials=2,
            sample_count=2,
            skew=0.0,
            kurtosis=3.0,
        )
