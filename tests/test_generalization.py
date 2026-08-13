from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from uquant.validation import generalization as generalization_module
from uquant.validation.generalization import (
    GeneralizationObservation,
    GeneralizationScenario,
    PreWindowEvidence,
    aggregate_metrics,
    build_generalization_provenance,
    build_generalization_scenarios,
    compute_pre_window_evidence,
    evaluate_generalization,
    industry_pnl_shares,
    load_generalization_baseline,
    observation_from_result,
    prior_dependence,
    reference_payload,
    run_generalization,
    scenario_fingerprint,
    symbol_pnl_from_result,
)


def test_generalization_git_runner_resolves_executable_and_uses_fixed_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(generalization_module.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(
        arguments: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> Any:
        assert check
        assert capture_output
        assert text
        calls.append(arguments)
        return type("Completed", (), {"stdout": "f" * 40 + "\n"})()

    monkeypatch.setattr(generalization_module.subprocess, "run", fake_run)

    assert (
        generalization_module._git_stdout(
            tmp_path,
            ["rev-parse", "HEAD"],
            label="cannot resolve test commit",
        )
        == "f" * 40 + "\n"
    )
    assert calls == [["/usr/bin/git", "-C", str(tmp_path), "rev-parse", "HEAD"]]


def test_generalization_git_runner_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(generalization_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="cannot resolve git executable"):
        generalization_module._git_executable()

    monkeypatch.setattr(generalization_module.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        generalization_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    with pytest.raises(RuntimeError, match="cannot inspect test source"):
        generalization_module._git_stdout(
            tmp_path,
            ["status"],
            label="cannot inspect test source",
        )


def test_generalization_production_commit_requires_clean_valid_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replies = iter(["", "a" * 40 + "\n"])
    calls: list[tuple[list[str], str]] = []

    def fake_git_stdout(_root: Path, arguments: list[str], *, label: str) -> str:
        calls.append((arguments, label))
        return next(replies)

    monkeypatch.setattr(generalization_module, "_git_stdout", fake_git_stdout)
    assert generalization_module._production_commit(tmp_path) == "a" * 40
    assert calls == [
        (
            [
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "uquant",
                "pyproject.toml",
            ],
            "cannot inspect generalization production source",
        ),
        (
            ["log", "-1", "--format=%H", "--", "uquant", "pyproject.toml"],
            "cannot resolve immutable production commit",
        ),
    ]

    monkeypatch.setattr(generalization_module, "_git_stdout", lambda *_args, **_kwargs: "dirty")
    with pytest.raises(RuntimeError, match="requires committed source"):
        generalization_module._production_commit(tmp_path)

    replies = iter(["", "not-a-commit"])
    monkeypatch.setattr(
        generalization_module,
        "_git_stdout",
        lambda *_args, **_kwargs: next(replies),
    )
    with pytest.raises(RuntimeError, match="cannot resolve immutable production commit"):
        generalization_module._production_commit(tmp_path)


def test_generalization_source_fingerprint_covers_exact_production_tree(tmp_path: Path) -> None:
    package = tmp_path / "uquant"
    nested = package / "validation"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (nested / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    first = generalization_module._production_source_fingerprint(tmp_path)
    assert len(first) == 64
    assert first == generalization_module._production_source_fingerprint(tmp_path)

    (nested / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert generalization_module._production_source_fingerprint(tmp_path) != first

    (tmp_path / "pyproject.toml").unlink()
    with pytest.raises(RuntimeError, match="cannot fingerprint generalization production source"):
        generalization_module._production_source_fingerprint(tmp_path)


def test_generalization_rejects_source_or_data_mutation_during_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "generalization.json"
    baseline.write_text("{}", encoding="utf-8")
    data_before = {
        "snapshot_id": "fixture",
        "files_verified": 1,
        "manifest_sha256": "a" * 64,
        "checksums_sha256": "b" * 64,
    }
    data_after = {**data_before, "manifest_sha256": "c" * 64}
    monkeypatch.setattr(generalization_module, "verify_data_manifest", lambda _: data_after)
    monkeypatch.setattr(
        generalization_module,
        "_production_source_fingerprint",
        lambda _: "d" * 64,
    )

    with (
        pytest.raises(RuntimeError, match="source or data changed during validation"),
        generalization_module._immutable_validation_inputs(
            baseline_path=baseline,
            baseline_sha256=generalization_module.hashlib.sha256(baseline.read_bytes()).hexdigest(),
            data_dir="fixture",
            repository_root=tmp_path,
            data_before=data_before,
            source_before="d" * 64,
        ),
    ):
        pass


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"as_of": "not-a-date"}, "valid as_of date"),
        ({"scores": (("a", 1.0), ("a", 0.5))}, "duplicate symbols"),
        ({"scores": (("", 1.0),)}, "invalid score"),
        ({"ineligible_symbols": ("",)}, "invalid ineligible symbol"),
        ({"ineligible_symbols": ("b", "a")}, "not canonical"),
        ({"ineligible_symbols": ("a", "a")}, "duplicate ineligible"),
        ({"ineligible_symbols": ("a",)}, "both eligible and ineligible"),
    ],
)
def test_pre_window_evidence_rejects_ambiguous_membership(
    overrides: dict[str, Any],
    message: str,
) -> None:
    values: dict[str, Any] = {"as_of": "2026-01-02", "scores": (("a", 1.0),)}
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        PreWindowEvidence(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": ""}, "require name"),
        ({"symbols": ("a", "a")}, "repeats a symbol"),
        ({"symbols": ("b", "a")}, "not canonical"),
        ({"removed_symbols": ("b", "b")}, "repeats a removed"),
        ({"removed_symbols": ("a",)}, "retains a removed"),
        ({"evidence_eligible_symbols": ("b", "a")}, "not canonical"),
        ({"evidence_eligible_symbols": ("",)}, "invalid evidence eligible"),
        (
            {"evidence_eligible_symbols": ("a",), "evidence_ineligible_symbols": ("a",)},
            "membership overlaps",
        ),
    ],
)
def test_generalization_scenario_rejects_noncanonical_contracts(
    overrides: dict[str, Any],
    message: str,
) -> None:
    values: dict[str, Any] = {"name": "case", "family": "baseline", "symbols": ("a",)}
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        GeneralizationScenario(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": ""}, "require name"),
        ({"final_wealth": 0.0}, "invalid final wealth"),
        ({"max_drawdown": 2.0}, "invalid maximum drawdown"),
        ({"account_orders": True}, "invalid account-order count"),
        ({"symbol_pnl": (("a", 1.0), ("a", 2.0))}, "duplicate symbol PnL"),
        ({"symbol_pnl": (("", 1.0),)}, "invalid symbol PnL"),
        (
            {"deployed_exposure": (("b", "CORE"), ("a", "CORE"))},
            "deployed exposure is not canonical",
        ),
        ({"deployed_exposure": (("a", "UNKNOWN"),)}, "invalid deployed exposure"),
    ],
)
def test_generalization_observation_rejects_invalid_economics(
    overrides: dict[str, Any],
    message: str,
) -> None:
    values: dict[str, Any] = {
        "name": "case",
        "family": "baseline",
        "final_wealth": 2.0,
        "max_drawdown": 0.1,
        "account_orders": 5,
        "symbol_pnl": (("a", 1.0),),
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        GeneralizationObservation(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("wealth_floor_ratio", False, "must be numeric"),
        ("wealth_floor_ratio", float("inf"), "must be finite"),
        ("order_tolerance", -1, "nonnegative integer"),
        ("wealth_floor_ratio", 0.0, "must be in"),
        ("drawdown_tolerance", 2.0, "must be in"),
        ("order_ceiling_ratio", 0.9, "cannot be below one"),
        ("dominance_wealth_regression", -0.1, "cannot be negative"),
        ("dominance_drawdown_regression", 1.1, "cannot exceed one"),
        ("remove_one_max_dependency", 0.3, "dependency ceiling"),
        ("remove_all_min_wealth", 1.0, "must require positive return"),
        ("remove_all_max_drawdown", 2.0, "drawdown ceiling"),
        ("remove_all_competitor_ratio", 2.0, "competitor ratio"),
        ("no_optical_max_drawdown", 2.0, "no-optical drawdown"),
        ("random_min_positive_fraction", 0.5, "positive fraction"),
        ("random_p10_min_wealth", 0.9, "p10 wealth floor"),
        ("optical_dependency_share_threshold", 0.8, "optical dependency"),
    ],
)
def test_generalization_policy_rejects_unsafe_thresholds(
    field: str,
    value: Any,
    message: str,
) -> None:
    policy = _policy()
    policy[field] = value
    with pytest.raises(RuntimeError, match=message):
        generalization_module._parse_policy(policy)


def test_generalization_policy_rejects_schema_drift() -> None:
    with pytest.raises(RuntimeError, match="must be an object"):
        generalization_module._parse_policy(None)

    missing = _policy()
    missing.pop("wealth_floor_ratio")
    with pytest.raises(RuntimeError, match="missing fields"):
        generalization_module._parse_policy(missing)

    unexpected = _policy()
    unexpected["unreviewed"] = 1.0
    with pytest.raises(RuntimeError, match="unexpected fields"):
        generalization_module._parse_policy(unexpected)


def test_generalization_identity_helpers_fail_closed() -> None:
    observation = GeneralizationObservation(
        name="case",
        family="baseline",
        final_wealth=2.0,
        max_drawdown=0.1,
        account_orders=1,
        symbol_pnl=(("a", 1.0),),
    )
    assert observation.pnl_map() == {"a": 1.0}

    for symbols, message in [
        (("",), "invalid symbol"),
        (("a", "a"), "duplicate symbols"),
        ((), "cannot be empty"),
    ]:
        with pytest.raises(ValueError, match=message):
            generalization_module._canonical_symbols(symbols, label="test universe")
    with pytest.raises(ValueError, match="stable scenario label"):
        generalization_module._slug("---")


def _universe() -> tuple[str, ...]:
    return tuple(f"s{index:02d}" for index in range(30))


def _industries() -> dict[str, str]:
    symbols = _universe()
    return {
        symbol: (
            "optical"
            if index < 5
            else "memory"
            if index < 15
            else "equipment"
            if index < 25
            else "pcb"
            if index < 28
            else "design"
            if index == 28
            else "foundry"
        )
        for index, symbol in enumerate(symbols)
    }


def _policy() -> dict[str, float | int]:
    return {
        "wealth_floor_ratio": 0.95,
        "drawdown_tolerance": 0.02,
        "order_tolerance": 1,
        "order_ceiling_ratio": 1.10,
        "dominance_wealth_regression": 0.01,
        "dominance_drawdown_regression": 0.005,
        "dominance_order_regression": 0.05,
        "pareto_wealth_improvement": 0.05,
        "pareto_drawdown_improvement": 0.02,
        "pareto_order_improvement": 0.10,
        "pareto_wealth_regression": 0.01,
        "pareto_drawdown_regression": 0.005,
        "pareto_order_regression": 0.05,
        "remove_one_max_dependency": 0.25,
        "remove_all_min_wealth": 1.01,
        "remove_all_max_drawdown": 0.25,
        "remove_all_competitor_ratio": 0.95,
        "no_optical_min_wealth": 1.01,
        "no_optical_max_drawdown": 0.25,
        "random_min_positive_fraction": 0.51,
        "random_p10_min_wealth": 1.01,
        "optical_dependency_share_threshold": 0.70,
    }


def _provenance() -> dict[str, Any]:
    return build_generalization_provenance(
        data={
            "snapshot_id": "fixture",
            "files_verified": 30,
            "manifest_sha256": "a" * 64,
            "checksums_sha256": "b" * 64,
        },
        universe=_universe(),
        industries=_industries(),
        prior_symbols=_universe()[:3],
        start="2026-01-05",
        end="2026-07-20",
        production_commit="c" * 40,
        production_source_sha256="d" * 64,
    )


def test_generalization_provenance_rejects_pre_2023_economic_interval() -> None:
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        build_generalization_provenance(
            data={
                "snapshot_id": "fixture",
                "files_verified": 30,
                "manifest_sha256": "a" * 64,
                "checksums_sha256": "b" * 64,
            },
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2022-01-04",
            end="2022-12-30",
            production_commit="c" * 40,
            production_source_sha256="d" * 64,
        )


def test_generalization_run_rejects_pre_2023_before_matching_provenance() -> None:
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        run_generalization(
            data_dir="unused",
            universe=_universe(),
            industries=_industries(),
            prior_symbols=_universe()[:3],
            start="2022-01-04",
            end="2022-12-30",
            baseline_path="unused.json",
            provenance=_provenance(),
            runner=lambda _case: {},
            pre_window_prices=_prices(),
        )


def _competitor_best(*, value: float = 2.0) -> dict[str, Any]:
    return {
        "metric": "final_wealth",
        "scenario": "remove_all_priors",
        "value": value,
        "provenance": {
            "repository": "ychenracing/uquant",
            "reference_path": "benchmarks/competitor_matrix_reference.json",
            "reference_commit": "e" * 40,
            "reference_sha256": "f" * 64,
        },
    }


def _reference_payload(
    cases: tuple[GeneralizationScenario, ...],
    observations: tuple[GeneralizationObservation, ...],
    *,
    policy: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    return reference_payload(
        cases,
        observations,
        policy=policy or _policy(),
        provenance=_provenance(),
        competitor_best=_competitor_best(),
    )


def _prices() -> dict[str, pd.Series]:
    dates = pd.bdate_range("2025-01-02", periods=330)
    return {
        symbol: pd.Series(
            [100.0 * (1.0 + 0.0005 * (index + 1)) ** offset for offset in range(len(dates))],
            index=dates,
            dtype=float,
        )
        for index, symbol in enumerate(_universe())
    }


def _matrix(
    *,
    random_seeds: range = range(3),
) -> tuple[tuple[GeneralizationScenario, ...], dict[str, pd.Series]]:
    prices = _prices()
    evidence = compute_pre_window_evidence(
        prices,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )
    cases = build_generalization_scenarios(
        _universe(),
        _industries(),
        _universe()[:3],
        window_start="2026-01-05",
        pre_window_evidence=evidence,
        random_sizes=(6, 12, 24),
        random_seeds=random_seeds,
        leave_top_k=(1, 2, 3, 5),
    )
    return cases, prices


def _observation(
    case: GeneralizationScenario,
    *,
    wealth: float = 2.0,
    drawdown: float = 0.10,
    orders: int = 5,
    pnl: tuple[tuple[str, float], ...] | None = None,
) -> GeneralizationObservation:
    return GeneralizationObservation(
        name=case.name,
        family=case.family,
        final_wealth=wealth,
        max_drawdown=drawdown,
        account_orders=orders,
        symbol_pnl=pnl if pnl is not None else ((case.symbols[0], 1.0),),
        deployed_exposure=((case.symbols[0], "CORE"),),
    )


def _deployed_exposure(case: GeneralizationScenario) -> list[dict[str, str]]:
    return [{"symbol": case.symbols[0], "lifecycle": "CORE"}]


def test_complete_scenario_matrix_is_deterministic_and_does_not_touch_global_rng() -> None:
    first, prices = _matrix()
    evidence = compute_pre_window_evidence(
        prices,
        reversed(_universe()),
        window_start="2026-01-05",
        lookback_sessions=120,
    )

    random.seed(9182)
    expected = random.random()
    random.seed(9182)
    second = build_generalization_scenarios(
        reversed(_universe()),
        _industries(),
        reversed(_universe()[:3]),
        window_start="2026-01-05",
        pre_window_evidence=evidence,
        random_sizes=(24, 12, 6),
        random_seeds=(2, 1, 0),
        leave_top_k=(5, 3, 2, 1),
    )
    observed = random.random()

    assert first == second
    assert observed == expected
    assert len(first) == 30
    families = {case.family for case in first}
    assert families == {
        "baseline",
        "remove_one",
        "remove_pair",
        "remove_all",
        "no_optical",
        "industry_only",
        "balanced",
        "random",
        "leave_top_k",
    }
    assert sum(case.family == "remove_one" for case in first) == 3
    assert sum(case.family == "remove_pair" for case in first) == 3
    assert sum(case.family == "random" for case in first) == 9
    assert {len(case.symbols) for case in first if case.family == "random"} == {6, 12, 24}
    assert not any(
        _industries()[symbol] == "optical"
        for case in first
        if case.name == "no_optical"
        for symbol in case.symbols
    )


def test_sparse_industry_diagnostics_are_not_presented_as_robust_industry_pools() -> None:
    cases, _ = _matrix(random_seeds=range(1))
    design = next(case for case in cases if case.name == "industry_only__design")
    foundry = next(case for case in cases if case.name == "industry_only__foundry")
    combined = next(case for case in cases if case.name == "industry_sparse_combined")
    balanced = next(case for case in cases if case.name == "balanced_industries")

    assert design.diagnostic == foundry.diagnostic == "singleton"
    assert design.symbols == ("s28",)
    assert foundry.symbols == ("s29",)
    assert combined.diagnostic == "combined_sparse"
    assert combined.source_industries == ("design", "foundry")
    assert combined.symbols == ("s28", "s29")
    assert balanced.diagnostic == "includes_singletons"


def test_leave_top_k_uses_only_pre_window_evidence() -> None:
    prices = _prices()
    evidence = compute_pre_window_evidence(
        prices,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )
    mutated = {symbol: series.copy() for symbol, series in prices.items()}
    for index, symbol in enumerate(reversed(_universe())):
        in_window = mutated[symbol].index >= pd.Timestamp("2026-01-05")
        mutated[symbol].loc[in_window] *= 10_000.0 * (index + 1)
    after = compute_pre_window_evidence(
        mutated,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )

    assert evidence == after
    assert pd.Timestamp(evidence.as_of) < pd.Timestamp("2026-01-05")
    cases = build_generalization_scenarios(
        _universe(),
        _industries(),
        _universe()[:3],
        window_start="2026-01-05",
        pre_window_evidence=evidence,
        random_sizes=(6, 12, 24),
        random_seeds=(0,),
        leave_top_k=(1, 2, 3, 5),
    )
    leave = {case.name: case for case in cases if case.family == "leave_top_k"}
    assert leave["leave_top_1"].removed_symbols == evidence.ranking[:1]
    assert leave["leave_top_5"].removed_symbols == evidence.ranking[:5]

    future = type(evidence)(as_of="2026-01-05", scores=evidence.scores)
    with pytest.raises(ValueError, match="must predate"):
        build_generalization_scenarios(
            _universe(),
            _industries(),
            _universe()[:3],
            window_start="2026-01-05",
            pre_window_evidence=future,
            random_seeds=(0,),
        )


def test_later_listings_remain_in_replays_but_never_enter_evidence_ranking(
    tmp_path: Path,
) -> None:
    prices = _prices()
    prices.pop("s28")
    prices["s29"] = prices["s29"].loc[prices["s29"].index >= pd.Timestamp("2025-10-01")]
    evidence = compute_pre_window_evidence(
        prices,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )

    assert evidence.ineligible_symbols == ("s28", "s29")
    assert set(evidence.eligible_symbols) == set(_universe()[:-2])
    assert not set(evidence.ranking) & set(evidence.ineligible_symbols)
    cases = build_generalization_scenarios(
        _universe(),
        _industries(),
        _universe()[:3],
        window_start="2026-01-05",
        pre_window_evidence=evidence,
        random_sizes=(6, 12, 24),
        random_seeds=(0,),
        leave_top_k=(1, 2, 3, 5),
    )
    base = next(case for case in cases if case.family == "baseline")
    leave = [case for case in cases if case.family == "leave_top_k"]
    assert set(base.symbols) == set(_universe())
    assert all(not set(case.removed_symbols) & {"s28", "s29"} for case in leave)
    assert all(case.evidence_eligible_symbols == evidence.eligible_symbols for case in cases)
    assert all(case.evidence_ineligible_symbols == ("s28", "s29") for case in cases)

    observations = tuple(_observation(case, wealth=2.0, drawdown=0.10, orders=5) for case in cases)
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
            "max_drawdown": 0.08,
            "account_orders": 4,
            "symbol_pnl": {case.symbols[0]: 1.0},
            "deployed_exposure": _deployed_exposure(case),
        },
        industries=_industries(),
        baseline=baseline,
    )
    assert report["pre_window_evidence"] == {
        "as_of": evidence.as_of,
        "eligible_symbols": list(evidence.eligible_symbols),
        "ineligible_symbols": ["s28", "s29"],
    }

    promoted = type(evidence)(
        as_of=evidence.as_of,
        scores=tuple(sorted((*evidence.scores, ("s29", 0.0)))),
        ineligible_symbols=("s28",),
    )
    promoted_cases = build_generalization_scenarios(
        _universe(),
        _industries(),
        _universe()[:3],
        window_start="2026-01-05",
        pre_window_evidence=promoted,
        random_sizes=(6, 12, 24),
        random_seeds=(0,),
        leave_top_k=(1, 2, 3, 5),
    )
    assert scenario_fingerprint(promoted_cases) != scenario_fingerprint(cases)


def test_pre_window_evidence_requires_a_meaningful_eligible_subset() -> None:
    dates = pd.bdate_range("2025-01-02", periods=130)
    only_history = pd.Series(range(1, 131), index=dates, dtype=float)

    with pytest.raises(ValueError, match="no meaningful eligible subset"):
        compute_pre_window_evidence(
            {"a": only_history},
            ("a", "later_listing"),
            window_start="2026-01-05",
            lookback_sessions=120,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda mapping: mapping.pop("s29"), "missing=\\['s29'\\]"),
        (lambda mapping: mapping.update({"outside": "memory"}), "extra=\\['outside'\\]"),
    ],
)
def test_industry_map_must_exactly_cover_the_replay_universe(
    mutate: Any,
    message: str,
) -> None:
    prices = _prices()
    evidence = compute_pre_window_evidence(
        prices,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )
    industries = _industries()
    mutate(industries)

    with pytest.raises(ValueError, match=message):
        build_generalization_scenarios(
            _universe(),
            industries,
            _universe()[:3],
            window_start="2026-01-05",
            pre_window_evidence=evidence,
            random_sizes=(6, 12, 24),
            random_seeds=(0,),
            leave_top_k=(1, 2, 3, 5),
        )


def test_run_rejects_industry_coverage_before_evidence_baseline_or_replay(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="missing=\\['b'\\]"):
        run_generalization(
            data_dir="unused",
            universe=("a", "b"),
            industries={"a": "memory"},
            prior_symbols=("a",),
            start="2026-01-05",
            end="2026-07-20",
            baseline_path=tmp_path / "does-not-exist.json",
            pre_window_prices={},
            runner=lambda _: pytest.fail("replay must not start"),
            random_sizes=(1,),
            random_seeds=(0,),
            leave_top_k=(1,),
        )


def test_aggregate_pdi_and_signed_industry_pnl_share() -> None:
    base = GeneralizationScenario("base", "baseline", ("memory", "optical"))
    one_a = GeneralizationScenario("remove_one__a", "remove_one", ("memory",))
    one_b = GeneralizationScenario("remove_one__b", "remove_one", ("optical",))
    all_priors = GeneralizationScenario("remove_all_priors", "remove_all", ("other",))
    observations = (
        _observation(base, wealth=10.0, drawdown=0.10, orders=4, pnl=(("optical", 70.0), ("memory", 30.0))),
        _observation(one_a, wealth=8.0, drawdown=0.20, orders=8),
        _observation(one_b, wealth=9.0, drawdown=0.30, orders=6),
        _observation(all_priors, wealth=7.0, drawdown=0.40, orders=10),
    )

    dependency = prior_dependence(observations)
    assert dependency["PDI_1"] == pytest.approx(0.20)
    assert dependency["PDI_3"] == pytest.approx(0.30)
    assert dependency["PDI_1_worst_case"] == "remove_one__a"

    aggregate = aggregate_metrics(observations[1:])
    assert aggregate == pytest.approx(
        {
            "p10_wealth": 7.2,
            "median_wealth": 8.0,
            "p90_drawdown": 0.38,
            "worst_drawdown": 0.40,
            "median_orders": 8.0,
            "p90_orders": 9.6,
        }
    )
    shares = industry_pnl_shares(
        observations[0],
        {"optical": "optical", "memory": "memory"},
    )
    assert shares["optical"]["share_of_net_pnl"] == pytest.approx(0.70)
    assert shares["memory"]["share_of_net_pnl"] == pytest.approx(0.30)

    signed = GeneralizationObservation(
        "signed",
        "baseline",
        2.0,
        0.1,
        1,
        (("winner", 120.0), ("loser", -20.0)),
    )
    signed_shares = industry_pnl_shares(
        signed,
        {"winner": "optical", "loser": "memory"},
    )
    assert signed_shares["optical"]["share_of_net_pnl"] == pytest.approx(1.20)
    assert signed_shares["memory"]["share_of_net_pnl"] == pytest.approx(-0.20)


def test_symbol_pnl_reconciles_fills_and_open_positions_to_total_profit() -> None:
    result: dict[str, Any] = {
        "final_equity": 1092.0,
        "final_account": {
            "initial_cash": 1000.0,
            "fills": [
                {
                    "symbol": "a",
                    "side": "BUY",
                    "gross_value": 400.0,
                    "commission": 4.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                },
                {
                    "symbol": "a",
                    "side": "SELL",
                    "gross_value": 250.0,
                    "commission": 1.0,
                    "stamp_duty": 1.0,
                    "transfer_fee": 0.0,
                },
                {
                    "symbol": "b",
                    "side": "BUY",
                    "gross_value": 200.0,
                    "commission": 2.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                },
            ],
            "positions": {
                "a": {"shares": 10},
                "b": {"shares": 10},
            },
        },
    }

    pnl = symbol_pnl_from_result(result, {"a": 20.0, "b": 25.0})
    assert pnl == pytest.approx({"a": 44.0, "b": 48.0})
    assert sum(pnl.values()) == pytest.approx(92.0)

    result["final_equity"] = 1093.0
    with pytest.raises(ValueError, match="does not reconcile"):
        symbol_pnl_from_result(result, {"a": 20.0, "b": 25.0})


def test_observation_rejects_inexact_orders_and_out_of_universe_attribution() -> None:
    case = GeneralizationScenario("base", "baseline", ("a",))
    with pytest.raises(ValueError, match="non-integer order count"):
        observation_from_result(
            case,
            {
                "final_wealth": 1.1,
                "max_drawdown": 0.1,
                "account_orders": 1.5,
                "symbol_pnl": {"a": 0.1},
            },
        )
    with pytest.raises(ValueError, match="outside its universe"):
        observation_from_result(
            case,
            {
                "final_wealth": 1.1,
                "max_drawdown": 0.1,
                "account_orders": 1,
                "symbol_pnl": {"invented": 0.1},
            },
        )

    observed = observation_from_result(
        case,
        {
            "final_wealth": 1.1,
            "max_drawdown": 0.1,
            "account_orders": 1,
            "symbol_pnl": {"a": 0.1},
            "final_account": {
                "fills": [
                    {
                        "symbol": "a",
                        "side": "BUY",
                        "shares": 100,
                        "lifecycle": "CORE",
                        "reason_code": "strategic_cohort",
                    }
                ]
            },
        },
    )
    assert observed.deployed_exposure == (("a", "CORE"), ("a", "STRATEGIC"))


def test_baseline_rejects_duplicate_missing_and_unexpected_references(tmp_path: Path) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    payload = _reference_payload(cases, observations)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_generalization_baseline(path, cases)
    assert loaded.case_fingerprint == scenario_fingerprint(cases)

    missing = json.loads(json.dumps(payload))
    missing["references"].pop(cases[-1].name)
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing references"):
        load_generalization_baseline(path, cases)

    unexpected = json.loads(json.dumps(payload))
    unexpected["references"]["invented"] = unexpected["references"]["base"]
    path.write_text(json.dumps(unexpected), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unexpected references"):
        load_generalization_baseline(path, cases)

    duplicate = (
        '{"schema_version":1,"case_fingerprint":"x","references":{'
        '"base":{"final_wealth":1,"max_drawdown":0,"account_orders":0},'
        '"base":{"final_wealth":1,"max_drawdown":0,"account_orders":0}}}'
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate key: base"):
        load_generalization_baseline(path, (cases[0],))


def test_baseline_binds_manifest_dataset_execution_and_production_provenance(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    expected = _provenance()
    payload = _reference_payload(cases, observations)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_generalization_baseline(path, cases, expected_provenance=expected)
    assert loaded.validation_fingerprint == payload["validation_fingerprint"]
    assert loaded.provenance == expected

    changed = json.loads(json.dumps(expected))
    changed["data"]["manifest_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="provenance does not match"):
        load_generalization_baseline(path, cases, expected_provenance=changed)

    stale = json.loads(json.dumps(payload))
    stale["provenance"]["production"]["source_sha256"] = "1" * 64
    path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(RuntimeError, match="validation fingerprint is stale"):
        load_generalization_baseline(path, cases)

    missing = json.loads(json.dumps(payload))
    missing.pop("provenance")
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing sections.*provenance"):
        load_generalization_baseline(path, cases)


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
