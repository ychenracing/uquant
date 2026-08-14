from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

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
from uquant.validation.generalization_reference import (
    GENERALIZATION_BASELINE_PATH,
    GENERALIZATION_POLICY_PATH,
    load_generalization_baseline,
    load_generalization_policy,
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


def _read_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _reseal_contract(payload: dict[str, Any]) -> None:
    canonical = {key: payload[key] for key in sorted(payload) if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            canonical,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def test_frozen_generalization_baseline_and_policy_load_with_complete_coverage() -> None:
    """Catches empty reviewed inputs or a baseline that omits fixed contract cells."""
    baseline = load_generalization_baseline()
    policy = load_generalization_policy()

    assert len(baseline.cells) == 234
    assert sum(cell.economic for cell in baseline.cells.values()) == 192
    assert sum(cell.replay_error is not None for cell in baseline.cells.values()) == 1
    assert baseline.runner_head == "80ad88ea03952bcb2839e6aab6390bb9541f739e"
    assert policy.baseline_sha256 == baseline.sha256
    assert policy.random_base_seed == 20260810
    assert policy.random_seed_indexes == (0, 1, 2, 3, 4)
    assert policy.random_pool_sizes == (5, 9, 15, 20)


def test_empty_generalization_baseline_fails_closed(tmp_path: Path) -> None:
    """Catches placeholder creation when the reviewed baseline is absent."""
    empty = tmp_path / "empty-baseline.json"
    empty.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="baseline"):
        load_generalization_baseline(empty)


@pytest.mark.parametrize(
    "mutation",
    ("cell", "missing_scenario", "seed", "window", "provenance"),
)
def test_edited_and_resealed_generalization_baseline_fails_compiled_anchor(
    mutation: str,
    tmp_path: Path,
) -> None:
    """Catches local resealing of cells, coverage, seeds, windows, or provenance."""
    payload = copy.deepcopy(_read_contract(GENERALIZATION_BASELINE_PATH))
    if mutation == "cell":
        cell = next(item for item in payload["cells"] if item["metrics"] is not None)
        cell["metrics"]["final_wealth"] += 0.01
    elif mutation == "missing_scenario":
        payload["cells"].pop()
    elif mutation == "seed":
        cell = next(item for item in payload["cells"] if item["seed_index"] is not None)
        cell["seed_index"] = 99
    elif mutation == "window":
        payload["cells"][0]["window"] = "replacement-window"
    else:
        payload["matrix_runner"]["head"] = "9" * 40
    _reseal_contract(payload)
    changed = tmp_path / f"changed-baseline-{mutation}.json"
    changed.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match=r"compiled|reviewed"):
        load_generalization_baseline(changed)


@pytest.mark.parametrize(
    ("field", "weakened"),
    (
        ("wealth_ratio_min", 0.94),
        ("drawdown_absolute_buffer", 0.021),
        ("orders_ratio_max", 1.11),
        ("turnover_ratio_max", 1.11),
    ),
)
def test_edited_and_resealed_policy_threshold_weakening_fails_compiled_anchor(
    field: str,
    weakened: float,
    tmp_path: Path,
) -> None:
    """Catches self-signing a weaker non-regression policy after matrix review."""
    payload = copy.deepcopy(_read_contract(GENERALIZATION_POLICY_PATH))
    payload["relative_per_cell"][field] = weakened
    _reseal_contract(payload)
    changed = tmp_path / f"weakened-policy-{field}.json"
    changed.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match=r"compiled|reviewed"):
        load_generalization_policy(changed)


def test_edited_and_resealed_policy_seed_contract_fails_compiled_anchor(
    tmp_path: Path,
) -> None:
    """Catches replacing a failing fixed seed after observing champion output."""
    payload = copy.deepcopy(_read_contract(GENERALIZATION_POLICY_PATH))
    payload["scenario_contract"]["random_seed_indexes"][-1] = 5
    _reseal_contract(payload)
    changed = tmp_path / "changed-policy-seeds.json"
    changed.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match=r"compiled|reviewed"):
        load_generalization_policy(changed)
