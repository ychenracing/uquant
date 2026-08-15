from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from research.candidate_search import ReplayObservation, search_candidates, validate_shared_config
from research.parameter_stress import factorial_perturbations, one_at_a_time_perturbations
from uquant.config import SystemConfig
from uquant.config_governance import (
    ParameterCategory,
    load_config_governance,
    validate_governed_config_migration,
)
from uquant.validation.ai_era import AI_ERA_WINDOWS

ROOT = Path(__file__).parents[1]
GOVERNANCE_PATH = ROOT / "benchmarks" / "config_parameter_governance.json"
REMOVED_COMPATIBILITY_OVERRIDES: tuple[dict[str, object], ...] = (
    {"strategic_cohort_symbols": ()},
    {"strategic_partial_universe_max_size": 8},
    {"adaptive_broad_universe_min_size": 10},
    {"adaptive_broad_universe_compatibility_enabled": False},
    {"strategic_expansive_universe_min_size": 20},
    {"strategic_persistent_confirm_days": 3},
    {"strategic_reversal_confirm_days": 2},
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "artifact_sha256"},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_governance_classifies_every_system_config_field_once() -> None:
    governance = load_config_governance()
    entries = governance.entries
    config_fields = {field.name for field in fields(SystemConfig)}

    assert {category.value for category in ParameterCategory} == {
        "MARKET_RULE",
        "SAFETY",
        "ECONOMIC",
        "DERIVED",
        "COMPATIBILITY",
    }
    assert len(entries) == len(config_fields)
    assert {entry.field for entry in entries} == config_fields
    assert len({entry.field for entry in entries}) == len(entries)
    assert all(entry.owner.value and entry.rationale.strip() for entry in entries)
    assert governance.current_total_fields == len(config_fields)
    assert governance.current_economic_fields == sum(
        entry.category is ParameterCategory.ECONOMIC for entry in entries
    )


def test_governance_is_compile_anchored_against_resealing(tmp_path: Path) -> None:
    payload = json.loads(GOVERNANCE_PATH.read_text(encoding="utf-8"))
    payload["categories"]["ECONOMIC"][0]["rationale"] += " edited"
    payload["artifact_sha256"] = _canonical_sha256(payload)
    edited = tmp_path / "config-governance.json"
    edited.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="compiled reviewed governance"):
        load_config_governance(edited)


def test_governed_config_migration_binds_both_exact_config_identities() -> None:
    migration = validate_governed_config_migration(SystemConfig())

    assert migration.champion_config_sha256 == (
        "023d709731196a325d9cd03e95ece92e4baf63d2c5c66bb9f7d0e7a190e7bf20"
    )
    assert migration.candidate_config_sha256 == (
        "7f8bb875abb16f54f050561a711fa52cc4c465c021537cb15c85a611f6e7d56c"
    )
    assert migration.removed_fields == (
        "strategic_cohort_symbols",
        "strategic_partial_universe_max_size",
        "adaptive_broad_universe_min_size",
        "adaptive_broad_universe_compatibility_enabled",
        "strategic_expansive_universe_min_size",
        "strategic_persistent_confirm_days",
        "strategic_reversal_confirm_days",
    )
    assert len(migration.carrier_sha256) == 64


def test_governed_config_migration_rejects_any_remaining_field_change() -> None:
    changed = SystemConfig().override(leader_mature_score=0.73)

    with pytest.raises(ValueError, match="reviewed post-removal config"):
        validate_governed_config_migration(changed)


@pytest.mark.parametrize(
    "parameters",
    [
        {"commission_rate": 0.0002},
        {"max_gross": 0.99},
        {"same_day_leader_pipeline_enabled": True},
        {"not_a_system_config_field": 1},
    ],
)
def test_candidate_validation_rejects_every_non_economic_category(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="ECONOMIC"):
        validate_shared_config(parameters)  # type: ignore[arg-type]


def test_candidate_search_rejects_non_economic_grid_before_runner() -> None:
    calls = 0

    def values() -> Any:
        raise AssertionError("non-economic grid values were consumed")
        yield 0.99

    def runner(_config: dict[str, object], _pool: str, _window: str) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("non-economic candidate reached replay")

    with pytest.raises(ValueError, match="ECONOMIC"):
        search_candidates(
            parameter_grid={"max_gross": values()},
            pools=("a",),
            windows=("h1_2023",),
            runner=runner,  # type: ignore[arg-type]
        )
    assert calls == 0


def test_candidate_grid_rejects_noncanonical_name_before_values() -> None:
    def values() -> Any:
        raise AssertionError("noncanonical grid values were consumed")
        yield 0.73

    with pytest.raises(ValueError, match="canonical exact"):
        search_candidates(
            parameter_grid={" leader_mature_score": values()},
            pools=("a",),
            windows=("h1_2023",),
            runner=lambda *_args: None,  # type: ignore[arg-type]
        )


def test_parameter_stress_public_paths_reject_non_economic_overrides() -> None:
    with pytest.raises(ValueError, match="ECONOMIC"):
        one_at_a_time_perturbations({"max_gross": 1.0})
    with pytest.raises(ValueError, match="ECONOMIC"):
        one_at_a_time_perturbations(
            {"leader_mature_score": 0.72},
            parameters=("max_gross",),
        )
    with pytest.raises(ValueError, match="ECONOMIC"):
        factorial_perturbations(
            {"leader_mature_score": 0.72},
            {"commission_rate": [0.0002]},
        )


def test_economic_override_is_validated_as_a_real_system_config_change() -> None:
    assert validate_shared_config({"leader_mature_score": 0.73}) == {"leader_mature_score": 0.73}
    with pytest.raises(ValueError, match="invalid ECONOMIC"):
        validate_shared_config({"leader_mature_score": 1.1})


def _search_observation(pool: str, window: str) -> ReplayObservation:
    start, end = AI_ERA_WINDOWS[window]
    return ReplayObservation(
        universe=pool,
        window=window,
        start=start,
        end=end,
        final_wealth=1.0,
        max_drawdown=0.0,
        annual_turnover=0.0,
        account_orders=0,
    )


@pytest.mark.parametrize(
    ("grid", "message"),
    [
        ({"leader_mature_score": [True]}, "leader_mature_score requires float"),
        ({"leader_cycle_confirm_days": [3.5]}, "leader_cycle_confirm_days requires int"),
        ({"conviction_weighting_enabled": [1]}, "conviction_weighting_enabled requires bool"),
        ({"conviction_weighting_enabled": [0.0]}, "conviction_weighting_enabled requires bool"),
    ],
)
def test_candidate_search_rejects_type_confused_values_before_runner(
    grid: dict[str, list[Any]],
    message: str,
) -> None:
    calls = 0

    def runner(_config: dict[str, object], pool: str, window: str) -> ReplayObservation:
        nonlocal calls
        calls += 1
        return _search_observation(pool, window)

    with pytest.raises(ValueError, match=message):
        search_candidates(
            parameter_grid=grid,
            pools=("a",),
            windows=("h1_2023",),
            runner=runner,  # type: ignore[arg-type]
        )
    assert calls == 0


def test_candidate_search_normalizes_valid_float_boundaries() -> None:
    observed: list[dict[str, object]] = []

    def runner(config: dict[str, object], pool: str, window: str) -> ReplayObservation:
        observed.append(config)
        return _search_observation(pool, window)

    results = search_candidates(
        parameter_grid={"leader_mature_score": [0, 1]},
        pools=("a",),
        windows=("h1_2023",),
        runner=runner,  # type: ignore[arg-type]
    )

    assert len(results) == 2
    assert {config["leader_mature_score"] for config in observed} == {0.0, 1.0}
    assert all(type(config["leader_mature_score"]) is float for config in observed)


@pytest.mark.parametrize(
    ("base", "message"),
    [
        ({"leader_mature_score": True}, "leader_mature_score requires float"),
        ({"leader_cycle_confirm_days": 3.5}, "leader_cycle_confirm_days requires int"),
    ],
)
def test_one_at_a_time_stress_rejects_type_confused_values(
    base: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        one_at_a_time_perturbations(base)


def test_factorial_stress_requires_real_boolean_values() -> None:
    with pytest.raises(ValueError, match="conviction_weighting_enabled requires bool"):
        factorial_perturbations(
            {"leader_mature_score": 0.72},
            {"conviction_weighting_enabled": [1]},
        )


def test_parameter_stress_normalizes_integral_ints_and_preserves_bools() -> None:
    integer_cases = one_at_a_time_perturbations(
        {"leader_cycle_confirm_days": 3.0},
        relative_deltas=(0.0,),
    )
    assert integer_cases[0].config()["leader_cycle_confirm_days"] == 3
    assert type(integer_cases[0].config()["leader_cycle_confirm_days"]) is int

    boolean_cases = factorial_perturbations(
        {"leader_mature_score": 0.72},
        {"conviction_weighting_enabled": [False, True]},
    )
    assert {case.config()["conviction_weighting_enabled"] for case in boolean_cases} == {
        False,
        True,
    }
    assert all(
        type(case.config()["conviction_weighting_enabled"]) is bool
        for case in boolean_cases
    )


@pytest.mark.parametrize("changes", REMOVED_COMPATIBILITY_OVERRIDES)
def test_removed_compatibility_overrides_fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        SystemConfig().override(**changes)
