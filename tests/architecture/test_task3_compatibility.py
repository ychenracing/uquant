from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast

import pytest

import uquant.config.model as config_model
from uquant.config import DEFAULT_CONFIG

from ._analysis import ROOT
from ._task3_baseline import (
    BASELINE_COMMIT,
    ISOLATED_VALIDATION_CASE_COUNT,
    METHOD_IDS,
    baseline_config_module,
    baseline_load_method_pickles,
    baseline_method_contract,
    capture_validation_contract,
    current_load_method_pickles,
    current_method_contract,
    exception_observation,
)

VALIDATION_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "task3_config_validation_contract.json"


def _validation_fixture() -> dict[str, object]:
    value = json.loads(VALIDATION_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_validation_fixture_is_reproducible_from_immutable_baseline_behavior() -> None:
    fixture = _validation_fixture()
    metadata = fixture["baseline"]
    assert isinstance(metadata, Mapping)
    assert metadata["baseline_commit"] == BASELINE_COMMIT

    assert capture_validation_contract(fixture) == fixture


def test_every_pair_of_isolated_invalid_stimuli_preserves_first_failure_order() -> None:
    fixture = _validation_fixture()
    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    isolated = cases[:ISOLATED_VALIDATION_CASE_COUNT]
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    comparisons = 0
    mismatches: list[dict[str, object]] = []

    for index, left in enumerate(isolated):
        left_changes = cast(Mapping[str, object], left["changes"])
        for right in isolated[index + 1 :]:
            right_changes = cast(Mapping[str, object], right["changes"])
            if not set(left_changes).isdisjoint(right_changes):
                continue
            changes = {**left_changes, **right_changes}
            comparisons += 1
            expected = exception_observation(baseline_default, changes)
            observed = exception_observation(DEFAULT_CONFIG, changes)
            if observed != expected and len(mismatches) < 20:
                mismatches.append(
                    {
                        "changes": changes,
                        "expected": expected,
                        "observed": observed,
                    }
                )

    metadata = cast(Mapping[str, object], fixture["baseline"])
    assert comparisons == metadata["pair_case_count"]
    assert mismatches == []


@pytest.mark.parametrize(
    ("left", "right", "changes"),
    (
        (
            "validate_market",
            "validate_execution",
            {"initial_cash": 0, "commission_rate": -0.1},
        ),
        (
            "validate_strategic_discovery",
            "validate_strategic_transition",
            {
                "strategic_cohort_size": 0,
                "strategic_long_cycle_min_ret20": -1.0,
            },
        ),
        (
            "validate_strategic_transition",
            "validate_strategic_lifecycle",
            {
                "strategic_long_cycle_min_ret20": -1.0,
                "strategic_dominant_max_weight": 1.01,
            },
        ),
        (
            "validate_strategic_lifecycle",
            "validate_risk",
            {
                "strategic_dominant_max_weight": 1.01,
                "risk_anchor_count": 0,
            },
        ),
    ),
)
def test_pairwise_guard_detects_demonstrated_validator_block_swaps(
    monkeypatch: pytest.MonkeyPatch,
    left: str,
    right: str,
    changes: dict[str, object],
) -> None:
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    expected = exception_observation(baseline_default, changes)
    assert exception_observation(DEFAULT_CONFIG, changes) == expected
    left_validator = getattr(config_model, left)
    right_validator = getattr(config_model, right)

    monkeypatch.setattr(config_model, left, right_validator)
    monkeypatch.setattr(config_model, right, left_validator)

    assert exception_observation(DEFAULT_CONFIG, changes) != expected


def test_all_authored_public_methods_retain_legacy_attribution() -> None:
    baseline = baseline_method_contract()
    current = current_method_contract()

    assert tuple(baseline) == METHOD_IDS
    assert tuple(current) == METHOD_IDS
    assert {
        method_id: (record["module"], record["qualname"])
        for method_id, record in current.items()
    } == {
        method_id: (record["module"], record["qualname"])
        for method_id, record in baseline.items()
    }


def test_authored_public_method_pickles_load_in_both_directions() -> None:
    baseline = baseline_method_contract()
    current = current_method_contract()
    baseline_pickles = {
        method_id: cast(str, record["pickle_b64"])
        for method_id, record in baseline.items()
    }
    current_pickles = {
        method_id: cast(str, record["pickle_b64"])
        for method_id, record in current.items()
    }

    assert current_pickles == baseline_pickles
    assert all(
        cast(bool, result["ok"])
        for result in current_load_method_pickles(baseline_pickles).values()
    )
    assert all(
        cast(bool, result["ok"])
        for result in baseline_load_method_pickles(current_pickles).values()
    )
