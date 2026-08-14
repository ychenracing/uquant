from __future__ import annotations

import dataclasses

import pytest

from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import (
    CORE_SYMBOLS,
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
    ScenarioStatus,
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from uquant.validation.universe import load_ai_universe


def _evidence() -> PreWindowEvidence:
    universe = load_ai_universe()
    symbols = universe.symbols_as_of("2022-12-30")
    return PreWindowEvidence(
        as_of="2022-12-30",
        scores=tuple((symbol, float(index)) for index, symbol in enumerate(symbols)),
    )


def test_official_windows_are_exact_and_cannot_replay_pre_ai_era() -> None:
    """Catches an added/removed window or a shard that changes economic bounds."""
    windows = official_windows()

    assert tuple((window.name, window.start, window.end) for window in windows) == (
        ("h1_2023", "2023-01-03", "2023-06-30"),
        ("h2_2023", "2023-07-03", "2023-12-29"),
        ("h1_2024", "2024-01-02", "2024-07-01"),
        ("h2_2024", "2024-07-01", "2024-12-31"),
        ("bull_crash_2025_2026", "2025-01-02", "2026-07-31"),
        ("continuous_ai_era", "2023-01-03", "2026-08-05"),
    )
    assert official_windows(("h2_2024",)) == (windows[3],)
    with pytest.raises(ValueError, match="official window"):
        official_windows(("2022",))


def test_contract_builds_complete_fixed_matrix_and_explicit_insufficient_samples() -> None:
    """Catches silently omitted singleton industries or a changed blocking matrix."""
    scenarios = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=_evidence(),
    )
    economic = tuple(item for item in scenarios if item.status is ScenarioStatus.READY)
    insufficient = tuple(
        item for item in scenarios if item.status is ScenarioStatus.INSUFFICIENT_SAMPLE
    )

    assert CORE_SYMBOLS == ("sz300308", "sz300502", "sz300394")
    assert RANDOM_BASE_SEED == 20260810
    assert RANDOM_POOL_SIZES == (5, 9, 15, 20)
    assert RANDOM_SEED_INDEXES == (0, 1, 2, 3, 4)
    assert len(economic) == 32
    assert len(scenarios) == 39
    assert {item.name for item in insufficient} == {
        "subindustry__advanced_packaging",
        "subindustry__datacenter",
        "subindustry__design",
        "subindustry__foundry",
        "subindustry__passives",
        "subindustry__semiconductor",
        "subindustry__storage",
    }
    assert all(item.symbols and not item.economic for item in insufficient)
    assert all(item.raw_scenario is None for item in insufficient)
    assert len({item.name for item in scenarios}) == len(scenarios)


def test_fixed_random_pools_expose_seed_indexes_and_derived_seeds() -> None:
    """Catches replacement seeds, global RNG use, or a changed derivation algorithm."""
    first = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=_evidence(),
    )
    second = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=_evidence(),
    )
    random_cases = tuple(item for item in first if item.family == "random")

    assert [(item.pool_size, item.seed_index) for item in random_cases] == [
        (size, index) for size in (5, 9, 15, 20) for index in (0, 1, 2, 3, 4)
    ]
    assert random_cases[0].derived_seed == 12162893439572421475
    assert random_cases[-1].derived_seed == 7197313796403640679
    assert scenario_contract_fingerprint(first) == scenario_contract_fingerprint(second)
    assert all(tuple(sorted(item.symbols)) == item.symbols for item in random_cases)


def test_contract_is_immutable_and_no_optical_only_changes_tradable_symbols() -> None:
    """Catches mutable contract cells or accidental reference-context replacement."""
    scenarios = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=_evidence(),
    )
    no_optical = next(item for item in scenarios if item.name == "tradable-no-optical")
    universe = load_ai_universe()
    industry = {member.symbol: member.industry for member in universe.members}

    assert no_optical.reference_symbols == universe.symbols
    assert all(industry[symbol] != "optical" for symbol in no_optical.symbols)
    with pytest.raises(dataclasses.FrozenInstanceError):
        no_optical.name = "mutated"  # type: ignore[misc]


def test_h1_2023_contract_uses_literal_point_in_time_membership() -> None:
    """Catches future listings entering any H1 2023 tradable scenario."""
    scenarios = build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=_evidence(),
    )
    expected = (
        "sh600487",
        "sh601869",
        "sh603688",
        "sh603986",
        "sh688008",
        "sh688012",
        "sh688019",
        "sh688037",
        "sh688041",
        "sh688072",
        "sh688082",
        "sh688110",
        "sh688120",
        "sh688200",
        "sh688233",
        "sh688256",
        "sh688268",
        "sh688300",
        "sh688498",
        "sh688766",
        "sz000636",
        "sz002281",
        "sz002371",
        "sz002409",
        "sz300054",
        "sz300223",
        "sz300308",
        "sz300394",
        "sz300502",
        "sz300604",
        "sz300666",
    )

    assert next(item for item in scenarios if item.name == "full").symbols == expected
    assert all(set(item.symbols) <= set(expected) for item in scenarios if item.economic)
    assert {"sh688146", "sh688347", "sh688361"}.isdisjoint(
        symbol for item in scenarios if item.economic for symbol in item.symbols
    )


def test_evidence_and_lookback_change_the_scenario_contract_fingerprint() -> None:
    """Catches causal evidence scores, date, or lookback being absent from identity."""
    window = official_windows(("h1_2023",))[0]
    evidence = _evidence()
    changed_scores = PreWindowEvidence(
        as_of=evidence.as_of,
        scores=tuple(
            (symbol, score + 0.25 if index == 0 else score)
            for index, (symbol, score) in enumerate(evidence.scores)
        ),
    )
    changed_date = PreWindowEvidence(as_of="2023-01-02", scores=evidence.scores)
    base = build_official_scenarios(window=window, evidence=evidence, lookback_sessions=120)

    assert scenario_contract_fingerprint(base) != scenario_contract_fingerprint(
        build_official_scenarios(
            window=window,
            evidence=changed_scores,
            lookback_sessions=120,
        )
    )
    assert scenario_contract_fingerprint(base) != scenario_contract_fingerprint(
        build_official_scenarios(
            window=window,
            evidence=changed_date,
            lookback_sessions=120,
        )
    )
    assert scenario_contract_fingerprint(base) != scenario_contract_fingerprint(
        build_official_scenarios(window=window, evidence=evidence, lookback_sessions=121)
    )
    with pytest.raises(ValueError, match="evidence identity differs"):
        dataclasses.replace(base[0], evidence_sha256="0" * 64)
